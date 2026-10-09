"""策略角色：每个角色一套透明规则，模拟不同性格的散户在 5 万资金下的操作。

decide(series, acct) 返回 (动作, 参数, 理由) 或 None（按兵不动）：
  ("buy", 金额元, 理由)   ("sell", 卖出仓位比例0~1, 理由)
series: 截至"今天"的收盘价序列（含当天现价）；acct: 模拟账户（cash/grams）。
"""


def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


class Persona:
    name = ""
    emoji = ""
    style = ""
    desc = ""

    def decide(self, series, acct):
        raise NotImplementedError


class DcaGrandpa(Persona):
    """定投爷爷：每 5 个交易日定额买入 1000 元，永不卖出。"""
    name, emoji, style = "定投爷爷", "🧓", "长期定额"
    desc = "不看盘不择时，每 5 个交易日无脑买 1000 元，打死不卖"
    PER_DAYS, AMOUNT = 5, 1000.0

    def __init__(self):
        self.day_count = 0

    def decide(self, series, acct):
        self.day_count += 1
        if self.day_count % self.PER_DAYS == 0 and acct.cash >= self.AMOUNT:
            return ("buy", self.AMOUNT, f"第{self.day_count}个交易日，定投日雷打不动")
        return None


class TrendWolf(Persona):
    """追趋势狼：MA5 上穿 MA20 金叉买一半现金，下穿死叉卖一半仓位。"""
    name, emoji, style = "追趋势狼", "🐺", "均线动量"
    desc = "MA5 上穿 MA20 追涨买半仓，死叉立刻砍一半，破位认输"

    def _cross(self, series):
        m5_now, m20_now = ma(series, 5), ma(series, 20)
        m5_prev, m20_prev = ma(series[:-1], 5), ma(series[:-1], 20)
        if None in (m5_now, m20_now, m5_prev, m20_prev):
            return None
        if m5_prev <= m20_prev and m5_now > m20_now:
            return "golden"
        if m5_prev >= m20_prev and m5_now < m20_now:
            return "death"
        return None

    def decide(self, series, acct):
        cross = self._cross(series)
        price = series[-1]
        if cross == "golden" and acct.cash > 1000:
            amt = acct.cash * 0.5
            return ("buy", amt, f"MA5({ma(series,5):.1f}) 金叉上穿 MA20({ma(series,20):.1f})，追趋势买 {amt:.0f} 元")
        if cross == "death" and acct.grams > 0:
            return ("sell", 0.5, f"MA5 死叉跌破 MA20，趋势转坏，先卖一半仓位")
        return None


class ReversionFox(Persona):
    """捡漏狐：现价低于 MA20 超过 3% 分批买，高于 3% 分批卖。"""
    name, emoji, style = "捡漏狐", "🦊", "均值回归"
    desc = "只捡偏离 20 日均线 3% 以上的便宜货，涨过头 3% 就落袋一部分"

    BAND = 0.03

    def decide(self, series, acct):
        base = ma(series, 20)
        if base is None:
            return None
        dev = (series[-1] - base) / base
        if dev <= -self.BAND and acct.cash > 1000:
            amt = acct.cash * 0.25
            return ("buy", amt, f"现价 {series[-1]:.1f} 低于 MA20({base:.1f}) {dev*100:.1f}%，超跌捡 {amt:.0f} 元")
        if dev >= self.BAND and acct.grams > 0:
            return ("sell", 1 / 3, f"现价高于 MA20 {dev*100:.1f}%，涨过头，落袋 1/3 仓位")
        return None


class GridSpider(Persona):
    """网格蛛：以上次成交价为锚，涨 4% 卖一档、跌 4% 买一档，机械执行。"""
    name, emoji, style = "网格蛛", "🕸", "区间网格"
    desc = "4% 步长网格：涨一档卖 1/4，跌一档买 1/4 现金，震荡市提款机"

    STEP = 0.04
    MIN_CASH = 2000.0

    def __init__(self):
        self.anchor = None

    def decide(self, series, acct):
        price = series[-1]
        if self.anchor is None:
            self.anchor = price
            if acct.grams == 0 and acct.cash > self.MIN_CASH:
                return ("buy", acct.cash * 0.25, f"开网：以 {price:.1f} 为锚价建首档底仓")
            return None
        if price >= self.anchor * (1 + self.STEP):
            self.anchor = price
            if acct.grams > 0:
                return ("sell", 0.25, f"较锚价涨超 {self.STEP*100:.0f}%，卖 1/4 仓位，锚上移至 {price:.1f}")
        elif price <= self.anchor * (1 - self.STEP):
            self.anchor = price
            if acct.cash > self.MIN_CASH:
                amt = acct.cash * 0.25
                return ("buy", amt, f"较锚价跌超 {self.STEP*100:.0f}%，买 {amt:.0f} 元，锚下移至 {price:.1f}")
        return None


ALL_PERSONAS = [DcaGrandpa, TrendWolf, ReversionFox, GridSpider]
