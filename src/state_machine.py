from __future__ import annotations

import json
import math

from config import (
    STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES,
    STATE_MACHINE_COOLDOWN_MINUTES,
    STATE_MACHINE_LOSS_STREAK_LIMIT,
)
from types_bot import ReentryState, StateMachineContext, StrategyState


class StateMachineEngine:
    def __init__(self, cycle_seconds: float):
        self.cycle_seconds = max(cycle_seconds, 1.0)

    def _cooldown_cycles(self) -> int:
        if STATE_MACHINE_COOLDOWN_MINUTES <= 0:
            return 0
        return max(int(math.ceil((STATE_MACHINE_COOLDOWN_MINUTES * 60.0) / self.cycle_seconds)), 1)

    def _accumulating_failsafe_cycles(self) -> int:
        if STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES <= 0:
            return 0
        return max(int(math.ceil((STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES * 60.0) / self.cycle_seconds)), 1)

    def transition(
        self,
        context: StateMachineContext,
        new_state: StrategyState,
        cycle_index: int,
        reason: str,
    ) -> None:
        if context.current_state == new_state and context.transition_reason == reason:
            return

        previous_state = context.current_state.value
        context.previous_state = previous_state
        context.current_state = new_state
        context.entered_cycle = cycle_index
        context.transition_reason = reason
        context.last_transition = f"{previous_state}->{new_state.value}:{reason}"
        context.last_transition_cycle = cycle_index
        if new_state != StrategyState.COOLDOWN:
            context.cooldown_until_cycle = None

    def serialize(self, context: StateMachineContext) -> str:
        return json.dumps(
            {
                "current_state": context.current_state.value,
                "previous_state": context.previous_state,
                "entered_cycle": context.entered_cycle,
                "transition_reason": context.transition_reason,
                "last_transition": context.last_transition,
                "last_transition_cycle": context.last_transition_cycle,
                "cooldown_until_cycle": context.cooldown_until_cycle,
            },
            separators=(",", ":"),
        )

    def time_in_state_cycles(self, context: StateMachineContext, cycle_index: int) -> int:
        return max(cycle_index - context.entered_cycle, 0)

    def time_in_state_seconds(self, context: StateMachineContext, cycle_index: int) -> float:
        return self.time_in_state_cycles(context, cycle_index) * self.cycle_seconds

    def cooldown_remaining_cycles(self, context: StateMachineContext, cycle_index: int) -> int:
        if context.cooldown_until_cycle is None:
            return 0
        return max(context.cooldown_until_cycle - cycle_index, 0)

    def accumulating_failsafe_due(self, context: StateMachineContext, cycle_index: int) -> bool:
        if context.current_state != StrategyState.ACCUMULATING:
            return False

        failsafe_cycles = self._accumulating_failsafe_cycles()
        if failsafe_cycles <= 0:
            return False

        return self.time_in_state_cycles(context, cycle_index) >= failsafe_cycles

    def _resting_state(
        self,
        reentry_state: ReentryState,
        portfolio_eth: float,
        min_eth_reserve: float,
    ) -> StrategyState:
        if reentry_state.active:
            return StrategyState.WAIT_REENTRY
        if portfolio_eth > (min_eth_reserve + 1e-9):
            return StrategyState.ACCUMULATING
        return StrategyState.IDLE

    def sync_cycle(
        self,
        context: StateMachineContext,
        cycle_index: int,
        reentry_state: ReentryState,
        portfolio_eth: float,
        min_eth_reserve: float,
    ) -> None:
        if context.current_state == StrategyState.COOLDOWN:
            remaining = self.cooldown_remaining_cycles(context, cycle_index)
            if remaining <= 0:
                self.transition(
                    context,
                    self._resting_state(reentry_state, portfolio_eth, min_eth_reserve),
                    cycle_index,
                    "cooldown_expired",
                )
            return

        resting_state = self._resting_state(reentry_state, portfolio_eth, min_eth_reserve)

        if context.current_state == StrategyState.WAIT_REENTRY and not reentry_state.active:
            self.transition(
                context,
                resting_state,
                cycle_index,
                "reentry_inactive",
            )
            return

        if context.current_state == StrategyState.IDLE and resting_state != StrategyState.IDLE:
            reason = "reentry_armed" if resting_state == StrategyState.WAIT_REENTRY else "inventory_detected"
            self.transition(context, resting_state, cycle_index, reason)
            return

        if context.current_state == StrategyState.ACCUMULATING and resting_state != StrategyState.ACCUMULATING:
            reason = "reentry_armed" if resting_state == StrategyState.WAIT_REENTRY else "inventory_depleted"
            self.transition(context, resting_state, cycle_index, reason)
            return

        if context.current_state == StrategyState.DISTRIBUTING and resting_state != StrategyState.DISTRIBUTING:
            self.transition(context, resting_state, cycle_index, "distribution_settled")

    def maybe_enter_cooldown(
        self,
        context: StateMachineContext,
        cycle_index: int,
        loss_streak: int,
    ) -> bool:
        if loss_streak < STATE_MACHINE_LOSS_STREAK_LIMIT:
            return False

        cooldown_cycles = self._cooldown_cycles()
        if cooldown_cycles <= 0:
            return False

        context.cooldown_until_cycle = cycle_index + cooldown_cycles
        self.transition(context, StrategyState.COOLDOWN, cycle_index, "loss_streak_limit")
        context.cooldown_until_cycle = cycle_index + cooldown_cycles
        return True

    def prepare_distribution(
        self,
        context: StateMachineContext,
        cycle_index: int,
        trade_reason: str,
    ) -> None:
        if context.current_state in {StrategyState.COOLDOWN, StrategyState.WAIT_REENTRY}:
            return
        self.transition(context, StrategyState.DISTRIBUTING, cycle_index, f"sell_setup:{trade_reason}")

    def handle_buy_fill(
        self,
        context: StateMachineContext,
        cycle_index: int,
        trade_reason: str,
    ) -> None:
        self.transition(context, StrategyState.ACCUMULATING, cycle_index, f"buy_fill:{trade_reason}")

    def handle_sell_fill(
        self,
        context: StateMachineContext,
        cycle_index: int,
        trade_reason: str,
        reentry_state: ReentryState,
        portfolio_eth: float,
        min_eth_reserve: float,
    ) -> None:
        next_state = StrategyState.WAIT_REENTRY if reentry_state.active else self._resting_state(
            reentry_state,
            portfolio_eth,
            min_eth_reserve,
        )
        self.transition(context, next_state, cycle_index, f"sell_fill:{trade_reason}")

    def allow_buy(self, context: StateMachineContext) -> bool:
        return context.current_state in {StrategyState.IDLE, StrategyState.WAIT_REENTRY}

    def allow_sell(self, context: StateMachineContext) -> bool:
        return context.current_state in {
            StrategyState.IDLE,
            StrategyState.ACCUMULATING,
            StrategyState.DISTRIBUTING,
        }

    def requires_reentry_only(self, context: StateMachineContext) -> bool:
        return context.current_state == StrategyState.WAIT_REENTRY

    def in_cooldown(self, context: StateMachineContext) -> bool:
        return context.current_state == StrategyState.COOLDOWN
