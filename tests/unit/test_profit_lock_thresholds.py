from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime
from runtime_risk import build_profit_lock_sell_plan, reset_profit_lock_state


class ProfitLockThresholdTests(unittest.TestCase):
    def _build_runtime(self):
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=0.50,
            start_eth_usd=0.0,
            enable_state_machine=False,
        )
        runtime.portfolio.eth_cost_basis = 100.0
        runtime.current_market_mode = "TREND"
        runtime.current_volatility_bucket = "NORMAL"
        reset_profit_lock_state(runtime, 100.0)
        return runtime

    def test_profit_lock_level_one_triggers_at_zero_point_zero_nine_percent(self) -> None:
        runtime = self._build_runtime()

        reason_before, _ = build_profit_lock_sell_plan(runtime, cycle_index=30, mid=100.08)
        reason_at, size_usd = build_profit_lock_sell_plan(runtime, cycle_index=31, mid=100.09)

        self.assertIsNone(reason_before)
        self.assertEqual(reason_at, "profit_lock_level_1")
        self.assertGreater(size_usd, 0.0)

    def test_profit_lock_level_two_triggers_at_zero_point_one_four_percent(self) -> None:
        runtime = self._build_runtime()
        runtime.profit_lock_state.level_one_executed = True

        reason_before, _ = build_profit_lock_sell_plan(runtime, cycle_index=30, mid=100.13)
        reason_at, size_usd = build_profit_lock_sell_plan(runtime, cycle_index=31, mid=100.14)

        self.assertIsNone(reason_before)
        self.assertEqual(reason_at, "profit_lock_level_2")
        self.assertGreater(size_usd, 0.0)

    def test_stop_loss_waits_for_looser_threshold(self) -> None:
        runtime = self._build_runtime()

        reason_before, _ = build_profit_lock_sell_plan(runtime, cycle_index=30, mid=98.81)
        reason_at, size_usd = build_profit_lock_sell_plan(runtime, cycle_index=31, mid=98.79)

        self.assertIsNone(reason_before)
        self.assertEqual(reason_at, "stop_loss_sell")
        self.assertGreater(size_usd, 0.0)

    def test_range_mode_uses_smaller_profit_targets(self) -> None:
        runtime = self._build_runtime()
        runtime.current_market_mode = "RANGE"

        reason_before_level_one, _ = build_profit_lock_sell_plan(runtime, cycle_index=30, mid=100.07)
        reason_at_level_one, size_level_one = build_profit_lock_sell_plan(runtime, cycle_index=31, mid=100.08)

        runtime.profit_lock_state.level_one_executed = True
        reason_before_level_two, _ = build_profit_lock_sell_plan(runtime, cycle_index=32, mid=100.12)
        reason_at_level_two, size_level_two = build_profit_lock_sell_plan(runtime, cycle_index=33, mid=100.13)

        self.assertIsNone(reason_before_level_one)
        self.assertEqual(reason_at_level_one, "profit_lock_level_1")
        self.assertGreater(size_level_one, 0.0)
        self.assertIsNone(reason_before_level_two)
        self.assertEqual(reason_at_level_two, "profit_lock_level_2")
        self.assertGreater(size_level_two, 0.0)


if __name__ == "__main__":
    unittest.main()
