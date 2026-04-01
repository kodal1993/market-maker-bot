from __future__ import annotations

import json
import math

from config import (
    MIN_ORDER_SIZE_USD,
    REENTRY_MAX_MISS_BUY_FRACTION,
    REENTRY_MAX_MISS_PCT,
    REENTRY_RUNAWAY_BUY_FRACTION,
    REENTRY_TIMEOUT_BUY_FRACTION,
    REENTRY_TIMEOUT_MINUTES,
    REENTRY_ZONE_1_BUY_FRACTION,
    REENTRY_ZONE_1_MULTIPLIER,
    REENTRY_ZONE_2_BUY_FRACTION,
    REENTRY_ZONE_2_MULTIPLIER,
    REENTRY_ZONE_3_BUY_FRACTION,
    REENTRY_ZONE_3_MULTIPLIER,
    WAIT_REENTRY_PULLBACK_PCT,
)
from strategy import calculate_buy_zones
from types_bot import ReentryPlan, ReentryState


class ReentryEngine:
    def __init__(self, cycle_seconds: float):
        self.cycle_seconds = max(cycle_seconds, 1.0)

    def _cycles_for_minutes(self, minutes: float) -> int:
        if minutes <= 0:
            return 1
        return max(int(math.ceil((minutes * 60.0) / self.cycle_seconds)), 1)

    def activate_after_sell(
        self,
        state: ReentryState,
        cycle_index: int,
        sell_price: float,
        sell_size_usd: float,
        budget_usd: float,
    ) -> None:
        state.active = sell_price > 0 and budget_usd > 0
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
        state.budget_usd = max(budget_usd, 0.0)
        state.spent_usd = 0.0
        state.timeout_cycle = cycle_index + self._cycles_for_minutes(REENTRY_TIMEOUT_MINUTES)
        state.timeout_triggered = False
        state.runaway_triggered = False
        state.max_miss_triggered = False
        state.highest_price_since_sell = sell_price
        state.lowest_price_since_sell = sell_price

    def update_state(self, state: ReentryState, mid: float) -> None:
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

    def serialize_state(self, state: ReentryState) -> str:
        if not state.last_sell_price:
            return ""
        return json.dumps(
            {
                "last_sell_price": round(state.last_sell_price, 6),
                "buy_zones": [round(price, 6) for price in state.buy_zones],
                "executed_buy_levels": list(state.executed_buy_levels),
                "active": state.active,
                "timeout_cycle": state.timeout_cycle,
                "timeout_triggered": state.timeout_triggered,
                "runaway_triggered": state.runaway_triggered,
                "max_miss_triggered": state.max_miss_triggered,
            },
            separators=(",", ":"),
        )

    def timeout_remaining_cycles(self, state: ReentryState, cycle_index: int) -> int:
        if state.timeout_cycle is None:
            return 0
        return max(state.timeout_cycle - cycle_index, 0)

    def remaining_budget(self, state: ReentryState) -> float:
        return max(state.budget_usd - state.spent_usd, 0.0)

    def build_scale_in_plan(
        self,
        state: ReentryState,
        cycle_index: int,
        mid: float,
        room_usd: float,
        buy_confirmation: bool,
        trend_buy_allowed: bool,
    ) -> ReentryPlan:
        if not state.active or state.last_sell_price is None or mid <= 0:
            return ReentryPlan(False)

        remaining_budget = self.remaining_budget(state)
        if remaining_budget <= 0 or room_usd <= 0:
            return ReentryPlan(False)

        pullback_price = state.last_sell_price * (1.0 - (WAIT_REENTRY_PULLBACK_PCT / 100.0))
        if (
            pullback_price > 0
            and "pullback" not in state.executed_buy_levels
            and mid <= pullback_price
        ):
            size_usd = min(remaining_budget, room_usd)
            return ReentryPlan(size_usd >= MIN_ORDER_SIZE_USD, "reentry_pullback", size_usd)

        if (
            state.timeout_cycle is not None
            and cycle_index >= state.timeout_cycle
            and not state.timeout_triggered
        ):
            size_usd = min(
                max(state.budget_usd * REENTRY_TIMEOUT_BUY_FRACTION, MIN_ORDER_SIZE_USD),
                remaining_budget,
                room_usd,
            )
            return ReentryPlan(size_usd >= MIN_ORDER_SIZE_USD, "reentry_timeout", size_usd)

        max_miss_price = state.last_sell_price * (1.0 + REENTRY_MAX_MISS_PCT)
        if (
            not state.max_miss_triggered
            and mid >= max_miss_price
            and (buy_confirmation or trend_buy_allowed)
        ):
            size_usd = min(
                max(state.budget_usd * REENTRY_MAX_MISS_BUY_FRACTION, MIN_ORDER_SIZE_USD),
                remaining_budget,
                room_usd,
            )
            return ReentryPlan(size_usd >= MIN_ORDER_SIZE_USD, "reentry_max_miss", size_usd)

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
                return ReentryPlan(size_usd > 0, f"reentry_{level_name}", size_usd)

        return ReentryPlan(False)
