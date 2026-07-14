"""毛毛虫做 T 状态存储。

按 symbol 持久化「做 T 腿位 + 利润垫」状态，供 caterpillar Agent 做连续决策。
状态文件落在 DATA_DIR 下，跨交易日自动重置（参照 intraday_event_gate 的轻量 JSON 模式）。

腿位（t_leg）状态机（单只、单日）：

    flat（空 T，仅底仓）
      │  低吸/竞价买入            高抛卖出
      ▼                           ▲
    long（已低吸，待高抛） ───────┘   一次蠕动闭环 → cushion_pct += 差价%
      │  先高抛（底仓做 T）
      ▼
    short（已高抛，待低吸买回） ──→ 低吸买回 → 回到 flat，cushion_pct += 差价%

Agent 不实际下单，这里记录的是「建议层面」的虚拟成交，
用于让模型连续决策并向用户呈现「今日已做 T N 次、利润垫 +x%」。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.core.json_store import read_json, write_json_atomic
from src.core.timezone import beijing_now

logger = logging.getLogger(__name__)

# 做 T 腿位
LEG_FLAT = "flat"  # 空 T，仅底仓
LEG_LONG = "long"  # 已低吸，待高抛
LEG_SHORT = "short"  # 已高抛，待低吸买回

# t_action -> 腿位推进语义
BUY_ACTIONS = {"t_buy_low", "t_buy_back"}  # 低吸 / 高抛后买回
SELL_ACTIONS = {"t_sell_high", "retreat"}  # 高抛 / 撤退


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _state_path() -> str:
    return os.path.join(_data_dir(), "state", "caterpillar_state.json")


def _today_str() -> str:
    return beijing_now().strftime("%Y-%m-%d")


def _now_hm() -> str:
    return beijing_now().strftime("%H:%M")


def _default_entry(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "t_leg": LEG_FLAT,
        "entry_price": None,
        "entry_time": None,
        "last_action": None,
        "cushion_pct": 0.0,
        "cycles": 0,
    }


def load_state(symbol: str) -> dict[str, Any]:
    """读取某只股票的当日做 T 状态（跨日自动重置）。"""
    today = _today_str()
    path = _state_path()
    store = read_json(path, default={})
    if not isinstance(store, dict):
        store = {}
    entry = store.get(symbol)
    if not isinstance(entry, dict) or entry.get("date") != today:
        return _default_entry(today)
    # 补齐缺失字段，向前兼容
    base = _default_entry(today)
    base.update({k: entry.get(k, base[k]) for k in base})
    base["date"] = today
    return base


def _save_entry(symbol: str, entry: dict[str, Any]) -> None:
    path = _state_path()
    store = read_json(path, default={})
    if not isinstance(store, dict):
        store = {}
    store[symbol] = entry
    try:
        write_json_atomic(path, store)
    except Exception as e:  # 状态持久化失败不应阻断 Agent
        logger.warning(f"caterpillar 状态写入失败: {e}")


def apply_action(
    symbol: str, t_action: str | None, price: float | None
) -> dict[str, Any]:
    """根据本次建议动作推进腿位并结算利润垫。

    Args:
        symbol: 股票代码
        t_action: 毛毛虫动作（t_buy_low/t_buy_back/t_sell_high/retreat/hold/watch）
        price: 当前价（用于计算差价）

    Returns:
        更新后的状态字典。
    """
    entry = load_state(symbol)
    action = (t_action or "").strip()

    # 仅可执行的买/卖动作推进腿位；hold/watch 不改变状态
    if action in BUY_ACTIONS and price:
        if entry["t_leg"] == LEG_SHORT and entry.get("entry_price"):
            # 高抛后买回，结算「高抛→低吸」差价（卖在高、买在低为正收益）
            ep = float(entry["entry_price"])
            if ep > 0:
                spread = (ep - float(price)) / ep * 100.0
                entry["cushion_pct"] = round(
                    float(entry.get("cushion_pct") or 0.0) + spread, 2
                )
                entry["cycles"] = int(entry.get("cycles") or 0) + 1
            entry["t_leg"] = LEG_FLAT
            entry["entry_price"] = None
            entry["entry_time"] = None
        else:
            # 低吸建/补 T 仓，进入待高抛
            entry["t_leg"] = LEG_LONG
            entry["entry_price"] = float(price)
            entry["entry_time"] = _now_hm()
    elif action in SELL_ACTIONS and price:
        if entry["t_leg"] == LEG_LONG and entry.get("entry_price"):
            # 低吸后高抛，结算「低吸→高抛」差价
            ep = float(entry["entry_price"])
            if ep > 0:
                spread = (float(price) - ep) / ep * 100.0
                entry["cushion_pct"] = round(
                    float(entry.get("cushion_pct") or 0.0) + spread, 2
                )
                entry["cycles"] = int(entry.get("cycles") or 0) + 1
            entry["t_leg"] = LEG_FLAT
            entry["entry_price"] = None
            entry["entry_time"] = None
        else:
            # 先高抛底仓（做 T 的卖出腿），进入待低吸买回
            entry["t_leg"] = LEG_SHORT
            entry["entry_price"] = float(price)
            entry["entry_time"] = _now_hm()

    entry["last_action"] = action or entry.get("last_action")
    _save_entry(symbol, entry)
    return entry


def leg_label(t_leg: str) -> str:
    return {
        LEG_FLAT: "空T（仅底仓）",
        LEG_LONG: "已低吸·待高抛",
        LEG_SHORT: "已高抛·待低吸买回",
    }.get(t_leg, t_leg)
