from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from config import (
    ETH_ACCUMULATION_REINVEST_PCT,
    ETH_PRESERVATION_FLOOR_MULTIPLIER,
    INTELLIGENCE_WARMUP_ROWS,
    LONG_MA_WINDOW,
    MAX_DAILY_LOSS_USD,
    MAX_EXPOSURE_USD,
    MAX_TRADES_PER_DAY,
    MIN_ORDER_SIZE_USD,
    PARTIAL_RESET_BUY_FRACTION,
    PARTIAL_RESET_USDC_THRESHOLD_PCT,
    PROFIT_LOCK_LEVEL_1_SELL_FRACTION,
    RANGE_EXIT_MIN_POSITION_PCT,
    RANGE_MEAN_REVERSION_EXIT_POSITION_PCT,
    REENTRY_INVENTORY_BUFFER_PCT,
    REENTRY_TIMEOUT_MINUTES,
    REENTRY_ZONE_1_BUY_FRACTION,
    REENTRY_ZONE_1_MULTIPLIER,
    REENTRY_ZONE_2_BUY_FRACTION,
    REENTRY_ZONE_2_MULTIPLIER,
    REENTRY_ZONE_3_BUY_FRACTION,
    REENTRY_ZONE_3_MULTIPLIER,
    SIDE_FLIP_COOLDOWN_CYCLES,
    SIDE_FLIP_MIN_BPS,
    STOP_LOSS_PCT,
    TRADE_FILTER_FORCE_TRADE_MINUTES,
    VOL_WINDOW,
)
from sizing_engine import SizingSnapshot, build_sizing_snapshot, trade_direction_to_target
from strategy_profile import resolve_hold_limits_minutes, resolve_take_profit_targets_bps
from strategy import calculate_buy_zones
from types_bot import DecisionOutcome, ProfitLockState, ReentryState, StrategyState

if TYPE_CHECKING:
    from bot_runner import BotRuntime

PRICE_WINDOW_SIZE = max(INTELLIGENCE_WARMUP_ROWS, LONG_MA_WINDOW, VOL_WINDOW + 1)
PROFIT_THRESHOLD_EPSILON_PCT = 1e-6


def _profit_lock_thresholds_bps(runtime: BotRuntime) -> tuple[float, float]:
    active_regime = getattr(runtime, "current_market_mode", "") or getattr(runtime, "current_active_regime", "") or "TREND"
    volatility_bucket = getattr(runtime, "current_volatility_bucket", "")
    return resolve_take_profit_targets_bps(active_regime, volatility_bucket)


def _current_sizing(runtime: BotRuntime) -> SizingSnapshot:
    snapshot = getattr(runtime, "current_sizing", None)
    if snapshot is not None:
        return snapshot

    mid_price = getattr(runtime, "last_mid", 0.0) or (runtime.prices[-1] if getattr(runtime, "prices", None) else 0.0)
    equity_usd = runtime.portfolio.total_equity_usd(mid_price) if mid_price > 0 else max(runtime.portfolio.usdc, 0.0)
    return build_sizing_snapshot(
        current_equity_usd=equity_usd,
        mid_price=mid_price,
        portfolio_usdc=runtime.portfolio.usdc,
        portfolio_eth=runtime.portfolio.eth,
    )


def min_notional_usd(runtime: BotRuntime) -> float:
    return max(_current_sizing(runtime).min_notional_usd, MIN_ORDER_SIZE_USD)


def max_trade_size_usd(runtime: BotRuntime) -> float:
    return max(_current_sizing(runtime).max_trade_size_usd, 0.0)


def max_position_usd(runtime: BotRuntime) -> float:
    return max(_current_sizing(runtime).max_position_usd, 0.0)


@dataclass(frozen=True)
class RiskLimitDecision:
    stop_trading: bool
    reason: str = ""
    details: str = ""
    daily_pnl_usd: float = 0.0
    exposure_usd: float = 0.0
    projected_exposure_usd: float = 0.0
    trade_size_usd: float = 0.0


def trim_price_history(prices: list[float]) -> None:
    overflow = len(prices) - PRICE_WINDOW_SIZE
    if overflow > 0:
        del prices[:overflow]


def account_state(runtime: BotRuntime, mid: float) -> tuple[float, float, float]:
    portfolio = runtime.portfolio
    inventory_usd = portfolio.inventory_usd(mid)
    equity_usd = portfolio.total_equity_usd(mid)

    if runtime.start_eq is None:
        runtime.start_eq = equity_usd
        runtime.equity_peak = equity_usd

    pnl_usd = equity_usd - runtime.start_eq
    return inventory_usd, equity_usd, pnl_usd


def track_runtime_state(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    equity_usd: float,
    pnl_usd: float,
    *,
    record_equity: bool = False,
) -> None:
    runtime.inventory_min = inventory_usd if runtime.inventory_min is None else min(runtime.inventory_min, inventory_usd)
    runtime.inventory_max = inventory_usd if runtime.inventory_max is None else max(runtime.inventory_max, inventory_usd)
    runtime.max_pnl = pnl_usd if runtime.max_pnl is None else max(runtime.max_pnl, pnl_usd)
    runtime.min_pnl = pnl_usd if runtime.min_pnl is None else min(runtime.min_pnl, pnl_usd)
    runtime.equity_peak = equity_usd if runtime.equity_peak is None else max(runtime.equity_peak, equity_usd)
    runtime.max_drawdown_usd = max(runtime.max_drawdown_usd, runtime.equity_peak - equity_usd)
    if runtime.equity_peak and runtime.equity_peak > 0:
        drawdown_pct = max((runtime.equity_peak - equity_usd) / runtime.equity_peak, 0.0)
        runtime.max_drawdown_pct = max(runtime.max_drawdown_pct, drawdown_pct)
    runtime.performance.record_equity(
        cycle_index=cycle_index,
        mid_price=mid,
        equity_usd=equity_usd,
        inventory_usd=inventory_usd,
    )
    if record_equity:
        runtime.recent_equities.append(equity_usd)


def track_inventory_ratio(runtime: BotRuntime, inventory_ratio: float) -> None:
    runtime.inventory_ratio_min = (
        inventory_ratio
        if runtime.inventory_ratio_min is None
        else min(runtime.inventory_ratio_min, inventory_ratio)
    )
    runtime.inventory_ratio_max = (
        inventory_ratio
        if runtime.inventory_ratio_max is None
        else max(runtime.inventory_ratio_max, inventory_ratio)
    )


def sync_daily_risk_state(
    runtime: BotRuntime,
    equity_usd: float,
    *,
    current_date: date | None = None,
) -> float:
    today = (current_date or date.today()).isoformat()
    if runtime.daily_reset_date != today or runtime.daily_start_equity is None:
        runtime.daily_reset_date = today
        runtime.daily_start_equity = equity_usd
        runtime.daily_start_realized_pnl = runtime.portfolio.realized_pnl_usd
        runtime.daily_trade_count = 0

    runtime.daily_pnl_usd = equity_usd - runtime.daily_start_equity
    return runtime.daily_pnl_usd


def projected_exposure_usd(side: str, inventory_usd: float, size_usd: float) -> float:
    normalized_side = side.strip().lower()
    if normalized_side == "buy":
        return max(inventory_usd + max(size_usd, 0.0), 0.0)
    if normalized_side == "sell":
        return max(inventory_usd - max(size_usd, 0.0), 0.0)
    return max(inventory_usd, 0.0)


def risk_limit_filter_values(
    runtime: BotRuntime,
    *,
    inventory_usd: float,
    equity_usd: float,
    side: str = "",
    trade_size_usd: float = 0.0,
) -> dict[str, object]:
    daily_pnl_usd = sync_daily_risk_state(runtime, equity_usd)
    projected = projected_exposure_usd(side, inventory_usd, trade_size_usd) if side else inventory_usd
    sizing = _current_sizing(runtime)
    return {
        "daily_pnl_usd": round(daily_pnl_usd, 6),
        "exposure_usd": round(inventory_usd, 6),
        "projected_exposure_usd": round(projected, 6),
        "max_trade_size_usd": round(sizing.max_trade_size_usd, 6),
        "max_position_usd": round(sizing.max_position_usd, 6),
        "max_daily_loss_usd": round(MAX_DAILY_LOSS_USD, 6),
        "max_exposure_usd": round(MAX_EXPOSURE_USD, 6),
        "limit_side": side or "-",
        "limit_trade_size_usd": round(trade_size_usd, 6),
    }


def evaluate_runtime_limits(
    runtime: BotRuntime,
    *,
    inventory_usd: float,
    equity_usd: float,
    current_date: date | None = None,
) -> RiskLimitDecision:
    daily_pnl_usd = sync_daily_risk_state(runtime, equity_usd, current_date=current_date)
    exposure_usd = max(inventory_usd, 0.0)

    if MAX_DAILY_LOSS_USD > 0 and daily_pnl_usd <= (-MAX_DAILY_LOSS_USD):
        return RiskLimitDecision(
            stop_trading=True,
            reason="max_daily_loss_limit",
            details=(
                f"daily_pnl {daily_pnl_usd:.2f} <= -{MAX_DAILY_LOSS_USD:.2f} "
                f"| exposure {exposure_usd:.2f}"
            ),
            daily_pnl_usd=daily_pnl_usd,
            exposure_usd=exposure_usd,
        )

    if MAX_EXPOSURE_USD > 0 and exposure_usd > MAX_EXPOSURE_USD:
        return RiskLimitDecision(
            stop_trading=False,
            reason="max_exposure_soft_limit",
            details=(
                f"exposure {exposure_usd:.2f} > {MAX_EXPOSURE_USD:.2f} "
                f"| daily_pnl {daily_pnl_usd:.2f}"
            ),
            daily_pnl_usd=daily_pnl_usd,
            exposure_usd=exposure_usd,
        )

    return RiskLimitDecision(
        stop_trading=False,
        daily_pnl_usd=daily_pnl_usd,
        exposure_usd=exposure_usd,
    )


def evaluate_trade_limits(
    runtime: BotRuntime,
    *,
    side: str,
    trade_size_usd: float,
    inventory_usd: float,
    equity_usd: float,
    current_date: date | None = None,
) -> RiskLimitDecision:
    daily_pnl_usd = sync_daily_risk_state(runtime, equity_usd, current_date=current_date)
    exposure_usd = max(inventory_usd, 0.0)
    projected_exposure = projected_exposure_usd(side, exposure_usd, trade_size_usd)
    dynamic_max_trade_size_usd = max_trade_size_usd(runtime)
    dynamic_max_position_usd = max_position_usd(runtime)

    if dynamic_max_trade_size_usd > 0 and trade_size_usd > dynamic_max_trade_size_usd:
        return RiskLimitDecision(
            stop_trading=False,
            reason="risk_stop_size_exceeded",
            details=(
                f"trade_size {trade_size_usd:.2f} > {dynamic_max_trade_size_usd:.2f} "
                f"| side {side}"
            ),
            daily_pnl_usd=daily_pnl_usd,
            exposure_usd=exposure_usd,
            projected_exposure_usd=projected_exposure,
            trade_size_usd=trade_size_usd,
        )

    if side.strip().lower() == "buy" and dynamic_max_position_usd > 0 and projected_exposure > dynamic_max_position_usd:
        return RiskLimitDecision(
            stop_trading=False,
            reason="max_position_pct_limit",
            details=(
                f"projected_position {projected_exposure:.2f} > {dynamic_max_position_usd:.2f} "
                f"| trade_size {trade_size_usd:.2f}"
            ),
            daily_pnl_usd=daily_pnl_usd,
            exposure_usd=exposure_usd,
            projected_exposure_usd=projected_exposure,
            trade_size_usd=trade_size_usd,
        )

    if side.strip().lower() == "buy" and MAX_EXPOSURE_USD > 0 and projected_exposure > MAX_EXPOSURE_USD:
        return RiskLimitDecision(
            stop_trading=False,
            reason="max_exposure_soft_limit",
            details=(
                f"projected_exposure {projected_exposure:.2f} > {MAX_EXPOSURE_USD:.2f} "
                f"| trade_size {trade_size_usd:.2f}"
            ),
            daily_pnl_usd=daily_pnl_usd,
            exposure_usd=exposure_usd,
            projected_exposure_usd=projected_exposure,
            trade_size_usd=trade_size_usd,
        )

    return RiskLimitDecision(
        stop_trading=False,
        daily_pnl_usd=daily_pnl_usd,
        exposure_usd=exposure_usd,
        projected_exposure_usd=projected_exposure,
        trade_size_usd=trade_size_usd,
    )


def cycles_for_minutes(runtime: BotRuntime, minutes: float) -> int:
    if minutes <= 0:
        return 1
    cycle_seconds = max(runtime.cycle_seconds, 1.0)
    return max(int(math.ceil((minutes * 60.0) / cycle_seconds)), 1)


def reentry_budget_usd(
    runtime: BotRuntime,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    fallback_trade_size_usd: float,
) -> float:
    del inventory_usd, effective_max_inventory_usd
    state = runtime.reentry_state
    if state.last_sell_size_usd <= 0 and fallback_trade_size_usd <= 0:
        return 0.0

    base_budget = max(state.last_sell_size_usd, fallback_trade_size_usd)
    available_profit = max(runtime.portfolio.realized_pnl_usd, 0.0)
    profit_boost = min(available_profit * ETH_ACCUMULATION_REINVEST_PCT, base_budget * 0.25)
    available_usdc = max(_current_sizing(runtime).available_quote_to_trade_usd, 0.0)
    return max(min(base_budget + profit_boost, available_usdc), 0.0)


def activate_reentry_state(
    runtime: BotRuntime,
    cycle_index: int,
    sell_price: float,
    sell_size_usd: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    fallback_trade_size_usd: float,
) -> None:
    state = runtime.reentry_state
    state.active = sell_price > 0
    state.last_sell_price = sell_price if sell_price > 0 else state.last_sell_price
    state.last_sell_size_usd = max(sell_size_usd, 0.0)
    state.last_sell_cycle = cycle_index
    state.buy_zones = calculate_buy_zones(
        sell_price,
        (
            REENTRY_ZONE_1_MULTIPLIER,
            REENTRY_ZONE_2_MULTIPLIER,
            REENTRY_ZONE_3_MULTIPLIER,
        ),
    )
    state.executed_buy_levels.clear()
    state.budget_usd = reentry_budget_usd(
        runtime=runtime,
        inventory_usd=inventory_usd,
        effective_max_inventory_usd=effective_max_inventory_usd,
        fallback_trade_size_usd=fallback_trade_size_usd,
    )
    state.spent_usd = 0.0
    state.timeout_cycle = cycle_index + cycles_for_minutes(runtime, REENTRY_TIMEOUT_MINUTES)
    state.timeout_triggered = False
    state.runaway_triggered = False
    state.max_miss_triggered = False
    state.highest_price_since_sell = sell_price
    state.lowest_price_since_sell = sell_price


def clear_profit_lock_state(runtime: BotRuntime) -> None:
    runtime.profit_lock_state = ProfitLockState()


def reset_profit_lock_state(runtime: BotRuntime, anchor_price: float) -> None:
    runtime.profit_lock_state = ProfitLockState(
        anchor_price=anchor_price if anchor_price > 0 else None,
        highest_price=anchor_price if anchor_price > 0 else None,
    )


def update_reentry_state(runtime: BotRuntime, mid: float) -> None:
    state = runtime.reentry_state
    if not state.active:
        return

    if state.highest_price_since_sell is None:
        state.highest_price_since_sell = mid
    else:
        state.highest_price_since_sell = max(state.highest_price_since_sell, mid)

    if state.lowest_price_since_sell is None:
        state.lowest_price_since_sell = mid
    else:
        state.lowest_price_since_sell = min(state.lowest_price_since_sell, mid)


def update_profit_lock_state(runtime: BotRuntime, mid: float) -> None:
    state = runtime.profit_lock_state
    if state.anchor_price is None:
        return

    if state.highest_price is None:
        state.highest_price = mid
    else:
        state.highest_price = max(state.highest_price, mid)

    level_one_bps, level_two_bps = _profit_lock_thresholds_bps(runtime)
    level_one_target = state.anchor_price * (1.0 + (level_one_bps / 10000.0))
    level_two_target = state.anchor_price * (1.0 + (level_two_bps / 10000.0))
    if not state.level_one_executed and mid >= level_one_target:
        state.level_one_armed = True
    if not state.level_two_executed and mid >= level_two_target:
        state.level_two_armed = True


def profit_lock_anchor_price(runtime: BotRuntime) -> float | None:
    anchor_price = runtime.profit_lock_state.anchor_price
    if anchor_price is not None and anchor_price > 0:
        return anchor_price

    cost_basis = runtime.portfolio.eth_cost_basis
    if cost_basis is not None and cost_basis > 0:
        return cost_basis

    return None


def current_profit_pct(runtime: BotRuntime, mid: float) -> float | None:
    anchor_price = profit_lock_anchor_price(runtime)
    if anchor_price is None or anchor_price <= 0 or mid <= 0:
        return None
    return ((mid / anchor_price) - 1.0) * 100.0


def position_hold_minutes(runtime: BotRuntime, cycle_index: int) -> float:
    open_cycle = getattr(runtime, "open_position_cycle", None)
    if open_cycle is None:
        return 0.0
    elapsed_cycles = max(cycle_index - open_cycle, 0)
    return (elapsed_cycles * max(runtime.cycle_seconds, 1.0)) / 60.0


def time_in_state_seconds(runtime: BotRuntime, cycle_index: int) -> float:
    if not runtime.enable_state_machine:
        return 0.0
    return runtime.state_machine.time_in_state_seconds(runtime.state_context, cycle_index)


def last_transition(runtime: BotRuntime) -> str:
    if not runtime.enable_state_machine:
        return ""
    return runtime.state_context.last_transition


def build_accumulating_failsafe_sell_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
) -> tuple[str | None, float]:
    if mid <= 0 or not runtime.enable_state_machine:
        return None, 0.0

    if runtime.state_context.current_state != StrategyState.ACCUMULATING:
        return None, 0.0

    if not runtime.state_machine.accumulating_failsafe_due(runtime.state_context, cycle_index):
        return None, 0.0

    tradable_eth = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0)
    if tradable_eth <= 0:
        return None, 0.0

    return "time_exit_sell", tradable_eth * mid


def reentry_timeout_remaining(runtime: BotRuntime, cycle_index: int) -> int:
    timeout_cycle = runtime.reentry_state.timeout_cycle
    if timeout_cycle is None:
        return 0
    return max(timeout_cycle - cycle_index, 0)


def minutes_since_last_trade(runtime: BotRuntime, cycle_index: int) -> float:
    if runtime.last_trade_cycle_any is None:
        return ((cycle_index + 1) * runtime.cycle_seconds) / 60.0
    return max(((cycle_index - runtime.last_trade_cycle_any) * runtime.cycle_seconds) / 60.0, 0.0)


def force_trade_due(runtime: BotRuntime, cycle_index: int) -> bool:
    if MAX_TRADES_PER_DAY > 0 and runtime.daily_trade_count >= MAX_TRADES_PER_DAY:
        return False
    return TRADE_FILTER_FORCE_TRADE_MINUTES > 0 and minutes_since_last_trade(
        runtime,
        cycle_index,
    ) >= TRADE_FILTER_FORCE_TRADE_MINUTES


def force_trade_size_usd(runtime: BotRuntime, base_trade_size_usd: float) -> float:
    del base_trade_size_usd
    return max(_current_sizing(runtime).force_trade_size_usd, 0.0)


def remaining_reentry_budget(state: ReentryState) -> float:
    return max(state.budget_usd - state.spent_usd, 0.0)


def reentry_buy_inventory_cap(
    runtime: BotRuntime,
    inventory_usd: float,
    effective_max_inventory_usd: float,
) -> float:
    dynamic_cap = effective_max_inventory_usd * (1.0 + max(REENTRY_INVENTORY_BUFFER_PCT, 0.0))
    budget_cap = inventory_usd + remaining_reentry_budget(runtime.reentry_state)
    return max(dynamic_cap, budget_cap)


def should_activate_reentry_after_sell(runtime: BotRuntime, portfolio_eth: float, mid: float) -> bool:
    min_notional = min_notional_usd(runtime)
    tradable_eth = max(portfolio_eth - runtime.engine.min_eth_reserve, 0.0)
    if tradable_eth <= 1e-9:
        return True
    if mid <= 0:
        return False
    return (tradable_eth * mid) < min_notional


def available_buy_room_usd(inventory_usd: float, effective_max_inventory_usd: float) -> float:
    inventory_cap = effective_max_inventory_usd * (1.0 + max(REENTRY_INVENTORY_BUFFER_PCT, 0.0))
    return max(inventory_cap - inventory_usd, 0.0)


def build_reentry_buy_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    trend_buy_allowed: bool,
    buy_confirmation: bool,
) -> tuple[str | None, float]:
    del cycle_index, trend_buy_allowed, buy_confirmation
    state = runtime.reentry_state
    min_notional = min_notional_usd(runtime)
    sizing = _current_sizing(runtime)
    if not runtime.enable_reentry_engine or not state.active or state.last_sell_price is None or mid <= 0:
        return None, 0.0

    remaining_budget = remaining_reentry_budget(state)
    if remaining_budget < min_notional:
        state.active = False
        return None, 0.0

    room_usd = min(
        available_buy_room_usd(inventory_usd, effective_max_inventory_usd),
        sizing.available_quote_to_trade_usd,
    )
    if room_usd < min_notional:
        return None, 0.0

    zones = (
        ("zone_1", state.buy_zones[0], REENTRY_ZONE_1_BUY_FRACTION),
        ("zone_2", state.buy_zones[1], REENTRY_ZONE_2_BUY_FRACTION),
        ("zone_3", state.buy_zones[2], REENTRY_ZONE_3_BUY_FRACTION),
    )
    for level_name, zone_price, fraction in zones:
        if level_name in state.executed_buy_levels or zone_price <= 0:
            continue
        if mid <= zone_price:
            size_usd = min(state.budget_usd * fraction, remaining_budget, room_usd)
            return f"reentry_{level_name}", size_usd

    return None, 0.0


def build_partial_reset_buy_plan(
    runtime: BotRuntime,
    equity_usd: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    base_trade_size_usd: float,
    trend_buy_allowed: bool,
    buy_confirmation: bool,
    target_inventory_pct: float,
) -> tuple[str | None, float]:
    if not runtime.enable_reentry_engine or equity_usd <= 0:
        return None, 0.0

    if runtime.reentry_state.active:
        return None, 0.0

    sizing = _current_sizing(runtime)
    min_notional = min_notional_usd(runtime)
    usdc_share = runtime.portfolio.usdc / equity_usd
    target_inventory_usd = equity_usd * max(target_inventory_pct, 0.0)
    if usdc_share < PARTIAL_RESET_USDC_THRESHOLD_PCT or inventory_usd >= target_inventory_usd:
        return None, 0.0

    room_usd = min(
        available_buy_room_usd(inventory_usd, effective_max_inventory_usd),
        sizing.available_quote_to_trade_usd,
    )
    size_usd = min(base_trade_size_usd * PARTIAL_RESET_BUY_FRACTION, room_usd)
    if size_usd < min_notional:
        return None, 0.0
    return "partial_reset", size_usd


def build_force_trade_candidate(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    buy_state_allowed: bool,
    sell_state_allowed: bool,
    state_requires_reentry_only: bool,
    in_cooldown: bool,
    base_trade_size_usd: float,
) -> DecisionOutcome | None:
    if mid <= 0 or in_cooldown or not force_trade_due(runtime, cycle_index):
        return None

    sizing = _current_sizing(runtime)
    min_notional = min_notional_usd(runtime)
    size_usd = force_trade_size_usd(runtime, base_trade_size_usd)
    if size_usd < min_notional:
        return None

    current_state = runtime.state_context.current_state if runtime.enable_state_machine else StrategyState.IDLE
    tradable_eth_usd = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0) * mid
    inventory_direction = trade_direction_to_target(sizing, inventory_usd)
    available_quote_usd = sizing.available_quote_to_trade_usd
    sellable_eth_usd = max(tradable_eth_usd - sizing.base_reserve_usd, 0.0)
    buy_inventory_cap = min(max(effective_max_inventory_usd, inventory_usd + size_usd), sizing.max_position_usd or effective_max_inventory_usd)

    if (
        inventory_direction == "buy"
        and (state_requires_reentry_only or current_state in {StrategyState.IDLE, StrategyState.WAIT_REENTRY})
    ):
        buy_size_usd = min(size_usd, available_quote_usd, max(sizing.target_base_usd - inventory_usd, 0.0))
        if buy_size_usd >= min_notional:
            return DecisionOutcome(
                action="BUY",
                size_usd=buy_size_usd,
                reason="force_trade_buy",
                source="force_trade",
                order_price=mid,
                inventory_cap_usd=buy_inventory_cap,
            )

    if inventory_direction == "sell" and sell_state_allowed and sellable_eth_usd >= min_notional:
        sell_size_usd = min(size_usd, sellable_eth_usd, max(inventory_usd - sizing.target_base_usd, 0.0))
        if sell_size_usd < min_notional:
            return None
        return DecisionOutcome(
            action="SELL",
            size_usd=sell_size_usd,
            reason="force_trade_sell",
            source="force_trade",
            order_price=mid,
            inventory_cap_usd=buy_inventory_cap,
        )

    if inventory_direction in {"buy", "neutral"} and buy_state_allowed and available_quote_usd >= min_notional:
        buy_size_usd = min(size_usd, available_quote_usd, max(sizing.max_position_usd - inventory_usd, 0.0))
        if buy_size_usd >= min_notional:
            return DecisionOutcome(
                action="BUY",
                size_usd=buy_size_usd,
                reason="force_trade_buy",
                source="force_trade",
                order_price=mid,
                inventory_cap_usd=buy_inventory_cap,
            )

    return None


def build_profit_lock_sell_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
) -> tuple[str | None, float]:
    if mid <= 0:
        return None, 0.0

    if runtime.enable_state_machine and runtime.state_context.current_state == StrategyState.ACCUMULATING:
        if runtime.state_machine.accumulating_failsafe_due(runtime.state_context, cycle_index):
            tradable_eth = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0)
            if tradable_eth > 0:
                return "time_exit_sell", tradable_eth * mid

    state = runtime.profit_lock_state
    current_profit = current_profit_pct(runtime, mid)
    if current_profit is None:
        return None, 0.0

    tradable_eth = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0)
    if tradable_eth <= 0:
        return None, 0.0

    regime = getattr(runtime, "current_regime_assessment", None)
    hold_minutes = position_hold_minutes(runtime, cycle_index)

    if current_profit <= (STOP_LOSS_PCT + PROFIT_THRESHOLD_EPSILON_PCT):
        return "stop_loss_sell", tradable_eth * mid

    if state.level_one_executed and state.level_two_executed:
        return None, 0.0

    level_one_bps, level_two_bps = _profit_lock_thresholds_bps(runtime)
    active_regime = getattr(runtime, "current_market_mode", "") or getattr(runtime, "current_active_regime", "") or "TREND"
    time_stop_minutes, max_hold_minutes = resolve_hold_limits_minutes(
        active_regime,
        getattr(runtime, "current_volatility_bucket", ""),
    )
    if regime is not None and getattr(regime, "execution_regime", "") == "RANGE":
        price_position_pct = getattr(regime, "price_position_pct", 0.5)
        if current_profit >= -0.05 and price_position_pct >= RANGE_EXIT_MIN_POSITION_PCT:
            return "range_sell", tradable_eth * mid
        if (
            current_profit >= 0.0
            and price_position_pct >= RANGE_MEAN_REVERSION_EXIT_POSITION_PCT
            and getattr(runtime, "open_position_reason", "") in {"range_buy", "inventory_rebalance", "partial_reset"}
        ):
            return "mean_reversion_exit", tradable_eth * mid
    if (
        time_stop_minutes > 0
        and hold_minutes >= time_stop_minutes
        and current_profit < ((level_one_bps / 100.0) - PROFIT_THRESHOLD_EPSILON_PCT)
    ):
        return "time_exit_sell", tradable_eth * mid
    if (
        max_hold_minutes > 0
        and hold_minutes >= max_hold_minutes
        and current_profit < ((level_two_bps / 100.0) - PROFIT_THRESHOLD_EPSILON_PCT)
    ):
        return "time_exit_sell", tradable_eth * mid

    if not state.level_two_executed and current_profit >= ((level_two_bps / 100.0) - PROFIT_THRESHOLD_EPSILON_PCT):
        return "profit_lock_level_2", tradable_eth * mid

    if not state.level_one_executed and current_profit >= ((level_one_bps / 100.0) - PROFIT_THRESHOLD_EPSILON_PCT):
        size_usd = tradable_eth * mid * PROFIT_LOCK_LEVEL_1_SELL_FRACTION
        return "profit_lock_level_1", size_usd

    return None, 0.0


def should_delay_regular_sell(runtime: BotRuntime, mode: str) -> bool:
    if mode == "OVERWEIGHT_EXIT":
        return False
    if profit_lock_anchor_price(runtime) is None:
        return False
    state = runtime.profit_lock_state
    return not (state.level_one_executed and state.level_two_executed)


def cap_inventory_preserving_sell_order(runtime: BotRuntime, sell_order, sell_reason: str) -> None:
    preservation_floor_eth = runtime.engine.min_eth_reserve
    if runtime.enable_reentry_engine and sell_reason in {"quoted_sell", "inventory_correction"}:
        preservation_floor_eth = max(
            runtime.start_eth * max(ETH_PRESERVATION_FLOOR_MULTIPLIER, 0.0),
            runtime.engine.min_eth_reserve,
        )
    sellable_eth = max(runtime.portfolio.eth - preservation_floor_eth, 0.0)
    if sellable_eth <= 0:
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0
        return

    if sell_order.size_base > sellable_eth:
        sell_order.size_base = sellable_eth
        sell_order.size_usd = sell_order.size_base * sell_order.price


def allows_opposite_side_trade(
    runtime: BotRuntime,
    cycle_index: int,
    side: str,
    order_price: float,
) -> bool:
    if runtime.last_fill_side is None or runtime.last_fill_cycle is None or runtime.last_fill_price is None:
        return True

    if runtime.last_fill_side == side:
        return True

    cycles_since_flip = cycle_index - runtime.last_fill_cycle
    if cycles_since_flip < max(SIDE_FLIP_COOLDOWN_CYCLES, 0):
        return False

    if SIDE_FLIP_MIN_BPS <= 0 or order_price <= 0:
        return True

    min_move_multiplier = SIDE_FLIP_MIN_BPS / 10000.0
    if side == "buy":
        return order_price <= runtime.last_fill_price * (1.0 - min_move_multiplier)

    return order_price >= runtime.last_fill_price * (1.0 + min_move_multiplier)
