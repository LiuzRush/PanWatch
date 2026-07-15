from fastapi import HTTPException

from src.collectors import capital_flow_collector
from src.models.market import MarketCode


def _fake_flow_payload():
    return {
        "data": {
            "code": "600519",
            "name": "贵州茅台",
            "klines": [
                "2026-07-10,100,10,20,30,40,1,0.1,0.2,0.3,0.4,1700,0.5,1000,2000",
                "2026-07-13,-50,-5,-10,-15,-20,-0.5,-0.1,-0.2,-0.3,-0.4,1690,-0.6,900,1800",
            ],
        }
    }


def test_capital_flow_history_parser(monkeypatch):
    """个股历史资金流应解析完整日级序列。"""
    monkeypatch.setattr(
        capital_flow_collector, "market_get", lambda *args, **kwargs: _fake_flow_payload()
    )

    collector = capital_flow_collector.CapitalFlowCollector(MarketCode.CN)
    items = collector.get_capital_flow_history("600519", days=2)

    assert len(items) == 2
    assert items[0].date == "2026-07-10"
    assert items[0].main_net_inflow == 100
    assert items[0].super_net_inflow == 40
    assert items[0].amount == 2000
    assert items[1].main_net_inflow == -50


def test_capital_flow_history_endpoint(monkeypatch):
    """/api/capital-flow/{symbol}/history 返回序列化后的资金流。"""
    from src.web.api import capital_flow

    monkeypatch.setattr(
        capital_flow_collector, "market_get", lambda *args, **kwargs: _fake_flow_payload()
    )

    res = capital_flow.get_capital_flow_history("600519", market="CN", days=2)
    assert res["symbol"] == "600519"
    assert res["market"] == "CN"
    assert res["items"][0]["date"] == "2026-07-10"
    assert res["items"][0]["main_net_inflow_pct"] == 1


def test_capital_flow_history_invalid_days_returns_400():
    """days 越界应返回 HTTP 400。"""
    from src.web.api import capital_flow

    try:
        capital_flow.get_capital_flow_history("600519", market="CN", days=0)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected HTTPException")


def test_capital_flow_router_mounted():
    """/api/capital-flow/{symbol}/history 已挂载到 app。"""
    from src.web.app import app

    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/capital-flow/{symbol}/history" in paths
