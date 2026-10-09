# goldlab · 黄金策略角色模拟盘

用 [akshare](https://github.com/akfamily/akshare) 的上金所 Au99.99 数据，给 4 个性格迥异的模拟角色各发 5 万元，看它们在黄金里的真实表现，并给出"此刻"的动作建议。

- 数据源：上海黄金交易所 Au99.99（实时 + 250 交易日历史），抓取失败自动降级国际金价换算
- 交易模型：模拟招行积存金，买卖各 0.5 元/克点差，按金额买入
- 在线看板：GitHub Pages（本仓库 Settings → Pages 链接），`data.json` 每小时由作者机器重新生成

## 角色阵容 v2（因子 + 风控；爷爷保留为对照组）

| 角色 | 进场逻辑（为什么买） | 离场逻辑（为什么卖） | 仓位控制 |
|---|---|---|---|
| 🧭 宏观策略师 | 联储/CPI/美债10Y 评分≥+1.5 且价格站回 MA60 | 评分≤-1.5 清仓；亏8%止损；浮盈从峰值回吐10%移动止盈 | 最多60%净值，分3批，每批35%现金 |
| 🛡 风控趋势手 | MA5金叉MA20；突破20日新高才加仓 | 死叉清仓；亏6%止损 | 首仓25%现金+加仓一批 |
| 🎯 超跌反手狐 | RSI14<32 且现价低于MA60超10%，双条件 | 亏8%认错；RSI>68 或反弹超MA60的10% 止盈 | 单笔≤30%现金 |
| 🧘 配置管家 | 不预测：每月检查，仓位<40%买回50%目标 | 仓位>60%卖出回落 | 单笔≤35%现金 |
| 🧓 定投爷爷(对照) | 每5交易日无脑买1000，永不卖 | （没有，这就是对照组意义） | 无（负例） |

v1 四角色（狼/狐/网格蛛/爷爷）通过 `run.py compare` 可随时对拉：350 日窗口 v2 全员正收益，
宏观策略师回撤从 -24%（无移动止盈版）压到 -7.8%，对照组爷爷 -2.07%/回撤-26.6%。

## 本地运行

```bash
cd gold && uv venv .venv && uv pip install -p .venv/bin/python akshare "requests[socks]"
cd goldlab && ../.venv/bin/python run.py backtest --days 250   # 回测
../.venv/bin/python run.py live                                # 实时信号
../.venv/bin/python run.py json > docs/data.json                # 生成前端数据
python3 -m http.server 8123 -d docs                             # 本地看前端
```

个人实盘成本（可选）：建 `position.json`，内容 `{"grams": 15.0, "cost": 922.0}`——已 gitignore，不会推到仓库。

## 连续模拟（live.py）

回测(run.py)是"每天收盘重放"，`live.py` 是**真·连续盘**：首次用 350 日回测+宏观因子对齐给角色建仓，
之后每 5 分钟（systemd timer）拉上金所分钟线，条件成立即按当时价格成交，
持仓/现金/均价/内部状态全部持久化在 `live_state.json`（gitignore），跨运行累积、永不重置。
触发节奏：v2 四角色每根分钟线评估（买分支要求空仓、卖分支要求持仓，天然幂等，止损可盘中触发）；爷爷每个交易日第一笔行情定投。

```bash
../.venv/bin/python live.py step      # 手动推进一格
../.venv/bin/python live.py publish   # 生成前端数据（--no-position 脱敏）
../.venv/bin/python live.py status    # 看账户
../.venv/bin/python live.py reset     # 清状态重来
```

## 免责

纯模拟研究，不构成投资建议。点差、滑点做了简化；上金所非交易时段返回的是最后成交价。
