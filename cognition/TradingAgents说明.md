# TradingAgents 实现与扩展说明

本文说明 PanWatch 里 TradingAgents 是如何接入的、哪些地方可以改、以及是否可以创建一个同级策略供页面选择。

## 一、TradingAgents 在项目里的定位

TradingAgents 在 PanWatch 里是一个 `workflow agent`，和 `盘前分析`、`盘中监测`、`收盘复盘`、`毛毛虫做T` 同级。

核心定义：

- Agent 实现：`src/agents/tradingagents/agent.py`
- Agent 注册：`server.py` 的 `AGENT_REGISTRY`
- 默认配置：`src/core/agent_catalog.py` 的 `AGENT_SEED_SPECS`
- 前端配置入口：`frontend/src/pages/Agents.tsx`
- 结果映射：`src/agents/tradingagents/result_mapper.py`
- LLM 配置桥接：`src/agents/tradingagents/llm_adapter.py`
- 数据工具适配：`src/agents/tradingagents/toolkit_adapter.py`
- 模拟盘信号桥接：`src/agents/tradingagents/paper_trading_bridge.py`

它不是普通的“单 prompt 调用”Agent。普通 Agent 继承 `BaseAgent` 后通常走：

```text
collect()
  -> build_prompt()
  -> ai_client.chat()
  -> AnalysisResult
```

TradingAgents 重写了 `analyze()`，实际走的是上游 `TauricResearch/TradingAgents` 的多 Agent 图：

```text
collect() 收集 PanWatch 数据
  -> build_ta_llm_config() 生成 TradingAgents 配置
  -> patch_route_to_vendor() 接管上游数据工具
  -> TradingAgentsGraph.propagate(symbol, date)
  -> map_state_to_result() 映射成 PanWatch AnalysisResult
  -> 写入 analysis_history / stock_suggestions
  -> 可选写入 strategy_signal_runs 给模拟盘消费
```

## 二、一次 TradingAgents 分析如何执行

### 1. 触发入口

常见触发方式：

- 在股票详情页手动触发深度分析。
- 在 Agents 页面启用后手动触发 Agent。
- 如果开启 `auto_trigger`，盘中监测发现急涨/急跌后异步触发。

单股触发主要走 `server.py` 的 `trigger_agent_for_stock()`：

```text
trigger_agent_for_stock("tradingagents", stock)
  -> 读取 AgentConfig.config
  -> TradingAgentsAgent(**config)
  -> agent.run(context)
```

批量/调度触发走 `trigger_agent()`；由于 TradingAgents 的 `execution_mode` 是 `single`，会按绑定股票逐只运行。

### 2. 数据采集

`TradingAgentsAgent.collect()` 会通过 PanWatch Provider Orchestrator 拉数据：

- 实时行情：quote
- K 线：kline，默认 120 天
- 资金流：capital_flow
- 事件/公告：events，默认 30 天
- A 股财务摘要：financial
- 技术指标预算：technical

这些数据先进入 PanWatch 的统一数据结构，再交给 TradingAgents 使用。

### 3. LLM 配置桥接

`llm_adapter.py` 把 PanWatch 的 AI 服务配置转换成 TradingAgents 需要的 config。

关键点：

- `backend_url` 使用 PanWatch 当前 AI 服务的 `base_url`。
- `deep_think_llm` 用于辩论、风控、PM 等深度推理节点。
- `quick_think_llm` 用于分析师和工具调用节点。
- `llm_provider` 固定走 `openrouter` 兼容路径，避免第三方 OpenAI 兼容服务不支持 `/v1/responses`。
- API Key 会注入到 `OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`。

注意：TradingAgents 上游 deep/quick 两档模型共用一个 `backend_url`。如果要混用不同厂商模型，建议通过 LiteLLM 这类代理把多厂商聚合到同一个 OpenAI 兼容 endpoint。

### 4. 数据工具适配

上游 TradingAgents 默认用自己的 vendor 路由，例如 yfinance。PanWatch 为了支持 A 股/港股和项目内缓存数据，在 `toolkit_adapter.py` 里 monkeypatch 了上游：

```text
tradingagents.dataflows.interface.route_to_vendor
```

效果：

- A 股 6 位数字代码优先走 PanWatch 数据。
- 港股 5 位数字代码会做 PanWatch/yfinance 格式兼容。
- 美股 ticker 保留上游默认路径。
- 每次工具命中/错过会写 `ta_toolkit` 日志，便于前端进度和诊断展示。

这里是比较脆弱但必要的适配层，因为上游 TradingAgents 没有稳定公开的数据工具注入接口。升级上游 TradingAgents 后，如果工具调用签名变化，优先检查这个文件。

### 5. 上游多 Agent 图执行

真正的分析由上游库完成：

```python
TradingAgentsGraph(
    selected_analysts=ta_config["selected_analysts"],
    config=ta_config,
    callbacks=[progress_handler],
).propagate(symbol, date_str)
```

上游核心链路大致是：

```text
market analyst
social/sentiment analyst
news analyst
fundamentals analyst
  -> bull/bear debate
  -> research manager
  -> trader
  -> risk debate
  -> portfolio manager
  -> final decision
```

PanWatch 通过 callback 记录进度，并通过 `portfolio_context.py` 把当前持仓、成本价、交易风格等信息注入到上游 PM 的 `past_context`。

### 6. 结果映射

上游返回：

```text
final_state, decision
```

`result_mapper.py` 会把它转换成 PanWatch 标准结果：

- `AnalysisResult.content`：详情页展示的 Markdown。
- `notify_content`：通知用的简版最终决策。
- `raw_data.suggestion`：统一建议格式。
- `raw_data.analyst_reports`：四类分析师完整报告。
- `raw_data.debate_history`：看多/看空辩论。
- `raw_data.risk_debate`：风控辩论。
- `raw_data.final_decision`：PM 最终决策书。
- `raw_data.trader_plan`：交易员计划。

评级解析优先级：

```text
PM 正文里的显式评级
  -> 上游 decision
  -> 正文关键词兜底
```

5 档评级会被映射成 PanWatch 的 3 档 action：

| TradingAgents 评级 | PanWatch action |
| --- | --- |
| buy | buy |
| overweight | buy |
| hold | hold |
| underweight | sell |
| sell | sell |

### 7. 结果落库

一次成功分析会写入：

- `analysis_history`：深度分析详情、成本、原始结果。
- `stock_suggestions`：持仓页/关注列表上的 AI 建议徽章。
- 可选 `strategy_signal_runs`：用于模拟盘自动开仓。

其中 `strategy_signal_runs` 只有在 `emit_paper_trading_signal=true` 时才会写。

## 三、当前可以通过页面配置什么

在 Agents 页面打开 TradingAgents 的“深度配置”，可以配置：

- 深度思考模型：`deep_model`
- 快速思考模型：`quick_model`
- 月度预算：`monthly_budget_usd`
- 超预算行为：`over_budget_action`
  - `reject`：拒绝新触发
  - `warn`：警告但继续
  - `continue`：不提示也不挡
- 辩论轮次：`debate_rounds`
- 超时时间：`timeout_minutes`
- 是否把 BUY 决策写入模拟盘信号：`emit_paper_trading_signal`
- 盘中急涨/急跌自动触发：`auto_trigger.enabled`
- 自动触发涨跌幅阈值：`auto_trigger.change_pct_threshold`
- 自动触发冷却时间：`auto_trigger.cooldown_hours`
- 高级完整 JSON：可以直接编辑 `AgentConfig.config`

默认配置在 `src/core/agent_catalog.py`：

```python
{
    "analyst_types": ["market", "social", "news", "fundamentals"],
    "debate_rounds": 1,
    "monthly_budget_usd": 10.0,
    "over_budget_action": "reject",
    "cache_ttl_hours": 12,
    "output_language": "Chinese",
    "deep_model": "",
    "quick_model": "",
    "timeout_minutes": 15,
    "emit_paper_trading_signal": False,
}
```

## 四、哪些部分可以接入修改

### 1. 改 LLM、模型分档、预算

修改位置：

- `src/agents/tradingagents/llm_adapter.py`
- `src/core/agent_catalog.py`
- `frontend/src/pages/Agents.tsx`

适合改：

- 新增 provider 兼容逻辑。
- 改 deep/quick 模型选择规则。
- 改默认预算、默认超时时间、默认分析师组合。
- 增加更多 UI 配置项。

### 2. 改数据源注入

修改位置：

- `src/agents/tradingagents/agent.py` 的 `collect()`
- `src/agents/tradingagents/toolkit_adapter.py`
- `src/agents/tradingagents/financial_data.py`

适合改：

- 给 TradingAgents 增加更多上下文，如行业数据、龙虎榜、财报细项。
- 改 A 股/港股数据兜底逻辑。
- 调整新闻/公告时间窗口。
- 给上游工具返回更结构化的数据摘要。

风险点：

- `toolkit_adapter.py` 依赖上游 TradingAgents 的内部函数签名。
- 如果上游库升级后字段或方法名变了，这里最容易出问题。

### 3. 改结果格式、评级和建议映射

修改位置：

- `src/agents/tradingagents/result_mapper.py`
- `src/core/suggestion_pool.py`

适合改：

- 5 档评级如何映射成 buy/hold/sell。
- 置信度解析。
- 详情页 Markdown 内容。
- 通知内容长短。
- AI 建议徽章里的 action/action_label/signal/reason。

### 4. 改模拟盘桥接

修改位置：

- `src/agents/tradingagents/paper_trading_bridge.py`
- `src/core/paper_trading_engine.py`

当前逻辑：

- 只有 `enabled=True` 且 TA 决策是 `buy/add` 才写模拟盘信号。
- `sell` 不会自动平仓。
- 入场区间用当前价 ±2%。
- 止损用当前价 -5%。
- 止盈用当前价 +10%。
- `strategy_code="tradingagents"`。

适合改：

- 让 TA 输出的交易计划决定 entry/stop/target。
- 让 underweight/sell 写入 `reduce/sell` 反转信号。
- 增加信号有效期。
- 把 `strategy_code` 拆成不同 TA 子策略。

### 5. 改自动触发

修改位置：

- `src/agents/tradingagents/auto_trigger.py`
- `frontend/src/pages/Agents.tsx`

当前逻辑：

- 默认关闭。
- 由 `intraday_monitor` 调用。
- `|change_pct| >= 阈值` 时触发。
- 同一标的有冷却时间。
- 月度预算用尽会停止触发。

适合改：

- 增加放量、资金流、板块强弱等触发条件。
- 只对持仓股触发。
- 只对关注池 Top N 触发。
- 不同市场使用不同阈值。

### 6. 改上游 TradingAgents 内部 Agent

如果你想改 TradingAgents 自己的 analyst、debate、risk、PM prompt 或节点逻辑，需要改上游库本身，而不是 PanWatch 这一层。

当前项目通过 requirements 安装：

```text
tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.3.0
```

更深层的修改方式有两种：

1. fork `TauricResearch/TradingAgents`，改完后把 `requirements.txt` 指到你的 fork。
2. 本地开发时 `pip install -e` 一个你自己的 TradingAgents 工作副本。

PanWatch 这一层更适合做数据注入、配置桥接、结果映射和信号消费，不适合大规模 monkeypatch 上游内部节点。

## 五、是否可以创建一个同级策略供选择

可以，但要先区分你说的“同级”是哪一层。

### 方案 A：创建和 TradingAgents 同级的 Agent

如果你想在 Agents 页面出现一个新的深度策略，例如：

```text
MyDeepAgents 深度分析
```

它和 TradingAgents、毛毛虫、盘中监测同级，那么要新增一个 Agent。

最小接入步骤：

1. 新增文件：

```text
src/agents/my_deep_agent.py
```

2. 实现 `BaseAgent`：

```python
from src.agents.base import BaseAgent, AgentContext, AnalysisResult


class MyDeepAgent(BaseAgent):
    name = "my_deep_agent"
    display_name = "我的深度策略"
    description = "自定义深度分析策略"

    async def collect(self, context: AgentContext) -> dict:
        # 拉行情/K线/新闻/持仓等数据
        return {}

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        return "system prompt", "user prompt"

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        # 可走普通 ai_client.chat，也可以像 TradingAgents 一样接外部框架
        content = await context.ai_client.chat(*self.build_prompt(data, context))
        return AnalysisResult(
            agent_name=self.name,
            title="【我的深度策略】",
            content=content,
            raw_data=data,
        )
```

3. 在 `server.py` 注册：

```python
from src.agents.my_deep_agent import MyDeepAgent

AGENT_REGISTRY = {
    ...
    "my_deep_agent": MyDeepAgent,
}
```

4. 在 `src/core/agent_catalog.py` 加 `AgentSeedSpec`：

```python
AgentSeedSpec(
    name="my_deep_agent",
    display_name="我的深度策略",
    description="自定义深度分析策略",
    enabled=False,
    schedule="",
    execution_mode="single",
    kind=AGENT_KIND_WORKFLOW,
    visible=True,
    display_order=45,
    config={},
)
```

5. 如果要驱动模拟盘，像 `paper_trading_bridge.py` 一样写入 `StrategySignalRun`：

```text
status=active
action=buy/add
entry_low 不为空
entry_high 不为空
strategy_code=my_deep_agent
```

这样模拟盘下一轮扫描就能消费你的信号。

### 方案 B：创建机会池里的同级 strategy_code

如果你说的是“机会页筛选里的策略”，例如在策略下拉里出现：

```text
我的突破策略
```

那要接的是 `StrategyCatalog`，不是 Agent。

最小接入步骤：

1. 在 `src/core/strategy_catalog.py` 的 `DEFAULT_STRATEGIES` 增加：

```python
StrategySpec(
    code="my_breakout",
    name="我的突破策略",
    description="自定义突破策略",
    risk_level="medium",
    params={"horizon_days": 3},
    default_weight=1.0,
)
```

2. 让候选生成阶段给候选打上这个 tag：

```text
EntryCandidate.strategy_tags 包含 "my_breakout"
```

3. `strategy_engine._strategy_codes_for_candidate()` 会把 `strategy_tags` 转成 `StrategySignalRun.strategy_code`。

4. 机会页策略下拉来自 `/api/recommendations/strategy-catalog`，新增的 `StrategySpec` 会出现在下拉中。

适合改的位置：

- `src/core/entry_candidates.py`：定义什么样的候选带上你的策略 tag。
- `src/core/strategy_engine.py`：调整策略评分、权重、风险、组合约束。
- `src/core/strategy_catalog.py`：注册策略名称、风险等级、默认权重、持有周期。

### 方案 C：不走候选池，直接写 StrategySignalRun

如果你的策略本身已经能直接产出交易信号，可以跳过 `EntryCandidate`，直接写：

```text
strategy_signal_runs
```

参考：

```text
src/agents/tradingagents/paper_trading_bridge.py
```

这样做适合：

- 外部策略服务接入。
- 另一套 AI 框架接入。
- 你想直接让模拟盘消费信号。

但如果你还想在机会页“策略下拉”里筛选它，仍然建议同时在 `StrategyCatalog` 里注册对应 `strategy_code`。

## 六、推荐的扩展路线

如果只是想改 TradingAgents 表现，优先顺序：

1. 改 Agents 页面里的配置：模型、预算、辩论轮次、超时。
2. 改 `result_mapper.py`：评级、置信度、展示内容。
3. 改 `paper_trading_bridge.py`：如何生成模拟盘入场信号。
4. 改 `agent.py collect()` 和 `toolkit_adapter.py`：给 TA 喂更多本地数据。
5. 最后才 fork 上游 TradingAgents。

如果想做一个全新的同级策略：

1. 想出现在 Agents 页面：新增 `BaseAgent` 子类 + `AGENT_REGISTRY` + `AgentSeedSpec`。
2. 想出现在机会页策略下拉：新增 `StrategySpec` + 给候选打 `strategy_tags`。
3. 想驱动模拟盘：写 `StrategySignalRun`，满足 `active + buy/add + entry_low/high`。

这三层可以独立做，也可以组合做。比如一个新的 `MyDeepAgent` 可以同时：

- 在 Agents 页面可触发。
- 把建议写入 `stock_suggestions`。
- 把可执行信号写入 `strategy_signal_runs`。
- 在 `StrategyCatalog` 注册 `my_deep_agent`，让机会页可筛选。
- 被模拟盘自动消费。

## 七、实现上的注意点

- TradingAgents 是软依赖；库不可用时不会让服务崩溃，但运行会返回明确错误。
- 成本预算来自 `analysis_history.raw_data.cost_usd` 聚合。
- 当前缓存是同标的同日缓存，`force_refresh=True` 会跳过缓存。
- `cache_ttl_hours` 当前只控制是否启用缓存；代码实际按“同日是否已有分析”判断，不精确按小时过期。
- `emit_paper_trading_signal` 默认关闭，避免深度分析后误开模拟仓。
- TradingAgents 写入模拟盘信号时，`strategy_code="tradingagents"`；如果希望它在机会页策略下拉可选，建议也把 `tradingagents` 加到 `StrategyCatalog`。
- 上游 TradingAgents 升级后，最需要回归测试的是 `toolkit_adapter.py` 和 `result_mapper.py`。
