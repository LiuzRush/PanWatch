"""毛毛虫 Agent - 日内做 T 差价，积累利润垫而不堆高仓位。

定位与 intraday_monitor 互补：盘中监测负责「广谱信号报警」，
毛毛虫负责「日复一日做差价」——开盘看竞价决定是否低吸，上涨途中综合涨速、
资金流入流出、大盘与板块情绪判断是否高抛 T 出锁差价，回落时轻仓撤退。

特点：
- 单只模式 (single)：逐只 run_single，每只独立分析与通知。
- 状态连续：用 caterpillar_state 记录做 T 腿位与当日利润垫，让模型连续决策。
- 通知降噪：仅在出现可执行动作（低吸/高抛/撤退）时推送，并按个股节流。
"""

import json
import logging
import re
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.akshare_collector import AkshareCollector
from src.collectors.discovery_collector import EastMoneyDiscoveryCollector
from src.core.analysis_history import get_latest_analysis, get_analysis
from src.core.context_builder import ContextBuilder
from src.core.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.core import caterpillar_state
from src.core.suggestion_pool import save_suggestion
from src.core.signals import SignalPackBuilder
from src.models.market import MarketCode, StockData, IndexData, MARKETS

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """按市场判断是否在交易时段。"""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    if market == MarketCode.CN:
        return "A股"
    if market == MarketCode.HK:
        return "港股"
    if market == MarketCode.US:
        return "美股"
    return market.value


# 毛毛虫做 T 动作 -> 标准 action（写入建议池/回测）
T_ACTION_TO_ACTION = {
    "t_buy_low": "buy",
    "t_buy_back": "buy",
    "t_sell_high": "reduce",
    "retreat": "reduce",
    "hold": "hold",
    "watch": "watch",
}

# 毛毛虫动作 -> 默认中文标签
T_ACTION_LABELS = {
    "t_buy_low": "低吸",
    "t_buy_back": "买回底仓",
    "t_sell_high": "高抛T出",
    "retreat": "轻仓撤退",
    "hold": "持T等待",
    "watch": "空仓观望",
}

# 触发推送的可执行动作
ALERT_T_ACTIONS = {"t_buy_low", "t_buy_back", "t_sell_high", "retreat"}

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "caterpillar.txt"


class CaterpillarAgent(BaseAgent):
    """毛毛虫做 T Agent"""

    name = "caterpillar"
    display_name = "毛毛虫做T"
    description = "高频日内做差价，结合竞价/涨速/资金/大盘/板块判断高抛低吸，积累利润垫"

    def __init__(
        self,
        throttle_minutes: int = 15,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        price_alert_threshold: float = 1.0,
        volume_alert_ratio: float = 1.5,
        include_boards: bool = True,
    ):
        """
        Args:
            throttle_minutes: 同一股票通知间隔（分钟）
            bypass_throttle: 是否跳过节流（测试用）
            bypass_market_hours: 是否跳过交易时段门禁（仅手动分析场景）
            price_alert_threshold: 做 T 关注的短时涨跌幅阈值（%）
            volume_alert_ratio: 量比关注阈值
            include_boards: 是否采集板块情绪（行业涨幅榜）
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.include_boards = include_boards

    async def collect(self, context: AgentContext) -> dict:
        """采集实时行情 + K线 + 资金 + 大盘 + 板块 + 做 T 状态。"""
        if not context.watchlist:
            logger.warning("自选股列表为空，跳过毛毛虫做 T")
            return {"stocks": [], "stock_data": None}

        stock_config = context.watchlist[0]
        market = stock_config.market
        symbol = stock_config.symbol
        name = stock_config.name or symbol

        # 按个股所属市场做交易时段门禁
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = f"当前{market_label(market)}非交易时段，已跳过执行"
            logger.info(f"{msg}: {symbol}")
            return {"stocks": [], "stock_data": None, "skip_reason": msg}

        # 统一结构化输入（quote/technical/capital_flow）
        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=True,
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None
        kline_summary = pack.technical if pack else None

        # 大盘指数（个股所属市场）
        index_list: list[IndexData] = []
        try:
            index_list = await AkshareCollector(market).get_index_data()
        except Exception as e:
            logger.warning(f"获取 {market.value} 大盘指数失败: {e}")

        # 板块情绪（行业涨幅榜，仅 A 股，尽力而为）
        hot_boards = []
        if self.include_boards and market == MarketCode.CN:
            try:
                collector = EastMoneyDiscoveryCollector()
                boards = await collector.fetch_hot_boards(
                    market="CN", mode="gainers", limit=8
                )
                hot_boards = [
                    {"name": b.name, "change_pct": b.change_pct} for b in boards
                ]
            except Exception as e:
                logger.debug(f"获取板块情绪失败，忽略: {e}")

        # 个股所属板块整体表现（仅 A 股，尽力而为）
        stock_sector = None
        if self.include_boards and market == MarketCode.CN:
            try:
                stock_sector = await AkshareCollector(market).get_stock_sector(symbol)
            except Exception as e:
                logger.debug(f"获取个股板块表现失败，忽略: {e}")

        # 做 T 状态（腿位 + 利润垫）
        t_state = caterpillar_state.load_state(symbol)

        # 历史分析上下文
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "index_list": index_list,
            "hot_boards": hot_boards,
            "stock_sector": stock_sector,
            "t_state": t_state,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建毛毛虫做 T Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "无股票数据"

        lines: list[str] = []
        lines.append(f"## 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # 股票行情
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## 股票行情")
        lines.append(f"- 股票：{stock.name}（{stock.symbol}｜{market_label(stock.market)}）")
        lines.append(f"- 现价：{current_price:.2f}")
        lines.append(f"- 涨跌幅：{change_pct:+.2f}%")
        lines.append(f"- 今开：{open_price:.2f}")
        lines.append(f"- 最高：{high_price:.2f}")
        lines.append(f"- 最低：{low_price:.2f}")
        lines.append(f"- 昨收：{prev_close:.2f}")
        if turnover > 0:
            lines.append(f"- 成交额：{turnover / 10000:.0f} 万")

        # 涨速/位置（做 T 关键）
        lines.append("\n## 涨速与日内位置")
        if open_price > 0:
            from_open = (current_price - open_price) / open_price * 100
            lines.append(f"- 相对今开：{from_open:+.2f}%")
        day_range = high_price - low_price
        if day_range > 0 and prev_close > 0:
            pos = (current_price - low_price) / day_range * 100
            lines.append(
                f"- 日内振幅：{day_range / prev_close * 100:.2f}%（现价位于区间 {pos:.0f}% 分位，0=最低/100=最高）"
            )
        lines.append(
            f"- 关注阈值：短时涨跌幅 ≥ {self.price_alert_threshold:.1f}% / 量比 ≥ {self.volume_alert_ratio:.1f}"
        )

        # 技术分析
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## 技术分析")
            lines.append(f"- 趋势：{kline.get('trend', 'N/A')}")
            lines.append(
                f"- 5日涨幅：{format_num(kline.get('change_5d'))}% | 20日涨幅：{format_num(kline.get('change_20d'))}%"
            )
            volume_ratio = kline.get("volume_ratio")
            volume_trend = kline.get("volume_trend")
            if volume_trend:
                vr = f"（量比={volume_ratio:.2f}）" if volume_ratio else ""
                lines.append(f"- 量能：{volume_trend}{vr}")
            lines.append(
                f"- MA5：{format_num(kline.get('ma5'))} | MA10：{format_num(kline.get('ma10'))} | MA20：{format_num(kline.get('ma20'))}"
            )
            macd_info = f"MACD：{kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f"（{kline.get('macd_cross_days')}日前）"
            lines.append(f"- {macd_info}")
            rsi6 = kline.get("rsi6")
            if rsi6 is not None:
                lines.append(f"- RSI(6)：{rsi6:.1f}（{kline.get('rsi_status', '')}）")
            kdj_status = kline.get("kdj_status")
            if kdj_status:
                lines.append(f"- KDJ：{kdj_status}")
            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- 短期支撑：{format_num(support_s)} | 短期压力：{format_num(resistance_s)}"
                )

        # 资金面（仅 A 股，若可用）
        pack = data.get("signal_pack")
        flow = getattr(pack, "capital_flow", None) if pack else None
        if isinstance(flow, dict) and flow and not flow.get("error") and flow.get("status"):
            try:
                inflow = float(flow.get("main_net_inflow") or 0)
                inflow_pct = float(flow.get("main_net_inflow_pct") or 0)
                inflow_str = (
                    f"{inflow / 1e8:+.2f}亿"
                    if abs(inflow) >= 1e8
                    else f"{inflow / 1e4:+.0f}万"
                )
                lines.append("\n## 资金面")
                lines.append(
                    f"- 资金：{flow.get('status')}，主力净流入{inflow_str}（{inflow_pct:+.1f}%）"
                )

                # 大小单结构：超大/大单=主力真实承接，仅小单流入需警惕拉高出货/诱多
                def _flow_str(v):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return None
                    return (
                        f"{v / 1e8:+.2f}亿" if abs(v) >= 1e8 else f"{v / 1e4:+.0f}万"
                    )

                parts = []
                for label, key in (
                    ("超大单", "super_net_inflow"),
                    ("大单", "big_net_inflow"),
                    ("中单", "mid_net_inflow"),
                    ("小单", "small_net_inflow"),
                ):
                    s = _flow_str(flow.get(key))
                    if s is not None:
                        parts.append(f"{label}{s}")
                if parts:
                    lines.append("- 大小单：" + "，".join(parts))

                if flow.get("trend_5d") and flow.get("trend_5d") != "无数据":
                    lines.append(f"- 5日资金：{flow.get('trend_5d')}")
            except Exception:
                pass

        # 大盘
        index_list = data.get("index_list") or []
        if index_list:
            lines.append("\n## 大盘")
            for idx in index_list[:3]:
                lines.append(f"- {idx.name}：{idx.current_price:.2f}（{idx.change_pct:+.2f}%）")

        # 个股所属板块整体表现（判断独强 / 随板块 / 逆板块）
        sector = data.get("stock_sector") or {}
        if sector and sector.get("name"):
            lines.append("\n## 个股所属板块")
            cp = sector.get("change_pct")
            rank = sector.get("rank")
            total = sector.get("total")
            head = f"- 所属板块：{sector.get('name')}"
            if isinstance(cp, (int, float)):
                head += f" {cp:+.2f}%"
            if rank and total:
                head += f"（行业榜 {rank}/{total}）"
            lines.append(head)
            up, down = sector.get("up_count"), sector.get("down_count")
            if isinstance(up, int) and isinstance(down, int):
                lines.append(f"- 板块涨跌家数：{up} 涨 / {down} 跌")
            # 个股 vs 板块强弱对比，供判断是否独强/逆势
            if isinstance(cp, (int, float)):
                diff = change_pct - cp
                if diff >= 0.8:
                    rel = "强于板块（个股独强，注意是否透支需高抛）"
                elif diff <= -0.8:
                    rel = "弱于板块（个股掉队，低吸需谨慎）"
                else:
                    rel = "与板块同步"
                lines.append(f"- 相对板块：{rel}")

        # 板块情绪
        hot_boards = data.get("hot_boards") or []
        if hot_boards:
            lines.append("\n## 板块情绪（行业涨幅榜 Top）")
            tops = []
            for b in hot_boards[:6]:
                cp = b.get("change_pct")
                cp_str = f"{cp:+.2f}%" if isinstance(cp, (int, float)) else "N/A"
                tops.append(f"{b.get('name')} {cp_str}")
            lines.append("- " + "； ".join(tops))

        # 当前做 T 腿位（仅用于约束动作方向，避免重复高抛/重复低吸；不做利润垫叙事）
        t_state = data.get("t_state") or {}
        lines.append("\n## 当前做 T 腿位")
        leg = t_state.get("t_leg", "flat")
        lines.append(f"- 腿位：{caterpillar_state.leg_label(leg)}")
        if t_state.get("entry_price"):
            lines.append(
                f"- 未平腿入场价：{t_state['entry_price']:.2f}（{t_state.get('entry_time', '')}）"
            )
            if current_price and t_state["entry_price"]:
                if leg == caterpillar_state.LEG_LONG:
                    float_pct = (current_price - t_state["entry_price"]) / t_state["entry_price"] * 100
                    lines.append(f"- 该腿浮动：{float_pct:+.2f}%（待高抛兑现差价）")
                elif leg == caterpillar_state.LEG_SHORT:
                    float_pct = (t_state["entry_price"] - current_price) / t_state["entry_price"] * 100
                    lines.append(
                        f"- 该腿浮动：{float_pct:+.2f}%（已高抛，待企稳后低吸买回——回调≠企稳，未确认前不买回）"
                    )

        # 持仓情况
        positions = context.portfolio.get_positions_for_stock(stock.symbol)
        if positions:
            agg = context.portfolio.get_aggregated_position(stock.symbol)
            if agg:
                avg_cost = agg.get("avg_cost") or 0
                pnl_pct = (
                    (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
                )
                lines.append("\n## 底仓情况")
                lines.append(f"- 持仓量：{agg.get('total_quantity')} 股，成本 {avg_cost:.2f}")
                lines.append(f"- 浮动盈亏：{pnl_pct:+.1f}%")
        else:
            lines.append("\n## 底仓情况")
            lines.append("- 当前未持底仓（A股做T需自备底仓；可考虑竞价/低吸建底仓后再做T）")

        # 历史分析参考
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")
        if daily_analysis or premarket_analysis:
            lines.append("\n## 历史分析参考")
            if premarket_analysis:
                content = premarket_analysis[:200] + "..." if len(premarket_analysis) > 200 else premarket_analysis
                lines.append(f"### 今日盘前\n{content}")
            if daily_analysis:
                content = daily_analysis[:200] + "..." if len(daily_analysis) > 200 else daily_analysis
                lines.append(f"### 昨日复盘\n{content}")

        lines.append(
            "\n请严格依据当前做 T 腿位约束，判断阶段并给出做 T 动作，只输出一个 JSON 对象。"
        )

        return system_prompt, "\n".join(lines)

    def _parse_suggestion(self, content: str) -> dict:
        """从 AI 响应解析毛毛虫做 T 建议。"""
        result = {
            "t_action": "watch",
            "t_action_label": "空仓观望",
            "action": "watch",
            "action_label": "观望",
            "phase": "",
            "rise_speed": "",
            "position_hint": "",
            "spread_target": "",
            "signal": "",
            "reason": "",
            "triggers": [],
            "invalidations": [],
            "risks": [],
            "should_alert": False,
        }

        obj = self._try_parse_loose_json(content)
        if not obj:
            # 兜底：无法解析时观望，不推送
            result["reason"] = re.sub(r"\s+", " ", (content or "").strip())[:120]
            return result

        t_action = (obj.get("t_action") or "watch").strip()
        if t_action not in T_ACTION_TO_ACTION:
            t_action = "watch"
        result["t_action"] = t_action
        result["t_action_label"] = (
            obj.get("t_action_label") or T_ACTION_LABELS.get(t_action, "")
        ).strip()[:20]
        # 标准 action 以映射为准，避免模型给出非法值
        result["action"] = T_ACTION_TO_ACTION[t_action]
        result["action_label"] = result["t_action_label"]
        result["phase"] = (obj.get("phase") or "").strip()[:20]
        result["rise_speed"] = (obj.get("rise_speed") or "").strip()[:12]
        result["position_hint"] = (obj.get("position_hint") or "").strip()[:30]
        result["spread_target"] = (obj.get("spread_target") or "").strip()[:20]
        result["signal"] = (obj.get("signal") or "").strip()[:60]
        result["reason"] = (obj.get("reason") or "").strip()[:160]
        for key in ("triggers", "invalidations", "risks"):
            val = obj.get(key)
            result[key] = [str(x)[:40] for x in val][:3] if isinstance(val, list) else []
        result["should_alert"] = t_action in ALERT_T_ACTIONS
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """宽松解析 JSON 输出，兜底兼容模型异常格式。"""
        raw = (text or "").strip()
        if not raw:
            return None
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None
        if not isinstance(obj, dict):
            return None
        keys = {"t_action", "action", "phase", "signal", "reason"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, t_state: dict
    ) -> str:
        """生成可读通知文案。"""
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        phase = suggestion.get("phase") or "盘中"
        label = suggestion.get("t_action_label") or "观望"
        pos = suggestion.get("position_hint") or ""
        target = suggestion.get("spread_target") or ""
        action_line = f"阶段：{phase} ｜ 动作：{label}"
        extra = "、".join([x for x in (pos, f"目标差价 {target}" if target and target != "-" else "") if x])
        if extra:
            action_line += f"（{extra}）"

        lines = [
            f"{stock.name}（{stock.symbol}）  现价 {price}  {chg}",
            action_line,
        ]
        if suggestion.get("signal"):
            lines.append(f"信号：{suggestion['signal']}")
        if suggestion.get("reason"):
            lines.append(f"理由：{suggestion['reason']}")
        if suggestion.get("triggers"):
            lines.append("触发：" + "；".join(suggestion["triggers"][:2]))
        if suggestion.get("risks"):
            lines.append("风险：" + "；".join(suggestion["risks"][:2]))
        lines.append("——以上仅供参考，不构成投资建议")
        return "\n".join(lines)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI 分析、推进做 T 状态并判断是否推送。"""
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】跳过",
                content=data.get("skip_reason", "跳过执行"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】无数据",
                content="未获取到股票数据",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)
        logger.info(f"=== Caterpillar Prompt for {stock.symbol} ===\n{user_content}")

        raw_content = await context.ai_client.chat(system_prompt, user_content)
        logger.info(f"=== Caterpillar Response for {stock.symbol} ===\n{raw_content}")

        suggestion = self._parse_suggestion(raw_content)

        # 推进做 T 腿位与利润垫（仅可执行动作改变状态）
        try:
            t_state = caterpillar_state.apply_action(
                stock.symbol,
                suggestion.get("t_action"),
                getattr(stock, "current_price", None),
            )
        except Exception as e:
            logger.warning(f"caterpillar 状态更新失败: {e}")
            t_state = data.get("t_state") or {}

        content = self._format_human_readable_content(stock, suggestion, t_state)

        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        quality_score = (
            (data.get("symbol_context") or {}).get("data_quality", {}).get("score")
        )

        # 写入建议池（标准 action，毛毛虫语义入 meta）
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=4,  # 做 T 建议时效更短
            prompt_context=user_content,
            ai_response=raw_content,
            stock_market=stock.market.value,
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "caterpillar": {
                    "t_action": suggestion.get("t_action"),
                    "t_action_label": suggestion.get("t_action_label"),
                    "phase": suggestion.get("phase"),
                    "rise_speed": suggestion.get("rise_speed"),
                    "position_hint": suggestion.get("position_hint"),
                    "spread_target": suggestion.get("spread_target"),
                    "t_leg": t_state.get("t_leg"),
                    "cushion_pct": t_state.get("cushion_pct"),
                    "cycles": t_state.get("cycles"),
                },
                "analysis_date": analysis_date,
                "context_quality_score": quality_score,
                "plan": {
                    "triggers": suggestion.get("triggers", []),
                    "invalidations": suggestion.get("invalidations", []),
                    "risks": suggestion.get("risks", []),
                },
            },
        )

        for horizon in (1, 5):
            save_agent_prediction_outcome(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                stock_market=stock.market.value,
                prediction_date=analysis_date,
                horizon_days=horizon,
                action=suggestion.get("action") or "watch",
                action_label=suggestion.get("action_label") or "观望",
                confidence=(float(quality_score) / 100.0)
                if quality_score is not None
                else None,
                trigger_price=getattr(stock, "current_price", None),
                meta={
                    "source": self.name,
                    "t_action": suggestion.get("t_action"),
                    "reason": suggestion.get("reason", ""),
                    "signal": suggestion.get("signal", ""),
                },
            )

        save_agent_context_run(
            agent_name=self.name,
            stock_symbol=stock.symbol,
            analysis_date=analysis_date,
            context_payload={
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                "t_state": t_state,
            },
            quality={"score": quality_score or 0},
        )

        title = f"【{self.display_name}】{stock.name} {stock.change_pct:+.2f}% {suggestion.get('t_action_label', '')}"
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "t_state": t_state,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """仅在出现可执行做 T 动作且未被节流时推送。"""
        if result.raw_data.get("skipped"):
            return False
        if not result.raw_data.get("should_alert", False):
            logger.info(
                f"毛毛虫无可执行动作，静默: {result.raw_data.get('stock', {}).get('symbol')}"
            )
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data or not stock_data.get("symbol"):
            return False
        symbol = stock_data["symbol"]

        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(f"通知节流: {symbol} 在 {self.throttle_minutes} 分钟内已通知")
                return False
        return True

    def _check_throttle(self, symbol: str) -> bool:
        """检查是否可以发送通知（未被节流）。"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )
            if not record:
                return True
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """更新节流记录。"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                if record.last_notify_at and record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count = (record.notify_count or 0) + 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )
            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """单只模式执行：只分析指定股票，独立分析与通知。"""
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]
        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if data.get("skip_reason"):
                return AnalysisResult(
                    agent_name=self.name,
                    title=f"【{self.display_name}】跳过",
                    content=data["skip_reason"],
                    raw_data={"skipped": True, **data},
                )
            if not data.get("stock_data"):
                return None

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title, result.content, result.images
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(f"Agent [{self.display_name}] 通知已发送: {stock_symbol}")
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
                else:
                    result.raw_data["notify_error"] = notify_result.get("error") or "未知错误"
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
