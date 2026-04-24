from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from logger import log

if TYPE_CHECKING:
    from bot_runner import BotRuntime


PERSISTED_KEYS: tuple[str, ...] = (
    "daily_reset_date",
    "daily_start_equity",
    "daily_start_realized_pnl",
    "daily_pnl_usd",
    "daily_trade_count",
    "equity_peak",
    "max_drawdown_usd",
    "max_drawdown_pct",
    "current_drawdown_pct",
    "drawdown_guard_stage",
    "last_trade_cycle_any",
    "last_trade_price_any",
    "last_fill_cycle",
    "last_fill_side",
    "last_fill_price",
    "last_trade_reason",
    "current_active_regime",
    "current_market_mode",
)


def load_state(path: str) -> dict[str, object]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"Runtime state load failed: {exc}")
        return {}


def apply_state(runtime: BotRuntime, payload: dict[str, object]) -> None:
    if not payload:
        return
    for key in PERSISTED_KEYS:
        if key in payload:
            setattr(runtime, key, payload[key])

    reentry_payload = payload.get("reentry_state")
    if isinstance(reentry_payload, dict):
        for key, value in reentry_payload.items():
            if hasattr(runtime.reentry_state, key):
                setattr(runtime.reentry_state, key, value)


def dump_state(runtime: BotRuntime, *, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {key: getattr(runtime, key, None) for key in PERSISTED_KEYS}
    payload["portfolio"] = {
        "usdc": runtime.portfolio.usdc,
        "eth": runtime.portfolio.eth,
        "fees_paid_usd": runtime.portfolio.fees_paid_usd,
        "realized_pnl_usd": runtime.portfolio.realized_pnl_usd,
        "eth_cost_basis": runtime.portfolio.eth_cost_basis,
    }
    payload["reentry_state"] = {
        "active": runtime.reentry_state.active,
        "last_sell_price": runtime.reentry_state.last_sell_price,
        "last_sell_size_usd": runtime.reentry_state.last_sell_size_usd,
        "last_sell_cycle": runtime.reentry_state.last_sell_cycle,
        "buy_zones": list(runtime.reentry_state.buy_zones),
        "executed_buy_levels": list(runtime.reentry_state.executed_buy_levels),
        "budget_usd": runtime.reentry_state.budget_usd,
        "spent_usd": runtime.reentry_state.spent_usd,
        "timeout_cycle": runtime.reentry_state.timeout_cycle,
    }
    try:
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(f"Runtime state save failed: {exc}")
