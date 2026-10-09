# goldlab · 黄金策略角色模拟盘

用 [akshare](https://github.com/akfamily/akshare) 的上金所 Au99.99 数据，给 4 个性格迥异的模拟角色各发 5 万元，看它们在黄金里的真实表现，并给出"此刻"的动作建议。

- 数据源：上海黄金交易所 Au99.99（实时 + 250 交易日历史），抓取失败自动降级国际金价换算
- 交易模型：模拟招行积存金，买卖各 0.5 元/克点差，按金额买入
- 在线看板：GitHub Pages（本仓库 Settings → Pages 链接），`data.json` 每小时由作者机器重新生成

## 四个角色

| 角色 | 风格 | 规则 |
|---|---|---|
| 🧓 定投爷爷 | 长期定额 | 每 5 个交易日买 1000 元，打死不卖 |
| 🐺 追趋势狼 | 均线动量 | MA5 金叉 MA20 买半仓现金，死叉卖一半仓位 |
| 🦊 捡漏狐 | 均值回归 | 低于 MA20 达 3% 买 1/4 现金，高于 3% 卖 1/3 仓位 |
| 🕸 网格蛛 | 区间网格 | 以上次成交价为锚，涨/跌 4% 各动一档（1/4 仓） |

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

回测是"每天收盘重放"，`live.py` 是**真·连续盘**：首次用 250 日回测给角色建仓，
之后每 5 分钟（systemd timer）拉上金所分钟线，条件首次成立即按当时价格成交，
持仓/现金/锚价全部持久化在 `live_state.json`（gitignore），跨运行累积、永不重置。
触发节奏：网格蛛盯每根分钟线；狼/狐盘中每分钟评估、当日只动手一次；爷爷每个交易日第一笔行情定投。

```bash
../.venv/bin/python live.py step      # 手动推进一格
../.venv/bin/python live.py publish   # 生成前端数据（--no-position 脱敏）
../.venv/bin/python live.py status    # 看账户
../.venv/bin/python live.py reset     # 清状态重来
```

## 免责

纯模拟研究，不构成投资建议。点差、滑点做了简化；上金所非交易时段返回的是最后成交价。
