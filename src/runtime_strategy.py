from __future__ import annotations

from typing import TYPE_CHECKING

from config import (
    PROFIT_LOCK_LEVEL_1_BPS,
    PROFIT_LOCK_LEVEL_2_BPS,
    REENTRY_MOMENTUM_LOOKBACK,
    REENTRY_RSI_BUY_THRESHOLD,
    REENTRY_RSI_PERIOD,
    REENTRY_RSI_TURN_MARGIN,
    STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES,
    STOP_LOSS_PCT,
    WAIT_REENTRY_PULLBACK_PCT,
)
from runtime_risk import current_profit_pct, reentry_timeout_remaining
from sizing_engine import build_sizing_snapshot
from strategy import calculate_rsi, detect_momentum_slowing

if TYPE_CHECKING:
    from bot_runner import BotRuntime


def reentry_pullback_price(last_sell_price: float | None) -> float | None:
    if last_sell_price is None or last_sell_price <= 0:
        return None
    return last_sell_price * (1.0 - (WAIT_REENTRY_PULLBACK_PCT / 100.0))


def base_sell_debug_reason(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    *,
    sell_enabled: bool,
    sell_state_allowed: bool,
    in_cooldown: bool,
    state_requires_reentry_only: bool,
) -> str:
    current_state = runtime.state_context.current_state.value if runtime.enable_state_machine else "DISABLED"
    tradable_eth = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0)
    current_profit = current_profit_pct(runtime, mid)
    level_one_target_pct = PROFIT_LOCK_LEVEL_1_BPS / 100.0
    level_two_target_pct = PROFIT_LOCK_LEVEL_2_BPS / 100.0
    state = runtime.profit_lock_state

    if state_requires_reentry_only:
        return "wait_reentry_buy_zones"
    if in_cooldown:
        return "cooldown"
    if not sell_state_allowed:
        return f"sell_blocked_state:{current_state}"
    if tradable_eth <= 0:
        return "protect_eth_reserve"
    if runtime.enable_state_machine and runtime.state_machine.accumulating_failsafe_due(runtime.state_context, cycle_index):
        return f"time_exit_ready:{STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES:.1f}m"
    if current_profit is not None and current_profit <= STOP_LOSS_PCT:
        return f"stop_loss_ready:{current_profit:.3f}%"
    if current_profit is None:
        return "no_profit_anchor"
    if current_profit > 0.0:
        return "profit_exit_ready"
    if not state.level_two_executed and current_profit >= level_two_target_pct:
        return "full_sell_ready"
    if not state.level_one_executed and current_profit >= level_one_target_pct:
        return "partial_sell_ready"
    if not sell_enabled:
        return "sell_disabled"
    if not state.level_one_executed:
        return "profit_below_partial_target"
    if not state.level_two_executed:
        return "waiting_full_sell_target"
    return "profit_targets_complete"


def finalize_sell_debug_reason(
    *,
    base_reason: str,
    action: str,
    sell_reason: str,
    selected_reason: str,
    allow_trade: bool,
    block_reason: str,
    sell_fill,
) -> str:
    if sell_fill is not None:
        if sell_fill.filled:
            return f"sell_executed:{sell_fill.trade_reason}"
        return f"sell_unfilled:{sell_fill.trade_reason}"

    if action == "SELL":
        if allow_trade:
            return f"sell_ready:{sell_reason}"
        return f"sell_blocked:{block_reason or 'trade_blocked'}"
    if action == "BUY" and "ready" in base_reason:
        return f"sell_overridden:{selected_reason}"

    return base_reason


def base_buy_debug_reason(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    *,
    buy_enabled: bool,
    buy_state_allowed: bool,
    in_cooldown: bool,
    reentry_plan,
    partial_reset_reason: str | None,
    force_trade_candidate,
    trend_signal_allows_buy: bool,
) -> str:
    current_state = runtime.state_context.current_state.value if runtime.enable_state_machine else "DISABLED"
    sizing = getattr(runtime, "current_sizing", None)
    if sizing is None:
        sizing = build_sizing_snapshot(
            current_equity_usd=runtime.portfolio.total_equity_usd(mid),
            mid_price=mid,
            portfolio_usdc=runtime.portfolio.usdc,
            portfolio_eth=runtime.portfolio.eth,
        )

    if in_cooldown:
        return "cooldown"
    if sizing.available_quote_to_trade_usd < sizing.min_notional_usd:
        if sizing.insufficient_equity_for_min_trade:
            return "insufficient_equity_for_min_trade"
        return "insufficient_quote_reserve"
    if runtime.reentry_state.active:
        if reentry_plan and reentry_plan.allow_trade:
            return f"reentry_buy_ready:{reentry_plan.trade_reason}"
        pullback_target = reentry_pullback_price(runtime.reentry_state.last_sell_price)
        if pullback_target is not None and mid > pullback_target:
            return f"waiting_reentry_pullback:{pullback_target:.2f}"
        timeout_remaining = reentry_timeout_remaining(runtime, cycle_index)
        if timeout_remaining > 0:
            return f"waiting_reentry_timeout:{timeout_remaining}"
        return "waiting_reentry_trigger"
    if not buy_state_allowed:
        return f"buy_blocked_state:{current_state}"
    if force_trade_candidate is not None and force_trade_candidate.action == "BUY":
        return "force_trade_buy_ready"
    if partial_reset_reason:
        return f"{partial_reset_reason}_ready"
    if trend_signal_allows_buy:
        return "trend_buy_ready"
    if not buy_enabled:
        return "buy_disabled"
    return "no_buy_signal"


def finalize_buy_debug_reason(
    *,
    base_reason: str,
    action: str,
    buy_reason: str,
    selected_reason: str,
    allow_trade: bool,
    block_reason: str,
    buy_fill,
) -> str:
    if buy_fill is not None:
        if buy_fill.filled:
            return f"buy_executed:{buy_fill.trade_reason}"
        return f"buy_unfilled:{buy_fill.trade_reason}"

    if action == "BUY":
        if allow_trade:
            return f"buy_ready:{buy_reason}"
        return f"buy_blocked:{block_reason or 'trade_blocked'}"
    if action == "SELL" and "ready" in base_reason:
        return f"buy_overridden:{selected_reason}"

    return base_reason


def trade_reason_category(mode: str, trade_reason: str) -> str:
    if not trade_reason:
        return ""
    if trade_reason.startswith("reentry_"):
        return "reentry"
    if trade_reason in {"failsafe_sell", "time_exit_sell"}:
        return "failsafe"
    if trade_reason == "stop_loss_sell":
        return "stop_loss"
    if trade_reason.startswith("force_trade_"):
        return "force_trade"
    if trade_reason == "trend_buy":
        return "trend"
    if trade_reason in {"range_buy", "inactivity_range_buy", "range_sell", "mean_reversion_exit"}:
        return "mean_reversion"
    if trade_reason == "trend_rally_sell":
        return "trend"
    if trade_reason in {"profit_lock_level_1", "profit_lock_level_2", "profit_exit_sell"}:
        return "momentum"
    if trade_reason == "quoted_sell":
        return "trend" if mode in {"TREND_UP", "OVERWEIGHT_EXIT"} else "mean_reversion"
    if trade_reason == "partial_reset":
        return "mean_reversion"
    return "momentum"


def buy_confirmation(prices: list[float]) -> tuple[bool, float, float, bool]:
    current_rsi = calculate_rsi(prices, REENTRY_RSI_PERIOD)
    previous_rsi = calculate_rsi(prices[:-1], REENTRY_RSI_PERIOD) if len(prices) > REENTRY_RSI_PERIOD else current_rsi
    momentum_slowing = detect_momentum_slowing(prices, REENTRY_MOMENTUM_LOOKBACK)
    rsi_turning = (
        previous_rsi < REENTRY_RSI_BUY_THRESHOLD
        and current_rsi > (previous_rsi + REENTRY_RSI_TURN_MARGIN)
        and current_rsi <= (REENTRY_RSI_BUY_THRESHOLD + 5.0)
    )
    return momentum_slowing or rsi_turning, current_rsi, previous_rsi, momentum_slowing


def cap_trend_sell_order(
    *,
    mode: str,
    sell_order,
    inventory_usd: float,
    equity_usd: float,
    effective_max_inventory_usd: float,
    target_inventory_pct: float,
    mid: float,
) -> None:
    del effective_max_inventory_usd
    if mode != "TREND_UP" or mid <= 0:
        return

    target_inventory_usd = equity_usd * min(max(target_inventory_pct, 0.0), 1.0)
    if target_inventory_usd <= 0:
        return

    max_sell_usd = max(inventory_usd - target_inventory_usd, 0.0)
    if max_sell_usd <= 0:
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0
        return

    if sell_order.size_usd > max_sell_usd:
        sell_order.size_usd = max_sell_usd
        sell_order.size_base = sell_order.size_usd / sell_order.price
