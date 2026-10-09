"""宏观因子库：只收录本机实测可通的 akshare 接口（SPDR/非农/失业率超时，弃用）。

因子与黄金的传导逻辑（写进评分，不装玄学）：
  美债10Y  —— 实际利率之锚。20日趋势 ↑ 压制金价（持金机会成本），↓ 抬升金价
  联储利率 —— 政策周期。最近一次有效决议：降息 +1 / 加息 -1 / 按兵不动 0
  美国CPI  —— 通胀。用【发布日期】对齐避免前视；环比回升 → 宽松预期+抗通胀需求 +0.5
  LLM 日评 —— 可选 macro_view.json {"score": -2..2, "reason": "..."}，早报系统写入

cache: factors_cache.json，6 小时内的重复调用不再打网络。
"""
import bisect
import json
import time
from pathlib import Path

DIR = Path(__file__).parent
CACHE = DIR / "factors_cache.json"
MAX_AGE = 6 * 3600


def _fetch():
    import akshare as ak
    import pandas as pd

    b = ak.bond_zh_us_rate(start_date="2024-01-01")
    ust = [[str(r["日期"]), float(r["美国国债收益率10年"])]
           for _, r in b.iterrows() if pd.notna(r["美国国债收益率10年"])]

    f = ak.macro_bank_usa_interest_rate()
    fed = [[str(r["日期"]), float(r["今值"])] for _, r in f.iterrows()
           if pd.notna(r["今值"]) and r["今值"] > 0]

    c = ak.macro_usa_cpi_yoy()
    cpi = [[str(r["发布日期"]), float(r["现值"])] for _, r in c.iterrows()
           if pd.notna(r["现值"]) and pd.notna(r["发布日期"])]
    return {"ust": sorted(ust), "fed": sorted(fed), "cpi": sorted(cpi),
            "fetched": int(time.time())}


def load(force=False):
    if not force and CACHE.exists():
        d = json.loads(CACHE.read_text())
        if time.time() - d.get("fetched", 0) < MAX_AGE:
            return d
    try:
        d = _fetch()
    except Exception as e:  # 网络挂了用旧缓存，没缓存就空集（角色会因缺因子而不动作）
        if CACHE.exists():
            return json.loads(CACHE.read_text())
        raise RuntimeError(f"宏观因子获取失败且无缓存: {e}")
    CACHE.write_text(json.dumps(d, ensure_ascii=False))
    return d


def _as_of(pairs, date):
    """[[iso_date, value]...] 升序，取 ≤ date 的最后一条。"""
    i = bisect.bisect_right([p[0] for p in pairs], date) - 1
    return i


def score(date, data=None, view=None):
    """返回 (综合评分 -3..+3, 分项说明 list)。date 为 YYYY-MM-DD。"""
    d = data or load()
    parts, total = [], 0.0

    i = _as_of(d["ust"], date)
    if i >= 20:
        cur, ago = d["ust"][i][1], d["ust"][i - 20][1]
        diff = cur - ago
        if diff <= -0.15:
            total += 1; parts.append(f"美债10Y 20日下行{abs(diff):.2f}({cur:.2f}%) → 利多")
        elif diff >= 0.15:
            total -= 1; parts.append(f"美债10Y 20日上行{diff:.2f}({cur:.2f}%) → 利空")
        else:
            parts.append(f"美债10Y 20日走平({cur:.2f}%)")

    j = _as_of(d["fed"], date)
    if j >= 1:
        now_v, prev_v = d["fed"][j][1], d["fed"][j - 1][1]
        if now_v < prev_v:
            total += 1; parts.append(f"联储降息{prev_v}→{now_v} → 利多")
        elif now_v > prev_v:
            total -= 1; parts.append(f"联储加息{prev_v}→{now_v} → 利空")
        else:
            parts.append(f"联储按兵不动({now_v})")

    k = _as_of(d["cpi"], date)  # cpi 已按发布日期排序
    if k >= 1:
        cur = d["cpi"][k][1]
        trend_up = cur > d["cpi"][k - 1][1]
        if trend_up:
            total += 0.5; parts.append(f"CPI回升至{cur}%(发布{d['cpi'][k][0]}) → 偏多")
        elif cur < d["cpi"][k - 1][1]:
            total -= 0.5; parts.append(f"CPI回落至{cur}%(发布{d['cpi'][k][0]}) → 偏空")
        else:
            parts.append(f"CPI持平{cur}%")

    if view is None:
        vf = DIR / "macro_view.json"
        if vf.exists():
            try:
                view = json.loads(vf.read_text())
            except Exception:
                view = None
    if view and isinstance(view.get("score"), (int, float)):
        s = max(-2, min(2, view["score"]))
        total += s
        parts.append(f"LLM日评 {s:+g}: {view.get('reason', '')[:40]}")

    return max(-3, min(3, round(total, 1))), parts
