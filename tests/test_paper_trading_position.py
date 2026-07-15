"""模拟盘仓位管理(Phase 1)单元测试 —— 纯函数,不触发 DB/网络。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.backtest.cost_model import CostModel
from src.core.paper_trading_engine import (
    _compute_quantity,
    _is_t1_locked,
    _is_trading_time,
    _position_weight,
)


def test_position_weight_tiers():
    """信号强度越高,单笔资金占比越大(分档)。"""
    assert _position_weight(90) == 0.25
    assert _position_weight(80) == 0.18
    assert _position_weight(70) == 0.12
    assert _position_weight(50) == 0.08
    assert _position_weight(90) > _position_weight(50)


def test_compute_quantity_respects_budget():
    """按市场预算 × 强度比例分配,买入 100 股整数倍且不超预算对应股数。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=1_000_000, cost_model=cm,
    )
    assert qty > 0 and qty % 100 == 0
    assert qty <= 25000  # 25% 预算 / 10 元


def test_compute_quantity_respects_cash():
    """可用现金不足时回退到买得起的手数,买入含费不超现金。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=10.0,
        available_cash=3000, cost_model=cm,
    )
    assert qty % 100 == 0
    if qty > 0:
        outlay = -cm.fill("buy", 10.0, qty).cash_delta
        assert outlay <= 3000


def test_compute_quantity_insufficient_cash_returns_zero():
    """现金连最小一手都买不起时返回 0(应跳过建仓)。"""
    cm = CostModel()
    qty = _compute_quantity(
        rank_score=90, market_budget=1_000_000, price=100.0,
        available_cash=500, cost_model=cm,
    )
    assert qty == 0


def test_engine_imports_ok():
    """改造后 paper_trading_engine 可正常导入(无语法/循环 import 错),关键符号在位。"""
    import src.core.paper_trading_engine as e

    assert hasattr(e, "ENGINE")
    assert hasattr(e, "COST_MODEL")
    assert hasattr(e, "_compute_quantity")


def test_market_trading_time_is_market_specific():
    """交易时段按市场本地时间判断。"""
    # 2026-07-14 02:00 UTC = 上海/香港 10:00，A 股/港股盘中。
    assert _is_trading_time("CN", datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc))
    assert _is_trading_time("HK", datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc))
    # 2026-07-14 04:00 UTC = 上海 12:00，A 股午休。
    assert not _is_trading_time("CN", datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc))


def test_a_share_t1_lock_same_market_date_only():
    """A 股当日开仓当日不可卖出，隔日解除；非 A 股不套用 T+1。"""
    opened = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)
    same_cn_day = datetime(2026, 7, 14, 6, 0, tzinfo=timezone.utc)
    next_cn_day = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    cn_pos = SimpleNamespace(stock_market="CN", opened_at=opened)
    hk_pos = SimpleNamespace(stock_market="HK", opened_at=opened)

    assert _is_t1_locked(cn_pos, now=same_cn_day)
    assert not _is_t1_locked(cn_pos, now=next_cn_day)
    assert not _is_t1_locked(hk_pos, now=same_cn_day)
