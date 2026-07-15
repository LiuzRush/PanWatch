"""策略评分合成测试。"""

from types import SimpleNamespace

from src.core.strategy_engine import _compute_factor_breakdown


def _candidate(**overrides):
    data = {
        "score": 90.0,
        "action": "buy",
        "is_holding_snapshot": False,
        "signal": "趋势延续，MACD金叉",
        "reason": "放量突破",
        "plan_quality": 100,
        "candidate_source": "market_scan",
        "source_agent": "",
        "entry_low": 9.8,
        "entry_high": 10.2,
        "status": "active",
        "meta": {
            "quote": {
                "change_pct": 3.0,
                "turnover": 2_000_000_000,
            },
            "kline": {
                "volume_ratio": 1.5,
            },
        },
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_high_quality_active_signal_does_not_saturate_to_100():
    """高基础分 + 多正向因子应保持区分度，而不是线性加法后直接顶满。"""
    out = _compute_factor_breakdown(
        row=_candidate(),
        strategy_code="trend_follow",
        weight=1.15,
        risk_level="medium",
        regime_info={"regime": "bullish", "confidence": 0.6},
        cross_feature={"relative_strength_pct": 85, "crowding_risk": 0},
        news_metric={"event_score": 4.0, "event_bias": 0.2, "news_count": 2},
        factor_weights=None,
    )

    assert 90.0 < out["weighted_score"] < 100.0
    assert out["raw_score"] < 100.0


def test_inactive_signal_is_capped_below_default_opportunity_threshold():
    """inactive 信号最高 69，避免混入默认 70+ 可执行机会。"""
    out = _compute_factor_breakdown(
        row=_candidate(status="inactive"),
        strategy_code="trend_follow",
        weight=1.15,
        risk_level="medium",
        regime_info={"regime": "bullish", "confidence": 0.6},
        cross_feature={"relative_strength_pct": 85, "crowding_risk": 0},
        news_metric={"event_score": 4.0, "event_bias": 0.2, "news_count": 2},
        factor_weights=None,
    )

    assert out["weighted_score"] <= 69.0
