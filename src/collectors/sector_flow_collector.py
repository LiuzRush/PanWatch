"""行业/概念板块资金流向采集器 - 基于东方财富 API。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.collectors.market_http import TTLCache, market_get, source_suffix

logger = logging.getLogger(__name__)

EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
EASTMONEY_CLIST_FALLBACK_URL = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
)
EASTMONEY_FLOW_FALLBACK_URL = (
    "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"
)

_SECTOR_FLOW_CACHE = TTLCache(default_ttl_sec=300.0)
_CLIST_HOST = "push2.eastmoney.com"
_FLOW_HOST = "push2his.eastmoney.com"
_MIN_INTERVAL_S = 0.2

_FLOW_FIELDS = "f12,f14,f2,f3,f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"


@dataclass(frozen=True)
class SectorMeta:
    code: str
    name: str
    type: str
    market: str = "CN"
    source: str = "eastmoney"


@dataclass(frozen=True)
class SectorFlow:
    code: str
    name: str
    type: str
    market: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    main_net_inflow: float | None
    main_net_inflow_pct: float | None
    super_net_inflow: float | None
    super_net_inflow_pct: float | None
    big_net_inflow: float | None
    big_net_inflow_pct: float | None
    mid_net_inflow: float | None
    mid_net_inflow_pct: float | None
    small_net_inflow: float | None
    small_net_inflow_pct: float | None
    updated_at: int


@dataclass(frozen=True)
class SectorStockFlow:
    symbol: str
    name: str
    market: str
    board_code: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    main_net_inflow: float | None
    main_net_inflow_pct: float | None
    super_net_inflow: float | None
    super_net_inflow_pct: float | None
    big_net_inflow: float | None
    big_net_inflow_pct: float | None
    mid_net_inflow: float | None
    mid_net_inflow_pct: float | None
    small_net_inflow: float | None
    small_net_inflow_pct: float | None
    updated_at: int


@dataclass(frozen=True)
class SectorFlowHistoryItem:
    date: str
    main_net_inflow: float
    small_net_inflow: float
    mid_net_inflow: float
    big_net_inflow: float
    super_net_inflow: float
    main_net_inflow_pct: float
    small_net_inflow_pct: float
    mid_net_inflow_pct: float
    big_net_inflow_pct: float
    super_net_inflow_pct: float
    close: float
    change_pct: float
    volume: float
    amount: float


def _safe_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_flow_float(value) -> float:
    parsed = _safe_float(value)
    return parsed if parsed is not None else 0.0


def _board_fs(sector_type: str) -> str:
    return "m:90+t:3" if sector_type == "concept" else "m:90+t:2"


def _parse_history_line(line: str) -> SectorFlowHistoryItem | None:
    parts = (line or "").split(",")
    if len(parts) < 13:
        return None
    return SectorFlowHistoryItem(
        date=str(parts[0]),
        main_net_inflow=_safe_flow_float(parts[1]),
        small_net_inflow=_safe_flow_float(parts[2]),
        mid_net_inflow=_safe_flow_float(parts[3]),
        big_net_inflow=_safe_flow_float(parts[4]),
        super_net_inflow=_safe_flow_float(parts[5]),
        main_net_inflow_pct=_safe_flow_float(parts[6]),
        small_net_inflow_pct=_safe_flow_float(parts[7]),
        mid_net_inflow_pct=_safe_flow_float(parts[8]),
        big_net_inflow_pct=_safe_flow_float(parts[9]),
        super_net_inflow_pct=_safe_flow_float(parts[10]),
        close=_safe_flow_float(parts[11]),
        change_pct=_safe_flow_float(parts[12]),
        volume=_safe_flow_float(parts[13]) if len(parts) > 13 else 0.0,
        amount=_safe_flow_float(parts[14]) if len(parts) > 14 else 0.0,
    )


class EastMoneySectorFlowCollector:
    """行业/概念资金流向采集器。"""

    def __init__(self, *, timeout_s: float = 10.0, verify_ssl: bool = False):
        self.timeout_s = float(timeout_s)
        self.verify_ssl = bool(verify_ssl)

    def fetch_sector_flows(
        self,
        *,
        sector_type: str = "industry",
        mode: str = "main_net",
        limit: int = 100,
    ) -> list[SectorFlow]:
        sector_type = "concept" if sector_type == "concept" else "industry"
        safe_limit = max(1, min(int(limit or 100), 500))
        fid = "f6" if mode == "turnover" else "f62"
        cache_key = f"sector_flows:{sector_type}:{mode}:{safe_limit}"
        cached = _SECTOR_FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "pn": 1,
            "pz": safe_limit,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": _board_fs(sector_type),
            "fields": _FLOW_FIELDS,
            "_": int(time.time() * 1000),
        }
        data = self._get_clist(params=params, log_label="板块资金流排行")
        diff = ((data or {}).get("data") or {}).get("diff") or []
        now = int(time.time())
        out = [self._parse_sector_flow(it, sector_type=sector_type, updated_at=now) for it in diff]
        out = [it for it in out if it is not None]
        _SECTOR_FLOW_CACHE.set(cache_key, out, ttl_sec=60.0)
        return out

    def fetch_sectors(
        self, *, sector_type: str = "industry", keyword: str = "", limit: int = 500
    ) -> list[SectorMeta]:
        sector_type = "concept" if sector_type == "concept" else "industry"
        items = self.fetch_sector_flows(
            sector_type=sector_type, mode="turnover", limit=limit
        )
        kw = (keyword or "").strip().lower()
        out = [
            SectorMeta(code=it.code, name=it.name, type=sector_type)
            for it in items
            if not kw or kw in it.code.lower() or kw in it.name.lower()
        ]
        return out

    def fetch_sector_stock_flows(
        self,
        *,
        board_code: str,
        sector_type: str = "industry",
        limit: int = 100,
    ) -> list[SectorStockFlow]:
        code = (board_code or "").strip()
        if not code:
            return []
        safe_limit = max(1, min(int(limit or 100), 500))
        cache_key = f"sector_stock_flows:{sector_type}:{code}:{safe_limit}"
        cached = _SECTOR_FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "pn": 1,
            "pz": safe_limit,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f62",
            "fs": f"b:{code}",
            "fields": _FLOW_FIELDS,
            "_": int(time.time() * 1000),
        }
        data = self._get_clist(params=params, log_label="板块成分股资金流")
        diff = ((data or {}).get("data") or {}).get("diff") or []
        now = int(time.time())
        out = [
            self._parse_sector_stock_flow(it, board_code=code, updated_at=now)
            for it in diff
        ]
        out = [it for it in out if it is not None]
        _SECTOR_FLOW_CACHE.set(cache_key, out, ttl_sec=60.0)
        return out

    def fetch_sector_flow_history(
        self, *, board_code: str, days: int = 60
    ) -> list[SectorFlowHistoryItem]:
        code = (board_code or "").strip()
        if not code:
            return []
        safe_days = max(1, min(int(days or 60), 5000))
        cache_key = f"sector_flow_history:{code}:{safe_days}"
        cached = _SECTOR_FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "lmt": safe_days,
            "klt": "101",
            "secid": f"90.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "_": int(time.time() * 1000),
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        }
        data = market_get(
            EASTMONEY_FLOW_URL,
            host_key=_FLOW_HOST,
            fallback_urls=(EASTMONEY_FLOW_FALLBACK_URL,),
            params=params,
            headers=headers,
            min_interval_s=_MIN_INTERVAL_S,
            timeout=self.timeout_s,
            retries=2,
            parse="json",
            symbol=code,
            log_label="板块历史资金流",
            verify=self.verify_ssl,
        )
        try:
            klines = (((data or {}).get("data") or {}).get("klines")) or []
            out = []
            for line in klines[-safe_days:]:
                item = _parse_history_line(line)
                if item is not None:
                    out.append(item)
            _SECTOR_FLOW_CACHE.set(cache_key, out)
            return out
        except Exception as e:
            logger.error(f"解析 {code} 板块历史资金流失败: {e}{source_suffix()}")
            return []

    def _get_clist(self, *, params: dict, log_label: str) -> dict | None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        return market_get(
            EASTMONEY_CLIST_URL,
            host_key=_CLIST_HOST,
            fallback_urls=(EASTMONEY_CLIST_FALLBACK_URL,),
            params=params,
            headers=headers,
            min_interval_s=_MIN_INTERVAL_S,
            timeout=self.timeout_s,
            retries=2,
            parse="json",
            log_label=log_label,
            verify=self.verify_ssl,
        )

    @staticmethod
    def _parse_sector_flow(
        it: dict, *, sector_type: str, updated_at: int
    ) -> SectorFlow | None:
        code = str(it.get("f12") or "").strip()
        name = str(it.get("f14") or "").strip()
        if not code or not name:
            return None
        return SectorFlow(
            code=code,
            name=name,
            type=sector_type,
            market="CN",
            price=_safe_float(it.get("f2")),
            change_pct=_safe_float(it.get("f3")),
            turnover=_safe_float(it.get("f6")),
            main_net_inflow=_safe_float(it.get("f62")),
            main_net_inflow_pct=_safe_float(it.get("f184")),
            super_net_inflow=_safe_float(it.get("f66")),
            super_net_inflow_pct=_safe_float(it.get("f69")),
            big_net_inflow=_safe_float(it.get("f72")),
            big_net_inflow_pct=_safe_float(it.get("f75")),
            mid_net_inflow=_safe_float(it.get("f78")),
            mid_net_inflow_pct=_safe_float(it.get("f81")),
            small_net_inflow=_safe_float(it.get("f84")),
            small_net_inflow_pct=_safe_float(it.get("f87")),
            updated_at=updated_at,
        )

    @staticmethod
    def _parse_sector_stock_flow(
        it: dict, *, board_code: str, updated_at: int
    ) -> SectorStockFlow | None:
        symbol = str(it.get("f12") or "").strip()
        name = str(it.get("f14") or "").strip()
        if not symbol or not name:
            return None
        return SectorStockFlow(
            symbol=symbol,
            name=name,
            market="CN",
            board_code=board_code,
            price=_safe_float(it.get("f2")),
            change_pct=_safe_float(it.get("f3")),
            turnover=_safe_float(it.get("f6")),
            main_net_inflow=_safe_float(it.get("f62")),
            main_net_inflow_pct=_safe_float(it.get("f184")),
            super_net_inflow=_safe_float(it.get("f66")),
            super_net_inflow_pct=_safe_float(it.get("f69")),
            big_net_inflow=_safe_float(it.get("f72")),
            big_net_inflow_pct=_safe_float(it.get("f75")),
            mid_net_inflow=_safe_float(it.get("f78")),
            mid_net_inflow_pct=_safe_float(it.get("f81")),
            small_net_inflow=_safe_float(it.get("f84")),
            small_net_inflow_pct=_safe_float(it.get("f87")),
            updated_at=updated_at,
        )
