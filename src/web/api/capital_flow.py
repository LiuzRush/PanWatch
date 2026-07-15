from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from src.collectors.capital_flow_collector import CapitalFlowCollector
from src.models.market import MarketCode

router = APIRouter()


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode((market or "CN").upper())
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _parse_days(days: int) -> int:
    try:
        safe_days = int(days)
    except (TypeError, ValueError):
        raise HTTPException(400, "days 必须是整数")
    if safe_days < 1 or safe_days > 5000:
        raise HTTPException(400, "days 必须在 1-5000 之间")
    return safe_days


@router.get("/{symbol}/history")
def get_capital_flow_history(symbol: str, market: str = "CN", days: int = 60):
    """获取单只股票日级历史资金流向。"""
    market_code = _parse_market(market)
    safe_days = _parse_days(days)
    collector = CapitalFlowCollector(market_code)
    items = collector.get_capital_flow_history(symbol, days=safe_days)
    if not items:
        raise HTTPException(404, "历史资金流向不存在")
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": safe_days,
        "items": [asdict(item) for item in items],
    }
