"""黄金行情客户端：akshare 取上金所 Au99.99 实时与历史，失败降级国际金价换算。

上金所是国内积存金/账户金的定价锚，与招行报价一致性最好；
本机代理对国内站反而慢，这里让 sge.com.cn 走直连。
"""
import json
import os
import subprocess

# requests 尊重 NO_PROXY 环境变量，需在 akshare 发请求前生效
os.environ.setdefault("NO_PROXY", "*sge.com.cn")
os.environ["no_proxy"] = os.environ.get("NO_PROXY", "")

import akshare as ak  # noqa: E402

SYMBOL = "Au99.99"


def realtime_price():
    """上金所实时行情，返回 (价格元/克, 更新时间str)。盘中延迟行情也够用。"""
    df = ak.spot_quotations_sge()
    df = df[df["品种"] == SYMBOL]
    row = df.iloc[-1]
    price = float(row["现价"])
    if price <= 0:  # 休市时末行可能为 0，回退到最后一个有效价
        valid = df[df["现价"].astype(float) > 0]
        row = valid.iloc[-1]
        price = float(row["现价"])
    return price, f"{row['更新时间']}"


def history(days=250):
    """Au99.99 日线历史（收盘用 close 列），返回 list[dict] 按日期升序。"""
    df = ak.spot_hist_sge(symbol=SYMBOL)
    df = df.tail(days)
    return [
        {"date": str(r["date"]), "open": float(r["open"]), "close": float(r["close"]),
         "low": float(r["low"]), "high": float(r["high"])}
        for _, r in df.iterrows()
    ]


def fallback_price():
    """降级通道：国际金价 × 汇率 换算人民币克价（复用现有 gold.py 的数据源）。"""
    usd = json.loads(subprocess.run(
        ["curl", "-sf", "-m", "12", "https://api.gold-api.com/price/XAU"],
        capture_output=True, text=True, check=True).stdout)["price"]
    fx = json.loads(subprocess.run(
        ["curl", "-sf", "-m", "12", "https://open.er-api.com/v6/latest/USD"],
        capture_output=True, text=True, check=True).stdout)["rates"]["CNY"]
    return round(usd / 31.1034768 * fx, 2), "国际金价换算(降级)"


def get_price():
    """实时价优先，SGE 拿不到就降级，绝不裸奔。"""
    try:
        price, ts = realtime_price()
        return {"price": price, "ts": ts, "source": "SGE Au99.99 实时"}
    except Exception:
        p, src = fallback_price()
        return {"price": p, "ts": "N/A", "source": src}
