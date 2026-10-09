"""模拟盘引擎：5 万现金开户，按积存金方式交易（买卖各有点差），逐日推进。"""
import copy
from datetime import datetime

INITIAL_CASH = 50_000.0
SPREAD_PER_GRAM = 0.5  # 双边各 0.5 元/克的点差近似（招行积存金口径）
WARMUP = 20            # MA20 需要暖机天数


class SimAccount:
    def __init__(self):
        self.cash = INITIAL_CASH
        self.grams = 0.0
        self.trades = []

    def buy(self, price, amount, reason, date):
        unit = price + SPREAD_PER_GRAM
        if amount > self.cash or unit <= 0:
            return False
        grams = amount / unit
        self.cash -= amount
        self.grams += grams
        self.trades.append({"date": date, "side": "buy", "price": round(price, 2),
                            "amount": round(amount, 2), "grams": round(grams, 3), "reason": reason})
        return True

    def sell(self, price, fraction, reason, date):
        unit = price - SPREAD_PER_GRAM
        grams = self.grams * min(max(fraction, 0.0), 1.0)
        if grams <= 0 or unit <= 0:
            return False
        cash = grams * unit
        self.grams -= grams
        self.cash += cash
        self.trades.append({"date": date, "side": "sell", "price": round(price, 2),
                            "amount": round(cash, 2), "grams": round(grams, 3), "reason": reason})
        return True

    def equity(self, price):
        return self.cash + self.grams * price


def max_drawdown(curve):
    peak, worst = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


class Simulator:
    """驱动一组角色按同一行情序列走盘。"""

    def __init__(self, persona_classes):
        self.personas = [(cls(), SimAccount()) for cls in persona_classes]

    def _step(self, persona, acct, series, date):
        sig = persona.decide(series, acct)
        if not sig:
            return None
        action, param, reason = sig
        price = series[-1]
        ok = acct.buy(price, param, reason, date) if action == "buy" else acct.sell(price, param, reason, date)
        return (action, param, reason) if ok else None

    def run_history(self, history):
        """历史回放。history: client.history() 的 list[dict]。返回每角色结果。"""
        closes = [h["close"] for h in history]
        curves = [[] for _ in self.personas]
        decisions = [[] for _ in self.personas]
        for i in range(WARMUP, len(closes)):
            series = closes[: i + 1]
            date = history[i]["date"]
            for k, (p, acct) in enumerate(self.personas):
                decisions[k].append(self._step(p, acct, series, date))
                curves[k].append(round(acct.equity(closes[i]), 2))
        results = []
        for (p, acct), curve, decs in zip(self.personas, curves, decisions):
            results.append({
                "persona": p, "account": acct, "curve": curve,
                "final_price": closes[-1],
                "return_pct": (acct.equity(closes[-1]) / INITIAL_CASH - 1) * 100,
                "max_dd_pct": max_drawdown(curve) * 100 if curve else 0.0,
                "trade_count": len(acct.trades),
                "last_action": next((d for d in reversed(decs) if d), None),
            })
        return results

    def live_check(self, history, live_price):
        """先回放历史，再用今日实时价问一遍每个角色：现在动手吗？"""
        hist = list(history)
        today = datetime.now().strftime("%Y-%m-%d")
        if hist and hist[-1]["date"] == today:  # 当天未收盘的残行剔除，用实时价代替
            hist.pop()
        results = self.run_history(hist)
        closes = [h["close"] for h in hist] + [live_price]
        for r in results:
            p, acct = r["persona"], r["account"]
            sig = p.decide(closes, acct)
            if sig:
                action, param, reason = sig
                price = closes[-1]
                est = (f"买 {param:.0f} 元 ≈ {param/(price+SPREAD_PER_GRAM):.2f} 克"
                       if action == "buy" else f"卖 {param*100:.0f}% 仓位 ≈ {acct.grams*param:.2f} 克")
                r["live_signal"] = {"action": action, "reason": reason, "estimate": est}
            else:
                r["live_signal"] = None
            r["live_equity"] = round(acct.equity(live_price), 2)
            r["return_pct"] = (r["live_equity"] / INITIAL_CASH - 1) * 100
        return results, hist  # hist 是剔除今日残行后的历史，与各角色 curve 对齐
