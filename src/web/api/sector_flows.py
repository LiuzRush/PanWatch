from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.collectors.sector_flow_collector import EastMoneySectorFlowCollector
from src.config import Settings
from src.models.market import MARKETS, MarketCode

router = APIRouter()
sectors_router = APIRouter()
groups_router = APIRouter()


class SectorFlowGroupSelector(BaseModel):
    type: str = Field(default="any", description="匹配类型: any/industry/concept")
    code: str = Field(default="", description="板块/概念代码")
    name: str = Field(default="", description="板块/概念名称或关键词")


class SectorFlowGroup(BaseModel):
    id: str | None = Field(default=None, description="聚类稳定 ID")
    name: str = Field(..., min_length=1, description="聚类名称")
    enabled: bool = Field(default=True, description="是否启用")
    includes: list[str] = Field(default_factory=list, description="兼容旧版: 包含的板块/概念代码或名称")
    excludes: list[str] = Field(default_factory=list, description="兼容旧版: 排除的板块/概念代码或名称")
    include_items: list[SectorFlowGroupSelector] = Field(default_factory=list, description="结构化包含项")
    exclude_items: list[SectorFlowGroupSelector] = Field(default_factory=list, description="结构化排除项")
    aliases: list[str] = Field(default_factory=list, description="聚类别名")
    weight: float = Field(default=1.0, description="聚合权重")
    order: int = Field(default=0, description="展示排序")
    color: str = Field(default="", description="前端展示颜色")


class SectorFlowGroupsPayload(BaseModel):
    groups: list[SectorFlowGroup] = Field(default_factory=list)


class SectorFlowGroupPreviewPayload(BaseModel):
    groups: list[SectorFlowGroup] = Field(default_factory=list)


def _data_dir() -> Path:
    path = Path(os.environ.get("DATA_DIR", "./data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _groups_file() -> Path:
    return _data_dir() / "sector_flow_groups.json"


def _default_groups_file() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "sector_flow_groups.json"


def _snapshot_dir() -> Path:
    path = _data_dir() / "sector_flow_snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _today_str() -> str:
    tz = Settings().app_timezone or "Asia/Shanghai"
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def _snapshot_file(date_str: str) -> Path:
    safe = (date_str or _today_str()).strip()
    if not safe:
        safe = _today_str()
    return _snapshot_dir() / f"{safe}.json"


def _parse_sector_type(value: str) -> str:
    v = (value or "industry").lower()
    if v not in ("industry", "concept"):
        raise HTTPException(400, f"不支持的 type: {value}")
    return v


def _parse_mode(value: str) -> str:
    v = (value or "main_net").lower()
    if v not in ("main_net", "turnover"):
        raise HTTPException(400, f"不支持的 mode: {value}")
    return v


def _parse_limit(value: int, *, max_value: int = 500) -> int:
    try:
        safe = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "limit 必须是整数")
    if safe < 1 or safe > max_value:
        raise HTTPException(400, f"limit 必须在 1-{max_value} 之间")
    return safe


def _parse_days(value: int) -> int:
    try:
        safe = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "days 必须是整数")
    if safe < 1 or safe > 5000:
        raise HTTPException(400, "days 必须在 1-5000 之间")
    return safe


def _require_cn(market: str) -> str:
    mkt = (market or "CN").upper()
    if mkt != "CN":
        raise HTTPException(400, "板块/概念资金流当前仅支持 CN")
    return mkt


def _dump_model(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _load_groups() -> list[dict]:
    path = _groups_file()
    if not path.exists():
        path = _default_groups_file()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            groups = data.get("groups") or []
        else:
            groups = data or []
        return groups if isinstance(groups, list) else []
    except Exception:
        return []


def _save_groups(groups: list[dict]) -> None:
    path = _groups_file()
    normalized = _normalize_groups(groups)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"groups": normalized}, f, ensure_ascii=False, indent=2)


def _load_snapshot(date_str: str) -> dict | None:
    path = _snapshot_file(date_str)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_latest_snapshot() -> dict | None:
    files = sorted(_snapshot_dir().glob("*.json"), reverse=True)
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _save_snapshot(date_str: str, items: list[dict]) -> dict:
    payload = {
        "date": date_str,
        "market": "CN",
        "created_at": int(datetime.now().timestamp()),
        "items": items,
    }
    path = _snapshot_file(date_str)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _is_cn_trading_time() -> bool:
    md = MARKETS.get(MarketCode.CN)
    return bool(md and md.is_trading_time())


def _fetch_all_sector_flows(limit: int = 500) -> list[dict]:
    collector = EastMoneySectorFlowCollector()
    items = []
    for sector_type in ("industry", "concept"):
        flows = collector.fetch_sector_flows(
            sector_type=sector_type, mode="main_net", limit=limit
        )
        items.extend(asdict(item) for item in flows)
    return items


def _resolve_sector_flows_for_summary(
    *,
    date: str | None = None,
    persist_live: bool = True,
) -> tuple[str, str, list[dict]]:
    """Resolve sector flows with snapshot fallback.

    source is "live" when using trading-session realtime data, otherwise "snapshot".
    """
    date_str = (date or "").strip()
    if date_str:
        snapshot = _load_snapshot(date_str)
        if not snapshot:
            raise HTTPException(404, "板块/概念资金流快照不存在")
        return date_str, "snapshot", snapshot.get("items") or []

    today = _today_str()
    trading = _is_cn_trading_time()
    if not trading:
        snapshot = _load_snapshot(today)
        if snapshot:
            return snapshot.get("date") or today, "snapshot", snapshot.get("items") or []

    flows = _fetch_all_sector_flows()
    if flows:
        if persist_live:
            _save_snapshot(today, flows)
        return today, "live" if trading else "snapshot", flows

    snapshot = _load_snapshot(today) or _load_latest_snapshot()
    if snapshot:
        return snapshot.get("date") or today, "snapshot", snapshot.get("items") or []
    return today, "live", []


def _sector_metas_from_snapshot(*, sector_type: str, keyword: str = "", limit: int = 500) -> list[dict]:
    snapshot = _load_snapshot(_today_str()) or _load_latest_snapshot()
    if not snapshot:
        return []
    kw = (keyword or "").strip().lower()
    out = []
    seen = set()
    for item in snapshot.get("items") or []:
        if str(item.get("type") or "") != sector_type:
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        if kw and kw not in code.lower() and kw not in name.lower():
            continue
        key = (sector_type, code)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "code": code,
                "name": name,
                "type": sector_type,
                "market": "CN",
                "source": "snapshot",
            }
        )
        if len(out) >= limit:
            break
    return out


def _group_id(name: str, order: int, idx: int) -> str:
    raw = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower())
    raw = raw.strip("-")
    return raw or f"group-{order or idx + 1}"


def _selector_token(selector: dict) -> str:
    return str(selector.get("code") or selector.get("name") or "").strip()


def _selector_label(selector: dict) -> str:
    code = str(selector.get("code") or "").strip()
    name = str(selector.get("name") or "").strip()
    if code and name:
        return f"{name}({code})"
    return name or code or "--"


def _normalize_selector(raw) -> dict | None:
    if isinstance(raw, str):
        token = raw.strip()
        if not token:
            return None
        return {"type": "any", "code": "", "name": token}
    if not isinstance(raw, dict):
        return None
    selector_type = str(raw.get("type") or "any").strip().lower()
    if selector_type not in ("any", "industry", "concept"):
        selector_type = "any"
    code = str(raw.get("code") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not code and not name:
        return None
    return {"type": selector_type, "code": code, "name": name}


def _normalize_selectors(items: list | None, fallback: list | None = None) -> list[dict]:
    out = []
    source = items if items else fallback
    for raw in source or []:
        selector = _normalize_selector(raw)
        if selector is not None:
            out.append(selector)
    seen = set()
    deduped = []
    for selector in out:
        key = (
            selector.get("type") or "any",
            str(selector.get("code") or "").lower(),
            str(selector.get("name") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(selector)
    return deduped


def _normalize_group(group: dict, idx: int = 0) -> dict:
    name = str(group.get("name") or f"未命名聚类{idx + 1}").strip()
    order = int(_num(group.get("order") or 0))
    include_items = _normalize_selectors(group.get("include_items"), group.get("includes"))
    exclude_items = _normalize_selectors(group.get("exclude_items"), group.get("excludes"))
    includes = [_selector_token(item) for item in include_items if _selector_token(item)]
    excludes = [_selector_token(item) for item in exclude_items if _selector_token(item)]
    return {
        "id": str(group.get("id") or _group_id(name, order, idx)).strip(),
        "name": name,
        "enabled": bool(group.get("enabled", True)),
        "includes": includes,
        "excludes": excludes,
        "include_items": include_items,
        "exclude_items": exclude_items,
        "aliases": [str(x).strip() for x in (group.get("aliases") or []) if str(x).strip()],
        "weight": _num(group.get("weight") or 1.0) or 1.0,
        "order": order,
        "color": str(group.get("color") or "").strip(),
    }


def _normalize_groups(groups: list[dict]) -> list[dict]:
    return [_normalize_group(group, idx) for idx, group in enumerate(groups or [])]


def _match_token(flow: dict, token: str) -> bool:
    t = (token or "").strip().lower()
    if not t:
        return False
    code = str(flow.get("code") or "").lower()
    name = str(flow.get("name") or "").lower()
    return t == code or t == name or t in name


def _match_selector(flow: dict, selector: dict) -> bool:
    selector_type = str(selector.get("type") or "any").lower()
    flow_type = str(flow.get("type") or "").lower()
    if selector_type in ("industry", "concept") and selector_type != flow_type:
        return False
    code = str(selector.get("code") or "").strip().lower()
    name = str(selector.get("name") or "").strip().lower()
    flow_code = str(flow.get("code") or "").strip().lower()
    flow_name = str(flow.get("name") or "").strip().lower()
    if code:
        return code == flow_code
    if name:
        return name == flow_name or name in flow_name
    return False


def _group_matches(flow: dict, group: dict) -> bool:
    include_items = _normalize_selectors(group.get("include_items"), group.get("includes"))
    exclude_items = _normalize_selectors(group.get("exclude_items"), group.get("excludes"))
    if exclude_items and any(_match_selector(flow, selector) for selector in exclude_items):
        return False
    if not include_items:
        return False
    return any(_match_selector(flow, selector) for selector in include_items)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _build_group_summary(groups: list[dict], flows: list[dict]) -> list[dict]:
    out = []
    for idx, raw_group in enumerate(groups):
        group = _normalize_group(raw_group, idx)
        if not group.get("enabled", True):
            continue
        matched = [flow for flow in flows if _group_matches(flow, group)]
        if not matched:
            continue
        weight = _num(group.get("weight") or 1.0) or 1.0
        raw_main_net = sum(_num(it.get("main_net_inflow")) for it in matched)
        adjusted_main_net = raw_main_net * weight
        turnover = sum(_num(it.get("turnover")) for it in matched)
        raw_pct = (raw_main_net / turnover * 100.0) if turnover else None
        adjusted_pct = (adjusted_main_net / turnover * 100.0) if turnover else None
        contributors = sorted(
            matched,
            key=lambda it: abs(_num(it.get("main_net_inflow"))),
            reverse=True,
        )[:8]
        out.append(
            {
                "id": group.get("id"),
                "name": group.get("name"),
                "enabled": group.get("enabled", True),
                "aliases": group.get("aliases") or [],
                "order": int(group.get("order") or 0),
                "weight": weight,
                "color": group.get("color") or "",
                "sector_count": len(matched),
                "main_net_inflow": adjusted_main_net,
                "raw_main_net_inflow": raw_main_net,
                "adjusted_main_net_inflow": adjusted_main_net,
                "turnover": turnover,
                "main_net_inflow_pct": adjusted_pct,
                "raw_main_net_inflow_pct": raw_pct,
                "adjusted_main_net_inflow_pct": adjusted_pct,
                "contributors": [
                    {
                        "code": it.get("code"),
                        "name": it.get("name"),
                        "type": it.get("type"),
                        "main_net_inflow": it.get("main_net_inflow"),
                        "main_net_inflow_pct": it.get("main_net_inflow_pct"),
                        "turnover": it.get("turnover"),
                        "change_pct": it.get("change_pct"),
                    }
                    for it in contributors
                ],
            }
        )
    out.sort(key=lambda it: (it.get("order") or 0, it.get("name") or ""))
    return out


def _build_group_diagnostics(groups: list[dict], flows: list[dict]) -> dict:
    normalized = _normalize_groups(groups)
    flow_map = {
        (str(flow.get("type") or ""), str(flow.get("code") or "")): flow
        for flow in flows
    }
    memberships: dict[tuple[str, str], list[str]] = {}
    empty_groups = []
    invalid_selectors = []

    for group in normalized:
        if not group.get("enabled", True):
            continue
        matched = [flow for flow in flows if _group_matches(flow, group)]
        if not matched:
            empty_groups.append(
                {"id": group.get("id"), "name": group.get("name"), "reason": "无匹配板块"}
            )
        for flow in matched:
            key = (str(flow.get("type") or ""), str(flow.get("code") or ""))
            memberships.setdefault(key, []).append(str(group.get("name") or ""))
        for direction, selectors in (
            ("include", group.get("include_items") or []),
            ("exclude", group.get("exclude_items") or []),
        ):
            for selector in selectors:
                if not any(_match_selector(flow, selector) for flow in flows):
                    invalid_selectors.append(
                        {
                            "group_id": group.get("id"),
                            "group_name": group.get("name"),
                            "direction": direction,
                            "selector": selector,
                            "label": _selector_label(selector),
                            "reason": "未匹配到当前板块/概念列表",
                        }
                    )

    duplicate_members = []
    for key, group_names in memberships.items():
        unique_groups = sorted(set(group_names))
        if len(unique_groups) <= 1:
            continue
        flow = flow_map.get(key) or {}
        duplicate_members.append(
            {
                "type": key[0],
                "code": key[1],
                "name": flow.get("name") or key[1],
                "groups": unique_groups,
            }
        )

    return {
        "total_flows": len(flows),
        "matched_flows": len(memberships),
        "duplicate_members": duplicate_members,
        "empty_groups": empty_groups,
        "invalid_selectors": invalid_selectors,
    }


@router.get("")
def list_sector_flows(
    market: str = "CN",
    type: str = Query(default="industry"),
    mode: str = "main_net",
    limit: int = 100,
):
    """查询行业/概念板块资金流排行。"""
    _require_cn(market)
    sector_type = _parse_sector_type(type)
    safe_mode = _parse_mode(mode)
    safe_limit = _parse_limit(limit)
    collector = EastMoneySectorFlowCollector()
    items = collector.fetch_sector_flows(
        sector_type=sector_type, mode=safe_mode, limit=safe_limit
    )
    return {
        "market": "CN",
        "type": sector_type,
        "mode": safe_mode,
        "limit": safe_limit,
        "items": [asdict(item) for item in items],
    }


@router.post("/snapshots/refresh")
def refresh_sector_flow_snapshot(market: str = "CN", date: str | None = None):
    """刷新并归档当天行业/概念资金流快照。"""
    _require_cn(market)
    date_str = (date or _today_str()).strip()
    items = _fetch_all_sector_flows()
    if not items:
        raise HTTPException(503, "板块/概念资金流数据源不可用")
    return _save_snapshot(date_str, items)


@router.get("/snapshots")
def get_sector_flow_snapshot(date: str | None = None):
    """读取行业/概念资金流快照。"""
    date_str = (date or _today_str()).strip()
    payload = _load_snapshot(date_str)
    if not payload:
        raise HTTPException(404, "板块/概念资金流快照不存在")
    return payload


@router.get("/{board_code}/history")
def get_sector_flow_history(
    board_code: str,
    type: str = Query(default="industry"),
    days: int = 60,
):
    """查询单个行业/概念板块日级历史资金流。"""
    sector_type = _parse_sector_type(type)
    safe_days = _parse_days(days)
    collector = EastMoneySectorFlowCollector()
    items = collector.fetch_sector_flow_history(board_code=board_code, days=safe_days)
    if not items:
        raise HTTPException(404, "板块/概念历史资金流不存在")
    return {
        "market": "CN",
        "type": sector_type,
        "board_code": board_code,
        "days": safe_days,
        "items": [asdict(item) for item in items],
    }


@router.get("/{board_code}/stocks")
def get_sector_stock_flows(
    board_code: str,
    type: str = Query(default="industry"),
    limit: int = 100,
):
    """查询单个行业/概念板块成分股资金流贡献。"""
    sector_type = _parse_sector_type(type)
    safe_limit = _parse_limit(limit)
    collector = EastMoneySectorFlowCollector()
    items = collector.fetch_sector_stock_flows(
        board_code=board_code, sector_type=sector_type, limit=safe_limit
    )
    if not items:
        raise HTTPException(404, "板块/概念成分股资金流不存在")
    return {
        "market": "CN",
        "type": sector_type,
        "board_code": board_code,
        "limit": safe_limit,
        "items": [asdict(item) for item in items],
    }


@sectors_router.get("")
def list_sectors(
    type: str = Query(default="industry"),
    keyword: str = "",
    limit: int = 500,
):
    """查询行业/概念元数据。"""
    sector_type = _parse_sector_type(type)
    safe_limit = _parse_limit(limit)
    collector = EastMoneySectorFlowCollector()
    items = collector.fetch_sectors(
        sector_type=sector_type, keyword=keyword, limit=safe_limit
    )
    payload_items = [asdict(item) for item in items]
    if not payload_items:
        payload_items = _sector_metas_from_snapshot(
            sector_type=sector_type,
            keyword=keyword,
            limit=safe_limit,
        )
    return {
        "market": "CN",
        "type": sector_type,
        "keyword": keyword,
        "items": payload_items,
    }


@groups_router.get("")
def get_sector_flow_groups():
    """读取板块资金流聚类规则。"""
    return {"groups": _normalize_groups(_load_groups())}


@groups_router.put("")
def update_sector_flow_groups(payload: SectorFlowGroupsPayload):
    """覆盖保存板块资金流聚类规则。"""
    groups = [_dump_model(group) for group in payload.groups]
    _save_groups(groups)
    return {"groups": _normalize_groups(groups)}


@groups_router.get("/defaults")
def get_default_sector_flow_groups():
    """读取默认板块资金流聚类规则模板。"""
    path = _default_groups_file()
    if not path.exists():
        return {"groups": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        groups = data.get("groups") if isinstance(data, dict) else data
    except Exception:
        groups = []
    return {"groups": _normalize_groups(groups or [])}


@groups_router.post("/preview")
def preview_sector_flow_groups(payload: SectorFlowGroupPreviewPayload, date: str | None = None):
    """预览未保存的板块资金流聚类规则和诊断信息。"""
    groups = [_dump_model(group) for group in payload.groups]
    date_str, source, flows = _resolve_sector_flows_for_summary(
        date=date,
        persist_live=False,
    )
    return {
        "date": date_str,
        "source": source,
        "groups": _build_group_summary(groups, flows),
        "diagnostics": _build_group_diagnostics(groups, flows),
    }


@groups_router.get("/summary")
def get_sector_flow_group_summary(date: str | None = None):
    """按聚类规则汇总行业/概念资金流。"""
    groups = _load_groups()
    date_str, source, flows = _resolve_sector_flows_for_summary(date=date)
    return {
        "date": date_str,
        "source": source,
        "groups": _build_group_summary(groups, flows),
    }
