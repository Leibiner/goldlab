"""连续模拟盘 v2：因子+风控角色，状态跨运行持久化，按上金所分钟线即时成交。

白手起家（version 3）：每角色 5 万现金 0 持仓，不回放历史建仓。
350 日历史价格只作为均线/RSI/宏观因子的输入；仓位轨迹从第一次实触发开始。
每次运行只处理新增分钟线——SGE 只在真实交易时段产生新分钟，休市自动空转。

触发节奏：
  🧭宏观策略师/🛡趋势手/🎯反手狐/🧘配置管家  每根分钟线评估——v2 规则天然幂等
      （买分支要求空仓、卖分支要求持仓、月度/批次数内部门控），不会当日反复打单
  🧓定投爷爷(对照组)  每个交易日第一根分钟线定投一次

用法: step / publish [--no-position] / status / reset
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from client import get_price, history  # noqa: E402
import factors  # noqa: E402
from engine import INITIAL_CASH, WARMUP, SimAccount, max_drawdown  # noqa: E402
from personas import DcaGrandpa  # noqa: E402
from personas2 import ALL_V2  # noqa: E402

DIR = Path(__file__).parent
STATE_FILE = DIR / "live_state.json"
POS_FILE = DIR / "position.json"
TZ = timezone(timedelta(hours=8))

ROSTER = ALL_V2 + [DcaGrandpa]          # v2 四角色 + 爷爷对照组
CLASSES = {c.__name__: c for c in ROSTER}
BOOT_DAYS = 350


def persona_key(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def get_minutes():
    """SGE 当前交易日全部分钟线 [(ts, price, date)]。"""
    import akshare as ak
    df = ak.spot_quotations_sge()
    df = df[df["品种"] == "Au99.99"]
    ticks = []
    for _, r in df.iterrows():
        price = float(r["现价"])
        if price <= 0:
            continue
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", str(r["更新时间"]))
        if not m:
            continue
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        ticks.append((f"{date} {r['时间']}", price, date))
    return ticks


def _simple(d):
    return {k: v for k, v in d.items() if isinstance(v, (int, float, str, bool, type(None)))}


def restore(ps):
    p = CLASSES[ps["cls"]]()
    p.__dict__.update(ps.get("pstate", {}))
    acct = SimAccount()
    acct.cash, acct.grams, acct.trades = ps["cash"], ps["grams"], ps["trades"]
    acct.avg_cost = ps.get("avg_cost", 0.0)
    return p, acct


def persist(ps, p, acct, ts, price):
    ps["cash"], ps["grams"], ps["trades"] = acct.cash, acct.grams, acct.trades
    ps["avg_cost"] = acct.avg_cost
    ps["pstate"] = _simple(p.__dict__)
    if len(ps["trades"]) > 300:
        ps["trades"] = ps["trades"][-300:]
    curve = ps["live_curve"]
    if curve and curve[-1][0][:10] == ts[:10] and len(curve) > 288:
        curve.pop(-1)
    curve.append([ts, round(acct.equity(price), 2)])
    days = {d: v for d, v in ps["daily_equity"]}
    days[ts[:10]] = round(acct.equity(price), 2)
    ps["daily_equity"] = sorted(days.items())


def fresh_accounts():
    """白手起家：每角色 5 万现金 0 持仓，不回放历史。
    历史价格只作为指标输入（算均线/RSI 用），仓位轨迹从第一次实触发开始。"""
    personas = []
    for cls in ROSTER:
        p = cls()
        personas.append({
            "key": persona_key(cls.__name__), "cls": cls.__name__,
            "cash": INITIAL_CASH, "grams": 0.0, "avg_cost": 0.0,
            "trades": [], "pstate": _simple(p.__dict__),
            "seen_day": None,
            "boot_dates": [], "boot_curve": [], "live_curve": [], "daily_equity": [],
        })
    return {"version": 3, "started": datetime.now(TZ).isoformat(),
            "last_ts": None, "last_date": None,
            "day_index": 0, "price_daily": {}, "personas": personas}


def in_trade_window(ts):
    """招行积存金可交易窗口（银行官方公告截图 2026-10-09）：
      本交易日 15:30 后 ~ 下一交易日 9:10 停止交易，【夜盘 20:00-02:30 除外】
    即：9:10-15:30（日盘，午休无行情自然无 tick）+ 20:00-02:30（夜盘，含凌晨 00:00-02:30）。
    15:30-20:00、02:30-9:10 两个闭市窗口内绝不成交。"""
    hm = ts[11:16]
    return ("09:10" <= hm <= "15:30") or hm >= "20:00" or hm <= "02:30"


def step():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    fdata = factors.load()
    if STATE_FILE.exists() and json.loads(STATE_FILE.read_text()).get("version") == 3:
        st = json.loads(STATE_FILE.read_text())
    else:
        st = fresh_accounts()
    hist_yday = [h for h in history(BOOT_DAYS) if h["date"] != today]
    closes_yday = [h["close"] for h in hist_yday]

    ticks = get_minutes()
    new = [t for t in ticks if (st["last_ts"] is None or t[0] > st["last_ts"]) and in_trade_window(t[0])]
    if st["last_ts"] is None and new:
        new = new[-1:]  # 冷启动只吃最新一分钟，不吃历史分钟风暴
    fired = 0
    for ts, price, date in new:
        if date != st["last_date"]:
            st["day_index"] += 1
            st["last_date"] = date
        ctx = {"date": date, "index": st["day_index"],
               "factors": factors.score(date, data=fdata)}
        series = closes_yday + [price]
        for ps in st["personas"]:
            p, acct = restore(ps)
            if ps["cls"] == DcaGrandpa.__name__:
                if ps.get("seen_day") == date:
                    persist(ps, p, acct, ts, price)
                    continue
                ps["seen_day"] = date
            sig = p.decide(series, acct, ctx)
            if sig:
                action, param, reason = sig
                stamp = f"｜{ts[11:16]} 盘中成交"
                ok = (acct.buy(price, param, reason + stamp, date) if action == "buy"
                      else acct.sell(price, param, reason + stamp, date))
                if ok:
                    fired += 1
            persist(ps, p, acct, ts, price)
        st["price_daily"][date] = price
        st["last_ts"] = ts
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False))
    return st, fired, (ticks[-1] if ticks else None)


def build_payload(no_position=False):
    st, _, _ = step()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    hist = [h for h in history(BOOT_DAYS) if h["date"] != today]
    tick = get_minutes()[-1]
    price, ts_disp = tick[1], tick[0]
    day_dates = [h["date"] for h in hist[WARMUP:]]
    live_days = sorted(d for d in st["price_daily"] if d > day_dates[-1])
    dates = day_dates + live_days
    closes = {h["date"]: h["close"] for h in hist}
    personas = []
    for ps in st["personas"]:
        by_date = dict(zip(ps["boot_dates"], ps["boot_curve"]))
        by_date.update(dict(ps["daily_equity"]))
        curve, last = [], None
        for d in dates:
            last = by_date.get(d, last)
            curve.append(last)
        cls = CLASSES[ps["cls"]]
        final_eq = round(ps["cash"] + ps["grams"] * price, 2)
        personas.append({
            "name": cls.name, "emoji": cls.emoji, "style": cls.style, "desc": cls.desc,
            "equity": final_eq,
            "return_pct": round((final_eq / INITIAL_CASH - 1) * 100, 2),
            "max_dd_pct": round(max_drawdown([v for v in curve if v is not None]) * 100, 2),
            "trade_count": len(ps["trades"]),
            "cash": round(ps["cash"], 2), "grams": round(ps["grams"], 3),
            "trades": ps["trades"][-12:], "curve": curve,
            "live_mode": True,
            "last_action": ps["trades"][-1] if ps["trades"] else None,
            "live_signal": None,
        })
    pos = json.loads(POS_FILE.read_text()) if (POS_FILE.exists() and not no_position) else None
    return {
        "updated": f"{ts_disp}（最新分钟）", "source": "SGE Au99.99 分钟线 · 连续模拟", "price": price,
        "real_position": ({**pos, "value": round(pos["grams"] * price, 2),
                           "pnl_pct": round((price / pos["cost"] - 1) * 100, 2)} if pos else None),
        "history": [{"date": d, "close": closes.get(d, st["price_daily"].get(d, price))} for d in dates],
        "personas": personas,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["step", "publish", "status", "reset"])
    ap.add_argument("--no-position", action="store_true")
    args = ap.parse_args()
    if args.cmd == "reset":
        STATE_FILE.unlink(missing_ok=True)
        print("状态已清除，下次 step/publish 白手起家（v3：5万现金0持仓，等真实行情触发）")
    elif args.cmd == "step":
        st, fired, tick = step()
        print(f"step ok: fired={fired} last_ts={st['last_ts']}")
    elif args.cmd == "publish":
        json.dump(build_payload(args.no_position), sys.stdout, ensure_ascii=False, indent=1)
    else:
        st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else step()[0]
        price = st["price_daily"].get(max(st["price_daily"]), 0)
        for ps in st["personas"]:
            eq = ps["cash"] + ps["grams"] * price
            cls = CLASSES[ps["cls"]]
            print(f"{cls.emoji}{cls.name:<7} 净值{eq:>9.0f} ({(eq/INITIAL_CASH-1)*100:+.2f}%) "
                  f"持仓{ps['grams']:>7.2f}g 均价{ps.get('avg_cost', 0):>6.1f} 现金{ps['cash']:>8.0f} "
                  f"笔数{len(ps['trades']):>2}")
        print("last_ts:", st["last_ts"])


if __name__ == "__main__":
    main()
