# 龙虎榜数据接口与晚间 Agent 开发计划

## 1. 现状 Review

### 结论

当前项目没有查询当日龙虎榜的后端接口，也没有龙虎榜 collector 或 agent。

现有能力可以复用：

- K 线接口已存在：`GET /api/klines/{symbol}`、`POST /api/klines/batch`、`GET /api/klines/{symbol}/summary`、`POST /api/klines/summary/batch`，实现位置见 `src/web/api/klines.py:102`、`src/web/api/klines.py:118`、`src/web/api/klines.py:145`、`src/web/api/klines.py:158`。
- 实时行情接口已存在：`GET /api/quotes/{symbol}`、`POST /api/quotes/batch`，实现位置见 `src/web/api/quotes.py:67`、`src/web/api/quotes.py:80`。
- 资金流 collector 已存在，支持个股主力、超大单、大单、中单、小单净流入，见 `src/collectors/capital_flow_collector.py:44`。
- Agent 调度框架已支持 5 段 cron 表达式，见 `src/core/scheduler.py:31`。21:00 工作日触发可配置为 `0 21 * * 1-5`。
- 新 agent 需要注册到 `AGENT_REGISTRY`，当前注册表在 `server.py:759`。
- 新 agent 需要加入 workflow 种子配置，当前配置在 `src/core/agent_catalog.py:59`。

缺口：

- `src/web/api` 中没有龙虎榜路由；`src/web/app.py:4` 到 `src/web/app.py:27` 的 API 导入列表也没有龙虎榜模块。
- 数据源类型表没有龙虎榜类型；`src/web/api/datasources.py:17` 当前只有 `news/kline/capital_flow/quote/events/chart`。
- 没有龙虎榜字段标准化、席位明细解析、机构/游资席位识别、候选股票评分逻辑。
- 没有持久化龙虎榜快照。若只依赖实时接口，每次 agent 重跑会重复请求外部数据，且不利于回溯。

### 可用外部数据源

本地安装的 `akshare` 包已包含龙虎榜函数：

- `stock_lhb_detail_em(start_date, end_date)`：东方财富龙虎榜区间明细。
- `stock_lhb_stock_detail_em(symbol, date, flag)`：个股某日买入/卖出席位明细，`flag` 为 `买入` 或 `卖出`。
- `stock_lhb_stock_detail_date_em(symbol)`：个股有龙虎榜详情的日期列表。

`stock_lhb_detail_em` 的主要字段包括：

- `代码`、`名称`、`上榜日`、`解读`、`收盘价`、`涨跌幅`
- `龙虎榜净买额`、`龙虎榜买入额`、`龙虎榜卖出额`、`龙虎榜成交额`
- `市场总成交额`、`净买额占总成交比`、`成交额占总成交比`
- `换手率`、`流通市值`、`上榜原因`

`stock_lhb_stock_detail_em` 的席位字段包括：

- `交易营业部名称`
- `买入金额`、`买入金额-占总成交比例`
- `卖出金额`、`卖出金额-占总成交比例`
- `净额`
- `类型`

## 2. 可行性分析

整体可行，建议分两期开发。

一期可以做到：

- 每晚 21:00 拉取当日 A 股龙虎榜明细。
- 对每只上榜股票拉取买入/卖出席位明细。
- 结合龙虎榜净买额、净买额占比、换手率、上榜原因、机构席位净买入、知名营业部净买入、近 20/60/120 日 K 线结构进行打分。
- 选出值得关注的 Top 10。
- 调用 AI 生成晚间龙虎榜复盘结论，并通过现有通知渠道发送。

主要限制：

- 龙虎榜只适用于 A 股；港股、美股没有同一语义的数据。
- 周末、节假日、非交易日 21:00 仍可能被 cron 触发，需要自动回退到最近交易日或直接跳过。
- 当日 `上榜后1日/2日/5日/10日` 字段在当天通常不可用，不能用于当晚评分，只能用于后续回测或复盘校验。
- 席位识别只能先用启发式规则：例如 `机构专用`、`沪股通专用`、`深股通专用`、常见游资营业部关键字。要做到高质量游资画像，需要维护一份可更新的席位标签表。
- 外部数据源稳定性不可控，需要缓存、重试、失败降级和运行日志。

## 3. 推荐设计

### 3.1 新增 collector

新增文件：

- `src/collectors/dragon_tiger_collector.py`

建议数据结构：

- `DragonTigerDailyItem`
  - `symbol`
  - `name`
  - `trade_date`
  - `close_price`
  - `change_pct`
  - `reason`
  - `interpretation`
  - `net_buy_amount`
  - `buy_amount`
  - `sell_amount`
  - `billboard_turnover`
  - `market_turnover`
  - `net_buy_ratio`
  - `deal_ratio`
  - `turnover_rate`
  - `free_float_market_cap`

- `DragonTigerSeatItem`
  - `symbol`
  - `trade_date`
  - `side`
  - `rank`
  - `seat_name`
  - `seat_type`
  - `buy_amount`
  - `buy_ratio`
  - `sell_amount`
  - `sell_ratio`
  - `net_amount`
  - `seat_tags`

- `DragonTigerCandidate`
  - `symbol`
  - `name`
  - `score`
  - `score_reasons`
  - `daily`
  - `buy_seats`
  - `sell_seats`
  - `capital_flow`
  - `kline_summary`
  - `klines`

核心方法：

- `get_daily(date: date) -> list[DragonTigerDailyItem]`
- `get_stock_seats(symbol: str, trade_date: date) -> tuple[list[DragonTigerSeatItem], list[DragonTigerSeatItem]]`
- `build_candidates(date: date, limit: int = 10, kline_days: int = 120) -> list[DragonTigerCandidate]`

### 3.2 新增龙虎榜 API

新增文件：

- `src/web/api/dragon_tiger.py`

建议接口：

- `GET /api/dragon-tiger/daily?date=YYYY-MM-DD`
  - 返回当日龙虎榜明细。

- `GET /api/dragon-tiger/{symbol}/seats?date=YYYY-MM-DD`
  - 返回个股当日买入/卖出席位明细。

- `GET /api/dragon-tiger/candidates?date=YYYY-MM-DD&limit=10`
  - 返回按规则评分后的 Top N 候选。

需要同步修改：

- `src/web/app.py`
  - 导入 `dragon_tiger`
  - `app.include_router(dragon_tiger.router, prefix="/api/dragon-tiger", tags=["dragon-tiger"], dependencies=protected)`

- `src/web/api/datasources.py`
  - `TYPE_LABELS` 增加 `"dragon_tiger": "龙虎榜"`

### 3.3 持久化与缓存

建议新增数据库模型：

- `DragonTigerSnapshot`
  - `id`
  - `trade_date`
  - `symbol`
  - `name`
  - `daily_data`
  - `candidate_score`
  - `score_reasons`
  - `created_at`
  - `updated_at`

- `DragonTigerSeatSnapshot`
  - `id`
  - `trade_date`
  - `symbol`
  - `side`
  - `rank`
  - `seat_name`
  - `seat_type`
  - `seat_tags`
  - `buy_amount`
  - `sell_amount`
  - `net_amount`
  - `raw_data`

约束：

- `DragonTigerSnapshot` 对 `(trade_date, symbol)` 做唯一约束。
- `DragonTigerSeatSnapshot` 对 `(trade_date, symbol, side, rank, seat_name)` 做唯一约束。

缓存策略：

- 当天 21:00 后首次拉取后写库。
- API 查询优先读库；如指定日期无数据，可选择 live fetch 并写库。
- agent 重跑时优先复用当日快照，除非传入 `force_refresh=true`。

### 3.4 Top 10 评分逻辑

初版规则评分建议 100 分制：

- 龙虎榜资金强度：35 分
  - 净买额绝对值
  - 净买额占总成交比
  - 龙虎榜成交额占总成交比

- 席位质量：25 分
  - 机构专用净买入为正
  - 买一到买五集中度适中
  - 卖出席位无明显压倒性净卖出
  - 知名营业部或活跃席位净买入

- 技术结构：25 分
  - 20 日/60 日趋势
  - 当日放量但不过度透支
  - 近期涨幅不过热
  - 支撑压力位置合理

- 风险扣分：15 分
  - 一字板或连续大涨后高位分歧
  - 换手率过高且净买额占比低
  - 大额净卖出席位集中
  - 流通市值过小导致流动性/操纵风险

候选输出字段：

- `rank`
- `symbol`
- `name`
- `score`
- `focus_reason`
- `risk_reason`
- `seat_summary`
- `technical_summary`
- `suggested_action`
  - `watch`
  - `avoid`
  - `track_only`
  - `high_risk_watch`

### 3.5 新增晚间 Agent

新增文件：

- `src/agents/dragon_tiger_review.py`
- `prompts/dragon_tiger_review.txt`

Agent 名称建议：

- `dragon_tiger_review`
- 展示名：`龙虎榜复盘`
- 调度：`0 21 * * 1-5`
- 执行模式：`batch`
- 默认启用：建议先 `enabled=False`，开发验证稳定后再开启。

Agent 流程：

1. 解析运行日期。
2. 如果当天无龙虎榜数据，回退最近一个有数据的交易日或跳过。
3. 拉取当日龙虎榜明细。
4. 对候选股票批量拉取：
   - 买入席位
   - 卖出席位
   - 120 日 K 线
   - K 线摘要
   - 当前资金流摘要
5. 规则打分选 Top 10。
6. 构造 prompt：
   - 市场概况
   - Top 10 候选表
   - 每只股票的资金/席位/技术/风险证据
7. 调用 AI 生成结论。
8. 保存 agent run。
9. 通过现有通知渠道发送。

需要修改：

- `server.py`
  - import `DragonTigerReviewAgent`
  - `AGENT_REGISTRY` 增加 `"dragon_tiger_review": DragonTigerReviewAgent`

- `src/core/agent_catalog.py`
  - `WORKFLOW_AGENT_NAMES` 增加 `dragon_tiger_review`
  - `AGENT_SEED_SPECS` 增加对应配置

### 3.6 Prompt 建议

Prompt 应明确限制：

- 这不是买入指令，只输出关注优先级和跟踪条件。
- 必须区分“机构净买入”、“游资接力”、“高位分歧”、“纯情绪炒作”。
- 每只股票必须给出：
  - 上榜原因
  - 资金结构
  - 席位结构
  - 技术位置
  - 风险点
  - 次日观察条件
- Top 10 之外如整体质量差，可以少于 10 只，不强行凑数。

## 4. 开发步骤

### Phase 1：数据层与接口

1. 新增 `src/collectors/dragon_tiger_collector.py`。
2. 封装 `akshare` 龙虎榜函数，完成字段标准化。
3. 增加席位标签启发式规则：
   - `机构专用`
   - `沪股通专用`
   - `深股通专用`
   - 常见活跃营业部关键词
4. 新增 `src/web/api/dragon_tiger.py`。
5. 在 `src/web/app.py` 注册 `/api/dragon-tiger`。
6. 在 `src/web/api/datasources.py` 增加 `dragon_tiger` 类型。
7. 加单元测试：
   - mock akshare 返回 DataFrame
   - 验证字段标准化
   - 验证空数据、异常、非交易日行为

### Phase 2：候选评分与历史行情分析

1. 复用 `KlineCollector.get_klines(symbol, days=120)`。
2. 复用 `KlineCollector.get_kline_summary(symbol)`。
3. 复用 `CapitalFlowCollector.get_capital_flow_summary(symbol)`。
4. 实现 `build_candidates()`。
5. 加评分单测：
   - 机构净买入高分
   - 大额净卖出扣分
   - 高位连续暴涨扣分
   - 数据缺失时不崩溃

### Phase 3：Agent

1. 新增 `src/agents/dragon_tiger_review.py`。
2. 新增 `prompts/dragon_tiger_review.txt`。
3. 在 `server.py` 注册 agent。
4. 在 `src/core/agent_catalog.py` 增加种子配置：
   - `name="dragon_tiger_review"`
   - `display_name="龙虎榜复盘"`
   - `enabled=False`
   - `schedule="0 21 * * 1-5"`
   - `execution_mode="batch"`
   - `kind=AGENT_KIND_WORKFLOW`
   - `visible=True`
5. 手动触发 agent 验证完整链路。
6. 开启定时运行前，先观察 3 个交易日。

### Phase 4：持久化与回溯

1. 新增数据库模型和 migration。
2. API 优先读库，缺失时 live fetch。
3. Agent 运行写入快照。
4. 在前端后续可增加“龙虎榜复盘”页面：
   - 日期选择
   - Top 10 列表
   - 个股席位详情
   - 历史走势摘要

## 5. 验收标准

接口验收：

- `GET /api/dragon-tiger/daily?date=YYYY-MM-DD` 返回标准化列表。
- `GET /api/dragon-tiger/{symbol}/seats?date=YYYY-MM-DD` 返回买入/卖出席位。
- `GET /api/dragon-tiger/candidates?date=YYYY-MM-DD&limit=10` 返回最多 10 只候选及评分原因。

Agent 验收：

- 21:00 触发配置可预览。
- 当天无数据时不会失败刷屏。
- 数据源异常时 agent run 记录失败原因。
- 正常交易日可生成包含 Top 10、席位分析、技术分析、风险提示的报告。

质量验收：

- collector 单元测试覆盖字段标准化、空数据、异常路径。
- 评分逻辑单测覆盖主要加分/扣分规则。
- 不引入真实网络依赖的测试。
- 后端 `python server.py` 可启动。

## 6. 建议优先级

P0：

- collector 封装
- API 路由
- Top 10 规则评分
- Agent 注册与 prompt

P1：

- 数据库存储
- 前端龙虎榜页面
- 席位标签配置化

P2：

- 历史回测：统计不同龙虎榜结构在 1/2/5/10 日后的收益分布。
- 席位画像：按营业部维护胜率、偏好题材、平均持仓周期。
- 候选池联动：把高质量龙虎榜股票写入现有推荐/建议池。
