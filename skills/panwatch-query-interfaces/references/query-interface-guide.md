# PanWatch 查询接口说明文档

本文梳理本工程内会查询外部数据、外部资讯或外部服务的接口。重点覆盖个股行情、K 线、新闻资讯、公告事件、资金流、机会池、模拟盘、AI 对话、通知和版本检查。

后端路由统一在 `src/web/app.py` 注册。除 `/api/market/indices`、`/api/health`、`/api/version`、认证接口外，大部分业务接口都需要登录。

## 一、外部数据源总览

| 数据类型 | 主要实现 | 外部来源 | 备注 |
| --- | --- | --- | --- |
| 实时行情 | `src/collectors/akshare_collector.py`、`src/core/providers/quote/*` | 腾讯 `http://qt.gtimg.cn/q=`；可选 yfinance | A/H/US 均走腾讯主源；`/api/quotes/*` 走 Provider 主备链。 |
| 市场指数 | `src/web/api/market.py` | 腾讯 `http://qt.gtimg.cn/q=` | 上证、深成指、创业板、恒生、纳指、道指。 |
| K 线 | `src/collectors/kline_collector.py` | 腾讯 `web.ifzq.gtimg.cn`；东财 `push2his.eastmoney.com`；Stooq `stooq.com`；可选 Tushare/yfinance | 腾讯主路径；A/H 不足回退东财；US 不足回退 Stooq。 |
| 新闻资讯 | `src/collectors/news_collector.py` | 东财搜索 `search-api-web.eastmoney.com`；东财公告 `np-anotice-stock.eastmoney.com`；雪球 `xueqiu.com` | 数据源由 `DataSource` 表配置，默认东财资讯 + 东财公告。 |
| 公告事件 | `src/collectors/events_collector.py` | 东财公告 `np-anotice-stock.eastmoney.com`；全文 `np-cnotice-stock.eastmoney.com` | 事件类型由标题关键词归类。 |
| 个股资金流 | `src/collectors/capital_flow_collector.py` | 东财 `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | 日级资金流，默认 TTL 10 分钟；支持历史序列查询。 |
| 板块/概念资金流 | `src/collectors/sector_flow_collector.py` | 东财 `push2.eastmoney.com/api/qt/clist/get`；东财 `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | 行业/概念排行、历史、成分股资金流，接口层缓存约 60 秒。 |
| 热门股票/板块 | `src/collectors/discovery_collector.py` | 东财 `push2.eastmoney.com/api/qt/clist/get` | 发现页、机会池市场扫描共用。 |
| 股票清单/搜索 | `src/web/stock_list.py` | 东财 `80.push2delay.eastmoney.com`、`searchapi.eastmoney.com`；akshare 备用 | 缓存文件 `data/stock_list_cache.json`，TTL 7 天。 |
| 汇率 | `src/web/api/accounts.py` | 新浪 `https://hq.sinajs.cn/list=fx_shkdcny/fx_susdcny` | 组合汇总中 HKD/USD 折算 CNY 使用，TTL 1 小时。 |
| K 线截图 | `src/collectors/screenshot_collector.py` | 雪球、东方财富、新浪页面 | Playwright 打开网页截图。 |
| AI 服务 | `src/core/ai_client.py` | OpenAI 兼容接口 `base_url` | 聊天、AI 解读、Agent 分析、模型探测等。 |
| 通知服务 | `src/core/notifier.py` | Telegram、企业微信、Server 酱、PushPlus | 发送测试通知和策略/提醒通知。 |
| 版本检查 | `src/core/update_checker.py` | Docker Hub / Docker Registry | `/api/settings/update-check` 使用。 |

## 二、直接查询型 API

### 1. 市场指数

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/market/indices` | GET | 腾讯行情 | 返回主要市场指数。公共接口，无需登录。 |

调用链：`src/web/api/market.py` -> `_fetch_tencent_quotes()`。

### 2. 股票搜索、股票列表、关注池行情

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/stocks/search?q=&market=` | GET | 东财实时搜索；缓存不足时读本地清单 | 搜索 A/H/US 股票。实时搜索失败时走 `data/stock_list_cache.json`。 |
| `/api/stocks/refresh-list` | POST | 东财全量股票清单；akshare 备用 | 强制刷新股票列表缓存，覆盖 A 股、港股、美股、北交所。 |
| `/api/stocks/quotes` | GET | 腾讯行情 | 获取所有自选股实时行情，按市场分组批量查询。 |

调用链：`src/web/api/stocks.py`、`src/web/stock_list.py`、`src/collectors/akshare_collector.py`。

### 3. 实时行情 Provider 接口

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/quotes/{symbol}?market=CN` | GET | QuoteOrchestrator | 查询单只股票行情。 |
| `/api/quotes/batch` | POST | QuoteOrchestrator | 批量查询多市场股票行情。 |

请求示例：

```json
{
  "items": [
    {"symbol": "600519", "market": "CN"},
    {"symbol": "00700", "market": "HK"},
    {"symbol": "AAPL", "market": "US"}
  ]
}
```

Provider 机制：`src/core/providers/orchestrator.py` 会读取 `DataSource(type="quote")`，按 `priority` 调用已启用 provider。目前内置 `tencent`、`yfinance`。默认缓存 TTL 5 秒。

### 4. K 线与技术摘要

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/klines/{symbol}?market=CN&days=60&interval=1d` | GET | KlineCollector | 单只股票 K 线。支持 `1d/1w/1m` 聚合。 |
| `/api/klines/batch` | POST | KlineCollector | 批量 K 线。 |
| `/api/klines/{symbol}/summary?market=CN` | GET | KlineCollector | 单只股票技术指标摘要。 |
| `/api/klines/summary/batch` | POST | KlineCollector | 批量技术摘要。 |

K 线源顺序：腾讯日 K -> A/H 东财长历史兜底 -> US Stooq 兜底。模块内有正缓存、失败负缓存和同标的并发合并。

### 5. 新闻资讯与公告

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/news` | GET | NewsCollector | 获取自选股或指定股票相关新闻/公告。 |
| `/api/news/sources` | GET | 否 | 只读取本地已配置新闻源。 |

主要参数：

| 参数 | 含义 |
| --- | --- |
| `symbols` | 股票代码，逗号分隔。 |
| `names` | 股票名称，逗号分隔，优先用于东财搜索。 |
| `hours` | 查询时间窗口，默认 168 小时。 |
| `limit` | 返回数量，默认 50。 |
| `filter_related` | 是否过滤为相关资讯。 |
| `source` | `xueqiu/eastmoney_news/eastmoney`。 |

数据源：

| provider | 外部接口 | 说明 |
| --- | --- | --- |
| `eastmoney_news` | `https://search-api-web.eastmoney.com/search/jsonp` | 用股票名称或关键词搜索资讯。 |
| `eastmoney` | `https://np-anotice-stock.eastmoney.com/api/security/ann` | 批量拉股票公告。 |
| `xueqiu` | `https://xueqiu.com/statuses/stock_timeline.json` | 需要有效 Cookie，A 股转 `SH/SZ` 格式。 |

新闻采集器有 5 分钟内存缓存。

### 6. 发现页热门股票/板块

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/discovery/stocks?market=CN&mode=turnover&limit=20` | GET | 东财 clist | 热门股票，`mode=turnover/gainers`。 |
| `/api/discovery/boards?market=CN&mode=gainers&limit=12` | GET | 东财 clist | 热门行业板块；港股/美股用热门股票合成主题桶。 |
| `/api/discovery/boards/{board_code}/stocks` | GET | 东财 clist | 板块成分股排行。 |

调用链：`src/web/api/discovery.py` -> `EastMoneyDiscoveryCollector`。接口层缓存 45-60 秒，collector 内部还有 90 秒 TTL 缓存；实时源不可用时会尽量回退到本地 `MarketScanSnapshot`。

### 7. 个股与板块资金流

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/capital-flow/{symbol}/history?market=CN&days=60` | GET | 东财 fflow daykline | 单只股票日级历史资金流，返回主力/超大单/大单/中单/小单净额、占比、收盘价、涨跌幅、成交量、成交额。 |
| `/api/sector-flows?market=CN&type=industry&mode=main_net&limit=100` | GET | 东财 clist | 行业/概念板块资金流排行。`type=industry/concept`，`mode=main_net/turnover`。 |
| `/api/sector-flows/{board_code}/history?type=industry&days=60` | GET | 东财 fflow daykline | 单个行业/概念板块日级历史资金流。 |
| `/api/sector-flows/{board_code}/stocks?type=industry&limit=100` | GET | 东财 clist | 板块/概念成分股资金流贡献度。 |
| `/api/sectors?type=industry&keyword=` | GET | 东财 clist；可回退本地快照 | 行业/概念元数据与搜索，统一代码、名称、类型和来源；实时源为空时回退今日或最近资金流快照，保障聚类配置搜索可用。 |
| `/api/sector-flows/snapshots/refresh` | POST | 东财 clist | 刷新并归档行业/概念资金流快照，写入 `DATA_DIR/sector_flow_snapshots/`。 |
| `/api/sector-flows/snapshots?date=YYYY-MM-DD` | GET | 否 | 读取本地资金流快照。 |
| `/api/sector-flow-groups` | GET/PUT | 否 | 读取或覆盖板块资金流聚类规则；兼容旧版 `includes/excludes` 字符串列表，也支持结构化 `include_items/exclude_items`。运行时配置存储在 `DATA_DIR/sector_flow_groups.json`，未配置时回退 `config/sector_flow_groups.json` 默认模板。 |
| `/api/sector-flow-groups/defaults` | GET | 否 | 读取默认板块资金流聚类规则模板，用于前端恢复默认配置。 |
| `/api/sector-flow-groups/preview?date=YYYY-MM-DD` | POST | 可选 | 预览未保存的聚类规则，返回组级汇总和诊断信息。传 `date` 时读取本地快照；不传时按交易状态解析实时/快照数据，实时源为空会回退快照。 |
| `/api/sector-flow-groups/summary?date=YYYY-MM-DD` | GET | 可选 | 传 `date` 时读取本地快照聚合；不传 `date` 时交易中拉实时行业/概念资金流并固化今日快照，盘后优先读取今日快照；实时源为空时回退今日或最近快照。 |

调用链：`src/web/api/capital_flow.py` -> `CapitalFlowCollector`；`src/web/api/sector_flows.py` -> `EastMoneySectorFlowCollector`。

板块/概念资金流当前仅支持 A 股 `CN`。聚类规则以组名、包含/排除的板块或概念、权重、别名、颜色、启用状态和排序聚合，供后续板块资金流 Agent 和页面复用。结构化 selector 形如 `{type: "industry|concept|any", code: "BKxxxx", name: "板块名"}`；指定 `type` 和 `code` 时按精确板块匹配，可避免同名行业/概念被重复计入。汇总结果保留兼容字段 `main_net_inflow`，同时返回 `raw_main_net_inflow` 与 `adjusted_main_net_inflow`，便于区分原始净流入和权重调整后净流入。

### 8. 聚合洞察

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/insights/batch` | POST | 腾讯行情 + K 线摘要 | 返回行情、技术摘要、最新建议。 |
| `/api/insights/add-position-eval` | POST | 腾讯行情 + K 线 + 新闻 + AI | 建仓/加仓快速评估。 |
| `/api/insights/announcement-eval` | POST | 新闻/公告 + AI | 近期公告利好/利空/中性解读。 |

`announcement-eval` 有 6 小时缓存；无公告结果短缓存 10 分钟。

### 9. 组合与账户相关查询

账户 CRUD 本身不出网，但以下组合接口会查询外部数据：

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/portfolio/summary?include_quotes=true` | GET | 腾讯行情 + 新浪汇率 | 组合汇总、市值、浮盈、HKD/USD 折算。 |
| `/api/portfolio/diagnostics` | GET | 腾讯行情 + 新浪汇率 | 真实持仓风险诊断。 |
| `/api/portfolio/benchmark?days=60&benchmark=000300` | GET | 腾讯行情 + 新浪汇率 + K 线 | 组合与基准对比。 |
| `/api/portfolio/attribution?days=60&benchmark=000300` | GET | 腾讯行情 + 新浪汇率 + K 线 | 收益贡献归因。 |
| `/api/portfolio/ai-review` | POST | 腾讯行情 + K 线 + AI | 组合 AI 体检。 |

组合基准和归因结果按持仓指纹缓存 10 分钟，失败/空结果不缓存。

## 三、业务触发型外部查询接口

这些接口不是“单纯查一个外部源”，但会在执行过程中查询外部行情、K 线、资讯、AI 或通知服务。

### 1. 价格提醒

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/price-alerts/{rule_id}/test` | POST | QuoteOrchestrator；必要时 K 线 | 测试单条提醒规则。 |
| `/api/price-alerts/scan?dry_run=&bypass_market_hours=` | POST | QuoteOrchestrator；必要时 K 线；命中时通知 | 手动扫描全部启用规则。 |

价格提醒优先用实时行情里的 `volume_ratio`；如果报价缺量比，才回退 K 线摘要。

### 2. 机会池与策略信号

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/recommendations/entry-candidates?refresh=true` | GET | 同刷新接口 | 查询前先刷新候选池。 |
| `/api/recommendations/entry-candidates/refresh` | POST | 东财热门发现 + 腾讯行情 + K 线 | 刷新机会候选，并同步刷新策略信号。 |
| `/api/recommendations/strategy-signals/refresh` | POST | 可触发候选刷新 + K 线 | 刷新策略信号，可后台执行。 |
| `/api/recommendations/entry-candidates/outcomes/evaluate` | POST | K 线 | 评估候选后验收益。 |
| `/api/recommendations/strategy-signals/outcomes/evaluate` | POST | K 线 | 评估策略信号后验收益。 |

机会池刷新链路：

1. `EastMoneyDiscoveryCollector.fetch_hot_stocks()` 拉三市场成交额榜和涨幅榜。
2. `AkshareCollector.get_stock_data()` 批量补实时行情。
3. `KlineCollector.get_kline_summary()` 为部分候选补技术摘要。
4. 与自选股历史建议、持仓状态、本地快照合并。

### 3. 模拟盘

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/paper-trading/scan` | POST | QuoteOrchestrator | 手动触发模拟盘建仓和平仓扫描。 |
| `/api/paper-trading/positions/{position_id}/close` | POST | QuoteOrchestrator | 手动平仓时拉最新价。 |
| `/api/paper-trading/notify-test` | POST | 通知渠道 | 发送模拟盘测试通知。 |
| `/api/paper-trading/premarket-plan` | POST | 可能读取信号 + 通知渠道 | 触发盘前计划通知。 |
| `/api/paper-trading/daily-summary` | POST | 可能读取信号 + 通知渠道 | 触发日终摘要通知。 |

模拟盘行情使用 QuoteOrchestrator，遵守当前代码中的交易时段限制和 A 股 T+1 限制。

### 4. Agent 手动触发与盘中扫描

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/agents/{agent_name}/trigger` | POST | 取决于 Agent | 触发全局 Agent，如盘前/收盘报告。 |
| `/api/stocks/{stock_id}/agents/{agent_name}/trigger` | POST | 取决于 Agent | 触发单只股票 Agent，包括 TradingAgents 深度分析。 |
| `/api/agents/intraday/scan?analyze=false` | POST | 腾讯行情 + K 线 | 扫描盘中监测关联股票。 |
| `/api/agents/intraday/scan?analyze=true` | POST | 腾讯行情 + K 线 + 新闻 + 资金流 + 事件 + AI | 扫描并调用 AI 生成建议。 |
| `/api/agents/tradingagents/history-comparison` | GET | K 线 | 对历史 TradingAgents 决策做后验对比。 |

其中 TradingAgents 会通过项目适配层读取行情、K 线、新闻、公告事件、历史上下文，并调用配置的 LLM。

### 5. 聊天与 AI 工具调用

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/chat/conversations/{conversation_id}/messages` | POST | AI 服务；可能拉行情/K 线 | 发送消息并获取 AI 回复。 |

聊天工具包含：

| Tool | 外部查询 |
| --- | --- |
| `get_stock_quote` | 腾讯行情 |
| `get_technical_analysis` | K 线技术摘要 |
| `get_portfolio` | 本地持仓，无实时行情 |
| `get_stock_suggestions` | 本地建议/历史分析 |
| `get_watchlist` | 本地自选股 |

如果对话绑定了股票，发送消息前会自动注入实时行情和技术面。

### 6. Dashboard AI 策展

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/dashboard/curate` | POST | AI 服务 | 对首页候选事件做 AI 排序和一句话解释。 |

`/api/dashboard/overview` 和 `/api/dashboard/brief` 主要读本地数据库，不主动拉外部行情。

## 四、数据源、AI、通知与系统外部接口

### 1. 数据源测试

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/datasources/{source_id}/test` | POST | 按数据源类型查询外部源 | 用于测试新闻、K 线、资金流、行情、事件、截图源。 |

测试行为：

| `DataSource.type` | 支持 provider | 外部访问 |
| --- | --- | --- |
| `news` | `xueqiu/eastmoney_news/eastmoney` | 雪球/东财资讯/东财公告 |
| `kline` | `tencent/tushare/yfinance` | 腾讯/Tushare/yfinance |
| `capital_flow` | `eastmoney` | 东财资金流 |
| `quote` | `tencent/yfinance` | 腾讯/yfinance |
| `events` | `eastmoney` | 东财公告事件 |
| `chart` | `xueqiu/eastmoney/sina` | Playwright 打开外部页面截图 |

### 2. AI 服务商与模型

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/providers/models/{model_id}/test` | POST | AI chat completion | 测试模型是否可用。 |
| `/api/providers/services/{service_id}/discover-models` | POST | AI `/v1/models` | 探测服务商可用模型列表。 |

实现使用 OpenAI 兼容客户端 `AsyncOpenAI`，配置来自 `AIService.base_url/api_key` 和 `AIModel.model`。

### 3. 通知渠道测试

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/channels/{channel_id}/test` | POST | 对应通知平台 | 发送测试通知。 |

内置通知平台：

| 类型 | 外部接口 |
| --- | --- |
| Telegram | `https://api.telegram.org/bot.../sendMessage` |
| 企业微信机器人 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send` |
| Server 酱 | `https://sctapi.ftqq.com/{sendkey}.send` |
| PushPlus | `https://www.pushplus.plus/send` |

### 4. 版本更新检查

| 接口 | 方法 | 外部查询 | 说明 |
| --- | --- | --- | --- |
| `/api/settings/update-check` | GET | Docker Hub / Registry | 检查镜像最新 semver tag。 |

先查 Docker Hub tags；失败时回退 Docker Registry token + tags/list。服务端缓存 15 分钟。

## 五、内部聚合链路

### SignalPackBuilder

文件：`src/core/signals/signal_pack.py`

被盘前、盘中、收盘、TradingAgents 等 Agent 复用。按需聚合：

- 行情：`AkshareCollector` -> 腾讯行情。
- 技术：`KlineCollector` -> 腾讯/东财/Stooq。
- 新闻：`NewsCollector.from_database()` -> 东财/雪球。
- 资金流：`CapitalFlowCollector` -> 东财。
- 事件：`EventsCollector.from_database()` -> 东财公告事件。
- 持仓：本地数据库。

### Provider Orchestrator

文件：`src/core/providers/orchestrator.py`

已实现的 Orchestrator：

| Orchestrator | `DataSource.type` | 默认 TTL | 内置 provider |
| --- | --- | --- | --- |
| QuoteOrchestrator | `quote` | 5 秒 | `tencent`、`yfinance` |
| KlineOrchestrator | `kline` | 60 秒 | `tencent`、`tushare`、`yfinance` |
| CapitalFlowOrchestrator | `capital_flow` | 60 秒 | `eastmoney` |
| EventsOrchestrator | `events` | 30 分钟 | `eastmoney` |
| DiscoveryOrchestrator | `discovery` | 60 秒 | `eastmoney` |
| NewsOrchestrator | `news` | 5 分钟 | 当前未注册 provider，新闻仍走 `NewsCollector` |

上层接口目前并非全部都走 Orchestrator：`/api/quotes/*`、价格提醒、模拟盘等已走；部分历史接口仍直接调用 collector。

## 六、容易误判但不主动出网的接口

以下接口通常只读本地数据库或内存状态，不主动访问外部行情/资讯：

| 接口 | 说明 |
| --- | --- |
| `/api/news/sources` | 只读本地 `DataSource`。 |
| `/api/sector-flows/snapshots` | 只读本地 `DATA_DIR/sector_flow_snapshots/`。 |
| `/api/sector-flow-groups` | 读取或写入本地 `DATA_DIR/sector_flow_groups.json`，不主动访问外部行情。 |
| `/api/sector-flow-groups/defaults` | 只读本地 `config/sector_flow_groups.json`。 |
| `/api/sector-flow-groups/summary?date=YYYY-MM-DD` | 传入 `date` 时只读本地快照；不传 `date` 时交易中会拉实时板块/概念资金流并写入今日快照，盘后或实时源为空会读取本地快照。 |
| `/api/sector-flow-groups/preview?date=YYYY-MM-DD` | 传入 `date` 时只读本地快照；不传 `date` 时可能拉实时板块/概念资金流，盘后或实时源为空会读取本地快照。 |
| `/api/recommendations/strategy-signals` | 只读已生成的策略信号。 |
| `/api/recommendations/entry-candidates` 且 `refresh=false` | 只读已生成候选。 |
| `/api/dashboard/overview` | 只读本地快照、信号、历史；不实时拉行情。 |
| `/api/dashboard/brief` | 读取最新 Agent 报告。 |
| `/api/agents/tradingagents/latest`、`/analysis`、`/running`、`/runs/{trace_id}/progress` | 读取本地历史/日志。 |
| `/api/context/*` | 读取上下文快照、主题快照、预测结果等本地记录。 |
| `/api/history/*` | 读取本地分析历史。 |
| `/api/suggestions/*` | 读取本地建议池。 |
| `/api/logs/*` | 读取本地日志。 |

## 七、缓存与代理注意事项

- 国内行情/新闻采集器多数显式 `trust_env=False`，默认绕过环境变量代理，避免本地代理拦截行情接口。
- 发现页可读取 UI 配置或环境变量中的 `http_proxy`，用于东财连接困难时显式代理。
- 新闻缓存 5 分钟；腾讯行情短 TTL 5 秒；个股资金流 10 分钟；板块/概念资金流排行约 60 秒；K 线按交易状态缓存，交易中短缓存、收盘后长缓存。
- `/api/settings/update-check`、组合基准/归因、公告解读等也有独立缓存。
- 若新增外部查询接口，建议同步更新本文件，并标明：路由、外部域名、是否走 `DataSource`、缓存 TTL、是否绕过代理、失败回退策略。
