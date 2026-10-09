"""v2 策略角色：每条都有仓位上限、止损/止盈、明确的"为什么买/为什么卖"。

对比 v1 的改动逻辑：
  爷爷全仓无卖  →  配置管家 目标仓位制 + 分批纠偏
  狼裸奔追趋势  →  风控趋势手 首仓 25% + 突破才加仓 + 6% 硬止损
  狐越跌越买    →  超跌反手狐 RSI+深度双条件才进场，8% 止损，过热止盈
  （无）        →  宏观策略师：联储/CPI/美债10Y 因子评分驱动，趋势未破才进场
  定投爷爷保留为对照组——每轮都跑，看"优化"到底值多少。

decide(series, acct, ctx) 契约同 v1；ctx = {"date", "index", "factors": (score, parts)}
"""
from personas import Persona, ma


def rsi14(closes):
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-15:], closes[-14:]):
        chg = b - a
        gains += max(chg, 0)
        losses += max(-chg, 0)
    if gains + losses == 0:
        return 50.0
    return gains / (gains + losses) * 100


def weight(acct, price):
    eq = acct.cash + acct.grams * price
    return acct.grams * price / eq if eq > 0 else 0.0


class MacroStrategist(Persona):
    """🧭 宏观策略师：因子评分定方向，趋势线定能不能买，8%硬止损。"""
    name, emoji, style = "宏观策略师", "🧭", "因子驱动"
    desc = "联储+美债10Y+CPI评分≥+1.5 且价格未破MA60才分三批进场；评分≤-1.5 或亏 8% 无条件清仓"
    TARGET_W = 0.6      # 满配仓位占净值比例
    STOP = 0.92         # 成本相对硬止损线
    TRAIL = 0.90        # 峰值相对移动止盈线：浮盈回吐 10% 无条件下车
    GAP_DAYS = 5        # 两笔加仓最小间隔（交易日）

    def __init__(self):
        self.entries = 0
        self.last_entry = -99
        self.hwm = 0.0  # 本轮持仓期最高价（trailing stop 锚）

    def decide(self, series, acct, ctx=None):
        price = series[-1]
        idx = ctx["index"] if ctx else len(series)
        if acct.grams > 0:
            self.hwm = max(self.hwm, price)
            stop_line = max(acct.avg_cost * self.STOP, self.hwm * self.TRAIL)
            if price < stop_line:
                self.entries = 0
                self.hwm = 0.0
                why = "跌破成本止损" if stop_line == acct.avg_cost * self.STOP else f"浮盈回吐：峰值{self.hwm:.0f}→{price:.0f}"
                return ("sell", 1.0, f"{why}，全部下车")
        score, parts = (ctx or {}).get("factors", (None, []))
        if score is None:
            return None
        trend_ok = ma(series, 60) is not None and price >= ma(series, 60)
        if score <= -1.5 and acct.grams > 0:
            self.entries = 0
            self.hwm = 0.0
            return ("sell", 1.0, f"宏观转空(评分{score:+g}): {'; '.join(parts[:3])}")
        if score >= 1.5 and acct.grams > 0 and self.entries < 3 \
                and idx - self.last_entry >= self.GAP_DAYS and weight(acct, price) < self.TARGET_W:
            self.entries += 1
            self.last_entry = idx
            amt = acct.cash * 0.35
            return ("buy", amt, f"宏观偏多(评分{score:+g})第{self.entries}批加仓: {'; '.join(parts[:2])}")
        if score >= 1.5 and acct.grams == 0 and trend_ok and self.entries < 3:
            self.entries = 1
            self.last_entry = idx
            amt = acct.cash * 0.35
            return ("buy", amt, f"宏观偏多(评分{score:+g})+价格站上MA60，首批建仓: {'; '.join(parts[:2])}")
        if score >= 1.5 and acct.grams == 0 and not trend_ok and self.entries == 0:
            return None  # 宏观利多但趋势破位：等，这是纪律不是犹豫
        return None


class RiskTrend(Persona):
    """🛡 风控趋势手：金叉只上 25% 底仓，创新高才加仓，94 线硬止损。"""
    name, emoji, style = "风控趋势手", "🛡", "趋势+风控"
    desc = "MA5金叉MA20先买1/4现金；站稳20日新高才加第二批；亏6%止损、死叉清仓"
    STOP = 0.94

    def __init__(self):
        self.added = False
        self.last_add = -99

    def decide(self, series, acct, ctx=None):
        price = series[-1]
        idx = ctx["index"] if ctx else len(series)
        m5, m20 = ma(series, 5), ma(series, 20)
        p5, p20 = ma(series[:-1], 5), ma(series[:-1], 20)
        if acct.grams > 0:
            if price < acct.avg_cost * self.STOP:
                self.added = False
                return ("sell", 1.0, f"硬止损：{price:.1f} 跌破均价{acct.avg_cost:.1f}的6%")
            if p5 and p20 and p5 >= p20 and m5 < m20:
                self.added = False
                return ("sell", 1.0, "MA5死叉MA20，趋势终结，清仓")
        if acct.grams > 0 and not self.added and price >= max(series[-21:-1]) \
                and idx - self.last_add > 10 and acct.cash > 1000:
            self.added = True
            self.last_add = idx
            return ("buy", acct.cash * 0.25, f"持仓盈利中突破20日新高{price:.1f}，金字塔加第二批")
        if acct.grams == 0 and p5 and p20 and p5 <= p20 and m5 > m20 and acct.cash > 1000:
            self.added = False
            amt = acct.cash * 0.25
            return ("buy", amt, f"MA5({m5:.1f})金叉MA20({m20:.1f})，首仓25%进场{amt:.0f}元")
        return None


class RsiDipHunter(Persona):
    """🎯 超跌反手狐：RSI<32 且比 MA60 低 10% 才接，接了必须带止损。"""
    name, emoji, style = "超跌反手狐", "🎯", "超跌反转"
    desc = "RSI14<32 且 现价<MA60×0.90 双条件才买；亏8%认错，RSI>68 或高于MA60的10%止盈"
    STOP = 0.92

    def decide(self, series, acct, ctx=None):
        price = series[-1]
        r = rsi14(series)
        m60 = ma(series, 60)
        if r is None or m60 is None:
            return None
        if acct.grams > 0:
            if price < acct.avg_cost * self.STOP:
                return ("sell", 1.0, f"抄底失败止损：{price:.1f} < 均价{acct.avg_cost:.1f}×0.92")
            if r > 68:
                return ("sell", 1.0, f"RSI{r:.0f}过热，止盈离场")
            if price > m60 * 1.10:
                return ("sell", 1.0, f"价格高于MA60达10%，反弹兑现")
            return None
        if r < 32 and price < m60 * 0.90 and acct.cash > 1000:
            amt = acct.cash * 0.30
            return ("buy", amt, f"RSI{r:.0f}<32 且低于MA60 {100*(1-price/m60):.1f}%，双条件超跌买{amt:.0f}元")
        return None


class Allocator(Persona):
    """🧘 配置管家：黄金目标占净值 50%，每月初检查，偏离 ±10 个百分点才动手。"""
    name, emoji, style = "配置管家", "🧘", "目标配置"
    desc = "目标仓位50%，每月检查一次，偏离超10个百分点分批纠偏(每次最多动现金的35%)，不预测只再平衡"
    TARGET = 0.5
    BAND = 0.10

    def __init__(self):
        self.last_month = None

    def decide(self, series, acct, ctx=None):
        date = (ctx or {}).get("date", "")
        month = date[:7]
        if not month or month == self.last_month:
            return None
        self.last_month = month
        price = series[-1]
        w = weight(acct, price)
        if w < self.TARGET - self.BAND and acct.cash > 1000:
            amt = min(acct.cash * 0.35, (self.TARGET * (acct.cash + acct.grams * price) - acct.grams * price))
            return ("buy", amt, f"月度检查：黄金仓位{w*100:.0f}%低于目标，买入{amt:.0f}元向50%纠偏")
        if w > self.TARGET + self.BAND and acct.grams > 0:
            need = (w - self.TARGET) * (acct.cash + acct.grams * price) / max(price, 1)
            frac = min(need / acct.grams, 0.35)
            return ("sell", frac, f"月度检查：仓位{w*100:.0f}%超配，卖出{frac*100:.0f}%仓位回落")
        return None


# 对照组沿用 v1 的无脑定投爷爷（from personas import DcaGrandpa 即可在注册表里混用）
ALL_V2 = [MacroStrategist, RiskTrend, RsiDipHunter, Allocator]
