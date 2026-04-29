from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import (
    _build_activity_floor_candidate,
    _cap_paper_activity_exit_sell_size,
    _side_flip_exempt,
    _update_runtime_sizing,
    create_runtime,
)
from types_bot import DecisionOutcome, StrategyState


def prepare_activity_runtime(*, start_usdc: float, start_eth: float, mid: float = 100.0):
    runtime = create_runtime(
        bootstrap_prices=[],
        reference_price=mid,
        start_usdc=start_usdc,
        start_eth=start_eth,
        start_eth_usd=0.0,
        cycle_seconds=1.0,
        enable_execution_engine=False,
    )
    equity_usd = runtime.portfolio.total_equity_usd(mid)
    _update_runtime_sizing(runtime, equity_usd=equity_usd, mid=mid)
    runtime.current_activity_floor_state = "filters_relaxed"
    runtime.current_paper_activity_override = True
    runtime.current_quote_enabled = True
    runtime.current_strategy_mode = "RANGE_MAKER"
    runtime.current_defensive_stage = 0
    runtime.state_context.current_state = StrategyState.ACCUMULATING
    return runtime, equity_usd


class ActivityFloorCandidateTests(unittest.TestCase):
    def test_activity_floor_builds_force_sell_when_initial_inventory_is_accumulating(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=250.0, start_eth=2.5)
        inventory_usd = runtime.portfolio.inventory_usd(100.0)

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=12,
            mid=100.0,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=equity_usd,
            available_quote_usd=runtime.current_sizing.available_quote_to_trade_usd,
            buy_state_allowed=False,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=equity_usd,
            reentry_buy_inventory_cap=equity_usd,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.action, "SELL")
        self.assertEqual(candidate.reason, "force_trade_sell")
        self.assertTrue(candidate.filter_values["activity_floor_force"])

    def test_activity_floor_can_start_from_tighten_quotes_state(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=250.0, start_eth=2.5)
        runtime.current_activity_floor_state = "tighten_quotes"

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=2,
            mid=100.0,
            inventory_usd=runtime.portfolio.inventory_usd(100.0),
            effective_max_inventory_usd=equity_usd,
            available_quote_usd=runtime.current_sizing.available_quote_to_trade_usd,
            buy_state_allowed=False,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=equity_usd,
            reentry_buy_inventory_cap=equity_usd,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.action, "SELL")

    def test_activity_floor_builds_force_buy_when_inventory_is_below_target(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=450.0, start_eth=0.5)
        inventory_usd = runtime.portfolio.inventory_usd(100.0)

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=12,
            mid=100.0,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=equity_usd,
            available_quote_usd=runtime.current_sizing.available_quote_to_trade_usd,
            buy_state_allowed=False,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=equity_usd,
            reentry_buy_inventory_cap=equity_usd,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.action, "BUY")
        self.assertEqual(candidate.reason, "force_trade_buy")
        self.assertTrue(candidate.filter_values["force_trade_active"])

    def test_activity_floor_can_buy_back_toward_target_above_position_cap(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=390.0, start_eth=1.1)
        inventory_usd = runtime.portfolio.inventory_usd(100.0)

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=12,
            mid=100.0,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=100.0,
            available_quote_usd=runtime.current_sizing.available_quote_to_trade_usd,
            buy_state_allowed=False,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=100.0,
            reentry_buy_inventory_cap=100.0,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.action, "BUY")
        self.assertGreaterEqual(candidate.inventory_cap_usd, inventory_usd + candidate.size_usd)

    def test_activity_floor_does_not_sell_more_when_buy_is_needed_but_unfunded(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=250.0, start_eth=1.1)
        inventory_usd = 20.0

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=12,
            mid=100.0,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=100.0,
            available_quote_usd=0.0,
            buy_state_allowed=False,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=100.0,
            reentry_buy_inventory_cap=100.0,
        )

        self.assertIsNone(candidate)

    def test_activity_floor_bypasses_side_flip_only_in_paper(self) -> None:
        activity_decision = DecisionOutcome(
            action="BUY",
            source="activity_floor",
            filter_values={"activity_floor_force": True},
        )
        strategy_decision = DecisionOutcome(action="BUY", source="strategy")

        with patch("bot_runner.BOT_MODE", "paper"):
            self.assertTrue(_side_flip_exempt(activity_decision))
            self.assertFalse(_side_flip_exempt(strategy_decision))
        with patch("bot_runner.BOT_MODE", "live"):
            self.assertFalse(_side_flip_exempt(activity_decision))

    def test_forced_sell_bypasses_side_flip(self) -> None:
        decision = DecisionOutcome(action="SELL", reason="profit_exit_sell", source="strategy")

        with patch("bot_runner.BOT_MODE", "live"):
            self.assertTrue(_side_flip_exempt(decision))

    def test_paper_exit_sell_cap_preserves_target_inventory(self) -> None:
        runtime, _ = prepare_activity_runtime(start_usdc=250.0, start_eth=2.5)
        runtime.adaptive_config = replace(runtime.adaptive_config, enabled=True)

        with patch("bot_runner.BOT_MODE", "paper"):
            capped = _cap_paper_activity_exit_sell_size(
                runtime,
                reason="profit_exit_sell",
                size_usd=200.0,
                inventory_usd=runtime.portfolio.inventory_usd(100.0),
                min_notional_usd=runtime.current_sizing.min_notional_usd,
            )

        self.assertEqual(capped, 0.0)

    def test_paper_exit_sell_cap_uses_force_size_when_above_target(self) -> None:
        runtime, _ = prepare_activity_runtime(start_usdc=200.0, start_eth=3.5)
        runtime.adaptive_config = replace(runtime.adaptive_config, enabled=True)
        expected_cap = max(runtime.current_sizing.force_trade_size_usd, runtime.current_sizing.min_notional_usd)

        with patch("bot_runner.BOT_MODE", "paper"):
            capped = _cap_paper_activity_exit_sell_size(
                runtime,
                reason="profit_lock_level_1",
                size_usd=200.0,
                inventory_usd=runtime.portfolio.inventory_usd(100.0),
                min_notional_usd=runtime.current_sizing.min_notional_usd,
            )

        self.assertAlmostEqual(capped, expected_cap)

    def test_activity_floor_does_not_override_hard_pause(self) -> None:
        runtime, equity_usd = prepare_activity_runtime(start_usdc=250.0, start_eth=2.5)
        runtime.current_quote_enabled = False
        runtime.current_strategy_mode = "NO_TRADE"
        runtime.current_defensive_stage = 4

        candidate = _build_activity_floor_candidate(
            runtime,
            cycle_index=12,
            mid=100.0,
            inventory_usd=runtime.portfolio.inventory_usd(100.0),
            effective_max_inventory_usd=equity_usd,
            available_quote_usd=runtime.current_sizing.available_quote_to_trade_usd,
            buy_state_allowed=True,
            sell_state_allowed=True,
            state_requires_reentry_only=False,
            in_cooldown=False,
            min_notional_usd=runtime.current_sizing.min_notional_usd,
            default_buy_inventory_cap=equity_usd,
            reentry_buy_inventory_cap=equity_usd,
        )

        self.assertIsNone(candidate)


if __name__ == "__main__":
    unittest.main()
