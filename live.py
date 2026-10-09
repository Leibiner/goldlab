"""连续模拟盘：角色状态跨运行持久化，按上金所分钟行情在"条件首次成立时刻"成交。

与回测的关系：首次 step 用 250 日日线回测给角色建仓（bootstrap，截至昨天），
之后每次运行只处理上次之后新增的分钟线，成交进永久账本，永不重算。

触发节奏：
  🧓 定投爷爷  每个交易日第一次见到行情 → 定投一笔（每天恰好一次）
  🐺 追趋势狼  盘中每分钟算一遍"今日收盘=现价"的假想K线，金叉/死叉首次成立即成交，当日不重复
  🦊 捡漏狐    同上，偏离 MA20 ±3% 首次成立即成交，当日一次
  🕸 网格蛛    每分钟盯锚价，±4% 立即成交一档（双向、当日可多次，锚价更新天然限频）

用法:
  python live.py step                 # 推进模拟（timer 每小时/每5分钟调）
  python live.py publish [--no-position]  # 生成 docs/data.json
  python live.py status               # 人读账户现状
  python live.py reset                # 清状态，下次重新 bootstrap
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import akshare as ak  # noqa: E402
from client import get_price, history  # noqa: E402
from engine import INITIAL_CASH, WARMUP, SimAccount, max_drawdown  # noqa: E402
from personas import ALL_PERSONAS, DcaGrandpa  # noqa: E402

DIR = Path(__file__).parent
STATE_FILE = DIR / "live_state.json"
POS_FILE = DIR / "position.json"
TZ = timezone(timedelta(hours=8))
CLASSES = {c.__name__: c for c in ALL_PERSONAS}


def persona_key(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def get_minutes():
    """SGE 当前交易日全部分钟线 [(ts, price, date)]，过滤无效价。"""
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


def restore(cls_name, ps):
    """从持久化状态还原一个全新的角色实例 + 账户。"""
    p = CLASSES[cls_name]()
    if ps.get("anchor") is not None:
        p.anchor = ps["anchor"]
    if ps.get("day_count") is not None:
        p.day_count = ps["day_count"]
    acct = SimAccount()
    acct.cash, acct.grams, acct.trades = ps["cash"], ps["grams"], ps["trades"]
    return p, acct


def persist(ps, p, acct, ts, price):
    ps["cash"], ps["grams"], ps["trades"] = acct.cash, acct.grams, acct.trades
    if hasattr(p, "anchor"):
        ps["anchor"] = p.anchor
    if hasattr(p, "day_count"):
        ps["day_count"] = p.day_count
    if len(ps["trades"]) > 300:
        ps["trades"] = ps["trades"][-300:]
    curve = ps["live_curve"]
    # 每个交易日只留当日最后一个净值 + 最近 288 根分钟线，防膨胀
    if curve and curve[-1][0][:10] == ts[:10] and len(curve) > 288:
        curve.pop(-1)
    curve.append([ts, round(acct.equity(price), 2)])
    days = {}
    for t, v in curve:
        days[t[:10]] = v
    ps["daily_equity"] = [[d, v] for d, v in sorted(days.items())]


def bootstrap(hist_boot):
    closes = [h["close"] for h in hist_boot]
    personas = []
    for cls in ALL_PERSONAS:
        p, acct = cls(), SimAccount()
        curve = []
        for i in range(WARMUP, len(closes)):
            price = closes[i]
            sig = p.decide(closes[: i + 1], acct)
            if sig:
                action, param, reason = sig
                date = hist_boot[i]["date"]
                if action == "buy":
                    acct.buy(price, param, reason, date)
                else:
                    acct.sell(price, param, reason, date)
            curve.append(round(acct.equity(price), 2))
        personas.append({
            "key": persona_key(cls.__name__), "cls": cls.__name__,
            "cash": acct.cash, "grams": acct.grams, "trades": acct.trades,
            "anchor": getattr(p, "anchor", None), "day_count": getattr(p, "day_count", None),
            "acted_on": None, "seen_day": None,
            "boot_dates": [h["date"] for h in hist_boot[WARMUP:]],
            "boot_curve": curve, "max_dd_pct": round(max_drawdown(curve) * 100, 2),
            "live_curve": [], "daily_equity": [],
        })
    return {"version": 1, "bootstrapped": datetime.now(TZ).isoformat(),
            "last_ts": None, "last_date": hist_boot[-1]["date"], "price_daily": {},
            "personas": personas}


def step():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text())
        hist_boot = None
    else:
        hist_boot = [h for h in history(250) if h["date"] != today]
        st = bootstrap(hist_boot)
    hist_yday = hist_boot or [h for h in history(250) if h["date"] != today]
    closes_yday = [h["close"] for h in hist_yday]

    ticks = get_minutes()
    new = [t for t in ticks if st["last_ts"] is None or t[0] > st["last_ts"]]
    if st["last_ts"] is None and new:  # 首次只从最新一分钟开始，不吃历史分钟风暴
        new = new[-1:]
    fired = 0
    for ts, price, date in new:
        series = closes_yday + [price]
        for ps in st["personas"]:
            p, acct = restore(ps["cls"], ps)
            if ps["cls"] == DcaGrandpa.__name__:
                # 爷爷：每个交易日只评估一次（当天第一根分钟线），其余分钟只记净值
                if ps["seen_day"] == date:
                    persist(ps, p, acct, ts, price)
                    continue
                ps["seen_day"] = date
                sig = p.decide(series, acct)
            elif ps["cls"] == "GridSpider":
                sig = p.decide(series, acct)  # 网格：每根都盯
            else:
                if ps["acted_on"] == date:
                    sig = None
                else:
                    sig = p.decide(series, acct)
            if sig:
                action, param, reason = sig
                stamp = f"｜{ts[11:16]} 盘中成交"
                ok = (acct.buy(price, param, reason + stamp, date) if action == "buy"
                      else acct.sell(price, param, reason + stamp, date))
                if ok:
                    fired += 1
                    ps["acted_on"] = date
            persist(ps, p, acct, ts, price)
        st["price_daily"][date] = price  # 每个交易日最后一根分钟价 ≈ 当日收盘
        st["last_ts"] = ts
        st["last_date"] = date
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False))
    return st, fired, (ticks[-1] if ticks else None)


def build_payload(no_position=False):
    """推进一格模拟后，生成前端 data.json。dates = 日线日期(截至昨天) + live 新增日期。"""
    st, _, _ = step()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    hist = [h for h in history(250) if h["date"] != today]
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
        print("状态已清除，下次 step/publish 重新 bootstrap")
    elif args.cmd == "step":
        st, fired, tick = step()
        print(f"step ok: fired={fired} last_ts={st['last_ts']} last_tick={tick}")
    elif args.cmd == "publish":
        json.dump(build_payload(args.no_position), sys.stdout, ensure_ascii=False, indent=1)
    else:
        st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else step()[0]
        for ps in st["personas"]:
            print(f"{ps['key']:<14} cash={ps['cash']:>9.2f} grams={ps['grams']:>8.3f} "
                  f"trades={len(ps['trades']):>3} anchor={ps['anchor']} day={ps['day_count']}")
        print("last_ts:", st["last_ts"])


if __name__ == "__main__":
    main()
