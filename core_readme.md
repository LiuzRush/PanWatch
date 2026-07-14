# PanWatch 核心模块说明文档

本文档主要对 PanWatch 项目中最核心的两个模块——**Agent（智能体模型）** 与 **信息获取（Collector）模块** 的实现机制与架构设计进行说明。这部分是整个监控与决策体系的大脑与感官。

---

## 1. Information Retrieval (信息获取模块)

信息获取模块充当了整个系统的“感官”，从公网（API或网页）定向抓取金融数据，并标准化后供 Agent 消费。

### 1.1 核心架构：DataCollectorManager
位于 `src/core/data_collector.py`，是全局统一的数据源管理器。
- **统一入口**：聚合了不同类型的数据源（如 K 线、新闻、资金流向、交易行情等）。它提供统一的 API 接口，例如 `collect_quote`、`collect_kline`、`collect_news`。
- **数据库驱动配置**：部分数据源（尤其是新闻或事件源）记录在数据库（利用 `src.web.models.DataSource`）。
- **可观测性**：内置了完整的日志打点记录（`CollectorLog`），记录抓包的耗时、源类型、抓包状态，便于监控与排错。

### 1.2 具体的采集器 (Collectors)
位于 `src/collectors/` 下。所有的采集器一般继承自 `BaseCollector` 或者实现了相似的接口规范。
- **AkshareCollector / 腾讯行情源** (`akshare_collector.py`)：
  - 弃用部分不稳定 API，默认封装了腾讯股票 HTTP API 以保证稳定性和无 SSL 认证报错问题。
  - 支持多市场行情的统一解析，能将 `A股 (sz/sh/bj)`、`港股 (hk)` 和 `美股 (us)` 的前缀标准化。
- **各类专项采集器**：
  - `capital_flow_collector.py`: 负责抓取个股或大盘的主力资金流向。
  - `news_collector.py`: 负责抓取对应股票或板块相关的突发新闻与公告。
  - `kline_collector.py`: 负责抓取历史 K 线数据以供技术面分析。
  - `screenshot_collector.py`: 通过无头浏览器等方式抓取关键网页截图，供多模态大模型分析。
  - `events_collector.py` & `discovery_collector.py`: 用于提取宏观事件或市场发现信息。

---

## 2. Agent (智能分析模块)

Agent 模块是 PanWatch 的“大脑”，通过编排 AI 模型进行数据分析，最后生成投资建议与告警。

### 2.1 核心架构：BaseAgent
位于 `src/agents/base.py`，所有业务 Agent 的抽象基类，定义了标准化的工作流模式 `run()`。
核心的 Pipeline 分为以下几个生命周期：
1. **`collect(context)`**：针对当前 Agent 的需求，调用 `DataCollectorManager` 及对应收集器，获取上下文数据。
2. **`build_prompt(data, context)`**：将收集到的结构化数据组合并渲染至 Prompt 模板中。
3. **`analyze(context, data)`**：请求 `AIClient`（后端大语言模型接口），获取模型的分析输出。
4. **`should_notify(result)`**：结果过滤与决策拦截。引入通知去重（Dedupe）、节流（Throttle）和通知策略（NotifyPolicy），防止由于短时波动给用户发送垃圾告警。如果验证需要通知，则通过 `NotifierManager` 发出警报。

### 2.2 业务 Agent 分类
位于 `src/agents/` 目录，不同的 Agent 专注于不同维度的分析：
- **IntradayMonitorAgent** (`intraday_monitor.py`):
  - **职责**：盘中实时监控。
  - **特点**：支持单股模式 (`run_single`) 逐个分析。自动校验当前是否处于交易时段。利用 AI 解析并统一输出决策信号（建仓/加仓/减仓/清仓/持有/观望），并对达到止盈止损线或异动阈值的标的触发严格告警。
- **DailyReportAgent** (`daily_report.py`): 生成每日复盘报告，聚合全天数据。
- **PremarketOutlookAgent** (`premarket_outlook.py`): 生成盘前展望，分析外围市场与 overnight 消息影响。
- **NewsDigestAgent** (`news_digest.py`): 专注于分析盘中及盘后突发新闻的影响面，评估利空利好。
- **ChartAnalystAgent** (`chart_analyst.py`): 主要基于 K 线、技术指标或者图表截图，做纯技术面形态研判。

---

## 3. 两者之间的协同纽带

- **AgentContext**: 在 `src/agents/base.py` 中定义的运行时上下文。把 AIClient、Notifier、AppConfig 以及持仓信息（PortfolioInfo / PositionInfo）串联起来并注入到 Agent。
- **数据流向**：
  1. 定时任务或事件触发某个 Agent 执行 (`run`)
  2. Agent 根据内置逻辑向 Collector 索要对应股票池、当前市场的各类数据。
  3. Collector 从腾讯API/数据库/爬虫获取原始数据并清理为 JSON/Dict 返回。
  4. Agent 把数据喂给 LLM，经解析和策略判断后，通过 Notifier 向外推送。
