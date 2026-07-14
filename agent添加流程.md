# PanWatch Agent 添加流程

## 1. 项目审阅结论

PanWatch 的 Agent 是“数据采集 → Prompt 构建 → AI 分析 → 结果持久化/通知”的异步流水线。核心位置如下：

- `src/agents/base.py`：定义 `AgentContext`、`AnalysisResult` 和 `BaseAgent.run()` 标准执行链路。
- `src/agents/`：Agent 业务实现；`src/collectors/` 和 `src/core/signals/` 提供行情、新闻、K 线及结构化信号。
- `prompts/`：每个 Agent 的系统 Prompt。
- `src/core/agent_catalog.py`：Agent 类型和数据库种子配置的唯一目录。
- `server.py`：实现类导入、`AGENT_REGISTRY`、上下文构建、手动触发和调度器装配。
- `src/core/scheduler.py`：基于 APScheduler 执行 `batch` 或 `single` 模式。
- `src/web/api/agents.py`、`src/web/api/stocks.py`：配置、股票绑定、触发和运行历史 API。
- `frontend/src/pages/Agents.tsx`：通用工作流 Agent 配置界面。

启动链路为：

```text
lifespan → init_db → seed_agents → build_scheduler
         → 查询已启用 workflow → 按注册表实例化 → 按 schedule 注册任务
         → 动态构建 AgentContext → collect/analyze/notify → AgentRun
```

Agent 分为两类：

- `workflow`：用户可见、可绑定股票、可启用和调度。
- `capability`：内部或按需能力；API 会强制禁用调度，也不能绑定股票，必须有专用调用方才有实际入口。

## 2. 先确定执行模型

选择 `batch` 或 `single`：

- `batch`：一次处理 `context.watchlist` 中的全部股票，适合盘前/盘后报告；实现基类要求的 `collect()` 和 `build_prompt()` 即可。
- `single`：调度器逐只股票调用 `run_single(context, symbol)`，适合盘中监测。实现时必须在 `finally` 中恢复被临时过滤的 `context.config.watchlist`，并自行处理通知、节流和跳过状态。

命名统一使用 `snake_case`，且以下位置的 `name` 必须完全一致，例如 `risk_monitor`：类属性、Prompt 文件、目录配置、注册表、测试和持久化数据中的 `agent_name`。

## 3. 实现 Agent

在 `src/agents/risk_monitor.py` 新建实现，并在 `prompts/risk_monitor.txt` 新建 Prompt：

```python
from pathlib import Path

from src.agents.base import AgentContext, AnalysisResult, BaseAgent

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "risk_monitor.txt"


class RiskMonitorAgent(BaseAgent):
    name = "risk_monitor"
    display_name = "风险监测"
    description = "识别自选股中的异常风险信号"

    def __init__(self, threshold: float = 3.0) -> None:
        self.threshold = threshold

    async def collect(self, context: AgentContext) -> dict:
        # 优先复用 collectors、SignalPackBuilder 和 ContextBuilder；不要在此写同步网络阻塞调用。
        return {"stocks": [], "threshold": self.threshold}

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        return system_prompt, f"待分析数据：{data}"

    async def should_notify(self, result: AnalysisResult) -> bool:
        return "风险" in result.content
```

注意：

- 构造参数必须有类型标注和安全默认值，并与种子配置 `config` 完全匹配。
- 默认 `BaseAgent.analyze()` 会调用 `AIClient.chat()`；需要结构化建议、历史或建议池时，可参考 `intraday_monitor.py`、`daily_report.py`，复用 `structured_output`、`save_analysis()`、`save_suggestion()`。
- `AnalysisResult.raw_data` 应保留 `notified`、`notify_error`、`notify_skipped`、`skipped`、`should_alert` 等运行状态，便于运行历史和 API 正确解释结果。
- 数据采集器应放在 `src/collectors/`，保持无状态、返回有类型的数据对象；不要把 API 密钥写入代码或 Prompt。

## 4. 加入目录和注册表

在 `src/core/agent_catalog.py` 完成两项修改：

1. 将名称加入 `WORKFLOW_AGENT_NAMES` 或 `CAPABILITY_AGENT_NAMES`。
2. 在 `AGENT_SEED_SPECS` 添加 `AgentSeedSpec`：

```python
AgentSeedSpec(
    name="risk_monitor",
    display_name="风险监测",
    description="识别自选股中的异常风险信号",
    enabled=False,
    schedule="*/10 9-15 * * 1-5",
    execution_mode="single",
    kind=AGENT_KIND_WORKFLOW,
    visible=True,
    display_order=40,
    config={"threshold": 3.0},
)
```

数字星期遵循 POSIX cron：`1-5` 表示周一至周五；也支持 `interval:30s`、`interval:5m`、`interval:1h`。用 `/api/agents/schedule/preview` 或 `preview_schedule()` 验证表达式。

随后在 `server.py` 导入实现并加入 `AGENT_REGISTRY`：

```python
from src.agents.risk_monitor import RiskMonitorAgent

AGENT_REGISTRY = {
    # ...
    "risk_monitor": RiskMonitorAgent,
}
```

仅写目录配置会在数据库中显示但无法执行；仅写注册表则不会被种子初始化、前端也不可配置。通用 `AgentConfig`/`StockAgent` 已能容纳新 Agent，通常不需要数据库迁移。

## 5. 配置同步与前端接入

服务启动时 `seed_agents()` 会创建缺失记录，并同步已有记录的名称、描述、类型、可见性、排序和执行模式。对已有 `workflow`，它会保留用户的 `enabled` 和 `schedule`；默认 `config` 只在原配置为空时写入。因此，后续新增配置键时需要显式兼容补齐，不能只修改 `AgentSeedSpec.config`。

新的可见 `workflow` 会自动出现在 `/api/agents` 和 Agent 页面，无需新增 React 卡片。只有以下情况才需要改前端或 API：专用扫描入口、特殊结果展示、建议来源中文映射或日志名称映射（例如 `frontend/src/lib/logger-map.ts`）。

运行前还必须在 UI 中：

1. 配置 AI 服务/模型和通知渠道。
2. 在 Agent 页面绑定至少一只股票。
3. 启用 Agent，并检查未来触发时间。

模型解析优先级为“股票级覆盖 → Agent 默认 → 系统默认”；通知渠道采用相同层级。股票绑定、模型和通知在每次执行时动态读取。

## 6. 测试与验收

在 `tests/test_risk_monitor.py` 至少覆盖：

- 无股票、采集失败和正常分析路径。
- Prompt 内容及结构化输出解析。
- `should_notify()` 的通知/静默分支。
- `single` 模式逐股执行、交易时段跳过、节流和上下文恢复。
- 构造参数与 `AgentSeedSpec.config` 一致，且名称同时存在于目录和注册表。

测试中 mock collector、AI client、notifier 和外部网络。建议执行：

```bash
python -m pytest
python -m compileall src/agents src/core server.py
cd frontend && pnpm build        # 仅在修改前端时需要
```

启动 `python server.py` 后完成验收：Agent 页面可见 → 绑定股票 → 手动触发 → 查看最近运行和日志 → 验证通知 → 验证一次计划任务。`requirements.txt` 当前未声明 `pytest`，全新环境需单独安装测试依赖。

## 7. 当前实现约束（新增时必须确认）

1. `PUT /api/agents/{name}` 不会调用 `reload_scheduler()`。股票绑定、模型和通知可动态生效，但 `enabled`、`schedule`、`execution_mode` 及调度实例中的构造配置需要重启服务（或显式重载调度器）后才可靠生效。
2. `StockAgent.schedule` 当前会被 API 保存并由前端展示，但调度器只读取全局 `AgentConfig.schedule`，尚未按股票分别调度。
3. `trigger_agent_for_stock()` 对普通 Agent 使用无参构造，只为 `intraday_monitor` 传入手动触发参数；新 Agent 若依赖 `config`，需要同步改造该实例化路径。全局手动触发和计划任务则会读取 `AgentConfig.config`。
4. `build_scheduler()` 在配置参数不匹配时会捕获 `TypeError` 并回退无参构造，而全局手动触发不会回退。应通过测试提前发现配置漂移，不要依赖静默回退。
5. `single` Agent 的 `run_single()` 绕过 `BaseAgent.run()` 标准通知流程；需自行保证静默时段、去重、通知失败记录及 `suppress_notify` 行为一致。

提交前按 `feat: add risk monitor agent` 格式提交，并在 PR 中说明默认开关、调度表达式、数据源、Prompt、通知策略、测试结果；涉及 UI 时附截图或 GIF，严禁提交 `.env`、API Key 或运行数据库。
