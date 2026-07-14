# 毛毛虫 Agent（caterpillar）开发计划

> 高频、单只、做 T 差价型实盘助手。与 `intraday_monitor`（广谱信号报警）互补，
> 毛毛虫专注「依据当日整体走势，在同一只票上反复做差价」。
> 现状：核心链路已落地并上线，本文档作为活文档维护策略与契约。

---

## 1. 定位

针对**已选定、上行趋势**的个股，盘中以较短周期（默认 10 分钟）反复触发，扮演「做 T 教练」：
开盘看竞价/早盘决定是否低吸，上涨途中综合涨速量能、资金大小单、板块与大盘判断是否高抛锁差价，回落时轻仓撤退。
一次「低吸→高抛」或「高抛→低吸买回」即一次蠕动，**净仓位稳定，只把波动赚成差价**。

> A 股 T+1：默认假设标的**有底仓**，用「高抛部分底仓 / 低吸买回」吃差价；港股/美股可日内 T+0。

| Agent | 周期 | 模式 | 职责 |
|---|---|---|---|
| premarket_outlook | 每日 9:00 | batch | 隔夜信息、今日展望 |
| **caterpillar** | **每 10 分钟（盘中）** | **single** | **做 T 差价** |
| intraday_monitor | 每 5 分钟（盘中） | single | 广谱异动/风险报警 |
| daily_report | 每日 15:30 | batch | 复盘与次日关注 |

---

## 2. 策略模型

**阶段（phase）**：开盘竞价 / 开盘拉升 / 盘中震荡 / 高位放量 / 高位滞涨 / 回落走弱 / 尾盘。

**腿位状态机（t_leg，单只·单日）**——仅用于**约束动作方向**（防重复高抛/低吸、防接刀子）：

```
flat（空T，仅底仓）
  │  低吸买入                高抛卖出
  ▼                          ▲
long（已低吸·待高抛） ───────┘
  │  先高抛底仓做 T
  ▼
short（已高抛·待低吸买回） ──→ 企稳后低吸买回 → 回到 flat
```

- 腿位约束：`long` 只能高抛/持有；`short` 只能买回/撤退/持有，且**买回须先确认企稳**。
- 利润垫 / 蠕动次数仅**内部记录**（`caterpillar_state`），**不再在输出与通知中呈现**——决策只看当日走势该低吸/高抛/观望。

**研判输入（模型需权衡）**：
1. 涨速/位置：相对今开、日内振幅分位、5/20 日斜率、量比；
2. 资金：主力净流入额/占比 + **超大/大/中/小单结构** + 5 日趋势；
3. 大盘：所属市场指数强弱；
4. 板块：**个股所属板块当日涨幅/榜内排名** + 行业涨幅榜热度；
5. 技术面：均线多空、MACD/KDJ/RSI、支撑压力位。

---

## 3. 核心决策纪律（重点）

**先判当日走势：站稳 vs 下行半山腰**（任何低吸/买回前必判）
- 站稳（可低吸/买回）：站回今日均价/MA5 不破 + 回调缩量 + 高低点不再下移 + 超大/大单未持续流出。
- 下行半山腰（禁低吸/买回 → hold/retreat/watch）：跌破均价站不回 + 放量下行 + 连创新低 + 超大/大单持续流出。
- 「出现差价」是必要条件，「确认企稳」才是充分条件；半山腰见差价就买回 = 接刀子且自相矛盾，严禁。

**高抛后买回纪律**（`short` 腿）
1. 小幅回调 ≠ 到位：未确认企稳优先 hold，不立即买回；
2. 前后一致：高抛理由（滞涨/放量/资金转流出）未修复前不得转买回；
3. 走弱则离场：回调演变为放量破位/资金持续流出，用 retreat 而非抄底。

**资金与板块**
- 大小单辨真伪：主力净流入须由超大/大单主导；仅小单流入而超大单流出多为拉高出货/诱多，低吸谨慎。
- 板块强弱：随板块同涨→低吸更可信；逆板块独强→防透支优先高抛；明显弱于板块→低吸谨慎。

---

## 4. 数据与来源

| 维度 | 字段 | 来源 |
|---|---|---|
| 实时行情 | 现价/涨跌幅/今开高低/昨收/量额 | `SignalPackBuilder.quote`（腾讯）|
| 技术指标 | 均线/MACD/KDJ/RSI/量比/支撑压力/振幅 | `SignalPackBuilder.technical`（K线）|
| 资金面 | 主力净流入 + 超大/大/中/小单 + 5 日趋势 | `SignalPackBuilder.capital_flow`（东财，仅 A 股）|
| 大盘 | 指数现价/涨跌幅 | `AkshareCollector.get_index_data()` |
| 板块情绪 | 行业涨幅榜 Top | `DiscoveryCollector.fetch_hot_boards()`（尽力而为）|
| **个股板块** | **所属行业 + 当日涨幅/榜内排名/涨跌家数** | **`AkshareCollector.get_stock_sector()`（akshare，仅 A 股，best-effort）** |
| 历史分析 | 盘前/昨日复盘摘要 | `analysis_history` |
| 做 T 腿位 | 腿位/入场价（内部含利润垫，不外显） | `caterpillar_state`（DATA_DIR JSON）|

---

## 5. 调度与降噪

- 调度：cron `*/10 9-15 * * 1-5`，Agent 内按个股所属市场做交易时段门禁（`is_market_trading`）。
- 模式：`single`（逐只 `run_single`），独立分析与通知；周期可在 UI 调整。
- 降噪：个股级节流（`NotifyThrottle` 默认 15 分钟）+ 通知去重 TTL；仅**可执行动作**（低吸/高抛/撤退）推送，hold/watch 静默。

---

## 6. 输出契约

模型只输出**单个 JSON**，Agent 解析后映射为标准 `action` 落库并生成通知。

```json
{
  "phase": "高位放量",
  "t_action": "t_sell_high",
  "t_action_label": "高抛T出",
  "action": "reduce",
  "rise_speed": "急涨",
  "position_hint": "高抛底仓 1/3",
  "spread_target": "+1.5%",
  "signal": "急涨放量近压力",
  "reason": "今日高开站稳均价，现10分钟涨2.1%量比2.3逼近23.5压力，超大单转净流出、高位滞涨，先高抛1/3锁差价。",
  "triggers": ["回踩MA10缩量企稳且资金回流再买回"],
  "invalidations": ["放量突破23.5则不追、等回踩"],
  "risks": ["大盘跳水带动回落"]
}
```

- `reason` 须点明「站稳还是下行半山腰」的依据；已移除 `cushion_note` 字段。

| t_action | 含义 | 映射 action | 推送 |
|---|---|---|---|
| t_buy_low | 低吸建/补 T 仓、竞价低吸 | buy | ✅ |
| t_buy_back | 高抛后低吸买回 | buy | ✅ |
| t_sell_high | 高抛 T 出锁差价 | reduce | ✅ |
| retreat | 轻仓撤退、止盈止损 | reduce | ✅ |
| hold | 持 T 等待 | hold | ❌ 静默 |
| watch | 空仓观望 | watch | ❌ 静默 |

**通知文案**（已去掉「今日做T N次/利润垫」行）：

```
平安银行（601127）  现价 22.95  +2.10%
阶段：高位放量 ｜ 动作：高抛T出（底仓1/3，目标差价 +1.5%）
信号：急涨放量近压力
理由：今日站稳均价后滞涨，量比2.3逼近23.5压力且超大单转净流出，先高抛1/3锁差价。
触发：回踩MA10缩量企稳且资金回流再买回
风险：大盘跳水带动回落
——以上仅供参考，不构成投资建议
```

---

## 7. 工程接入（现状）

- 新增：`prompts/caterpillar.txt`、`src/core/caterpillar_state.py`、`src/agents/caterpillar.py`、`AkshareCollector.get_stock_sector()`。
- 注册：`server.py`（registry）、`src/core/agent_catalog.py`（`WORKFLOW_AGENT_NAMES` + `AGENT_SEED_SPECS`）、`src/agents/base.py`（去重 TTL）。
- `seed_agents()` / `build_scheduler()` 为通用逻辑，登记后即按 `single` + cron 调度，无需改调度器。
- 触发：定时调度 / `trigger_agent[_for_stock]` 手动 / UI 开关（默认 `enabled=False`，选定标的后显式开启）。

---

## 8. 状态与里程碑

| 项 | 状态 |
|---|---|
| 状态机 `caterpillar_state` | ✅ |
| Prompt（含站稳/半山腰 + 买回纪律 + 大小单 + 板块） | ✅ |
| Agent `caterpillar`（collect/build_prompt/analyze/run_single） | ✅ |
| 注册与调度接入 | ✅ |
| 个股所属板块采集 `get_stock_sector` | ✅（待生产环境实跑验证 akshare 匹配）|
| 单测 `tests/test_caterpillar.py`（腿位状态机 + 解析 + 板块 mock） | ⬜ |
| 前端详情页展示做 T 建议 | ⬜ |

---

## 9. 风险与后续

- **A 股 T+1**：以「底仓做 T」为假设、不代下单，文案明确「需自备底仓」。
- **触发成本**：10 分钟 × 多只放大 AI 调用量；靠节流 + 仅可执行动作推送降噪，必要时放宽周期或仅开重点标的。
- **板块采集稳定性**：`get_stock_sector` 依赖 akshare 两接口（`stock_individual_info_em` × `stock_board_industry_name_em`），全程 best-effort，失败则板块段缺省不影响主流程；首跑需核对命名匹配。
- **回测闭环**：已写入 `save_agent_prediction_outcome`（1/5 日维度评估做 T 建议胜率）。
- **免责**：所有输出附「仅供参考，不构成投资建议」。
