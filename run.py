"""goldlab CLI：回测 / 实时信号 / JSON 输出（供前端）。

用法（在 gold 目录的 venv 里）:
  .venv/bin/python goldlab/run.py backtest --days 250
  .venv/bin/python goldlab/run.py live
  .venv/bin/python goldlab/run.py json > web/data.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from client import get_price, history  # noqa: E402
from engine import INITIAL_CASH, WARMUP, Simulator  # noqa: E402
from personas import ALL_PERSONAS  # noqa: E402

# 实盘参照从本地 position.json 读取（该文件 .gitignore，仓库/前端不会泄露成本价）
# 格式: {"grams": 15.0, "cost": 922.0}
POS_FILE = Path(__file__).parent / "position.json"
REAL_POSITION = json.loads(POS_FILE.read_text()) if POS_FILE.exists() else None


def cmd_backtest(days):
    hist = history(days)
    sim = Simulator(ALL_PERSONAS)
    results = sim.run_history(hist)
    print(f"回测区间: {hist[0]['date']} → {hist[-1]['date']}（{len(hist)} 个交易日，"
          f"SGE Au99.99，初始资金 {INITIAL_CASH:.0f} 元）\n")
    hdr = f"{'角色':<7}{'风格':<7}{'期末资产':>10}{'收益率':>8}{'最大回撤':>8}{'交易次数':>7}  最后一次动作"
    print(hdr)
    print("-" * 76)
    for r in results:
        p = r["persona"]
        last = f"{r['last_action'][0]} @{r['last_action'][2][:18]}" if r["last_action"] else "空仓观望"
        print(f"{p.emoji}{p.name:<6}{p.style:<8}{r['account'].equity(r['final_price']):>10.0f}"
              f"{r['return_pct']:>7.2f}%{r['max_dd_pct']:>7.2f}%{r['trade_count']:>7}  {last}")
    gold_only = (hist[-1]["close"] / hist[0]["close"] - 1) * 100
    print(f"\n基准：同期金价本身涨跌 {gold_only:+.2f}%（拿住不动的结局）")


def cmd_live():
    q = get_price()
    hist = history(250)
    sim = Simulator(ALL_PERSONAS)
    results, _ = sim.live_check(hist, q["price"])
    print(f"📍 {q['source']}: {q['price']} 元/克（{q['ts']}）")
    if REAL_POSITION:
        rp = REAL_POSITION
        print(f"👤 你的实盘: {rp['grams']} 克 @{rp['cost']} → 现值 {rp['grams']*q['price']:.0f} 元，"
              f"{(q['price']/rp['cost']-1)*100:+.2f}%")
    print()
    print(f"{'角色':<7}{'模拟资产':>10}{'收益':>8}  此刻指令")
    print("-" * 68)
    for r in results:
        p = r["persona"]
        sig = r["live_signal"]
        act = f"🔴 {sig['action']}: {sig['estimate']}｜{sig['reason']}" if sig else "⚪ 按兵不动"
        print(f"{p.emoji}{p.name:<6}{r['live_equity']:>10.0f}{r['return_pct']:>7.2f}%  {act}")


def cmd_json(no_position=False):
    q = get_price()
    hist = history(250)
    sim = Simulator(ALL_PERSONAS)
    results, used_hist = sim.live_check(hist, q["price"])
    payload = {
        "updated": q["ts"], "source": q["source"], "price": q["price"],
        "real_position": ({**REAL_POSITION, "value": round(REAL_POSITION["grams"] * q["price"], 2),
                           "pnl_pct": round((q["price"] / REAL_POSITION["cost"] - 1) * 100, 2)}
                          if (REAL_POSITION and not no_position) else None),
        "history": [{"date": h["date"], "close": h["close"]} for h in used_hist[WARMUP:]],
        "personas": [
            {
                "name": r["persona"].name, "emoji": r["persona"].emoji,
                "style": r["persona"].style, "desc": r["persona"].desc,
                "equity": r["live_equity"], "return_pct": round(r["return_pct"], 2),
                "max_dd_pct": round(r["max_dd_pct"], 2), "trade_count": r["trade_count"],
                "cash": round(r["account"].cash, 2), "grams": round(r["account"].grams, 3),
                "trades": r["account"].trades[-12:],  # 最近 12 笔
                "curve": r["curve"],
                "live_signal": r["live_signal"],
            } for r in results
        ],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description="goldlab 模拟盘")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bt = sub.add_parser("backtest"); bt.add_argument("--days", type=int, default=250)
    sub.add_parser("live")
    js = sub.add_parser("json")
    js.add_argument("--no-position", action="store_true", help="发布模式：剔除实盘成本")
    args = ap.parse_args()
    if args.cmd == "backtest":
        cmd_backtest(args.days)
    elif args.cmd == "live":
        cmd_live()
    else:
        cmd_json(args.no_position)


if __name__ == "__main__":
    main()
