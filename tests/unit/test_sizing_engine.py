from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sizing_engine import build_sizing_snapshot


class SizingEngineTests(unittest.TestCase):
    def _config_patch(self, **overrides):
        defaults = {
            "ACCOUNT_REFERENCE_MODE": "dynamic",
            "ACCOUNT_SIZE_OVERRIDE": 0.0,
            "TRADE_SIZE_PCT": 0.10,
            "MAX_POSITION_PCT": 0.25,
            "MAX_TRADE_SIZE_PCT": 0.15,
            "FORCE_TRADE_SIZE_PCT": 0.03,
            "TARGET_BASE_PCT": 0.50,
            "TARGET_QUOTE_PCT": 0.50,
            "MIN_NOTIONAL_USD": 10.0,
            "MIN_BASE_RESERVE_PCT": 0.05,
            "MIN_QUOTE_RESERVE_PCT": 0.05,
            "TRADE_SIZE_USD": 30.0,
            "MAX_TRADE_SIZE_USD": 75.0,
            "MAX_INVENTORY_USD": 140.0,
        }
        defaults.update(overrides)
        return patch.multiple("sizing_engine", **defaults)

    def test_equity_100_scales_down_sizes_and_targets(self) -> None:
        with self._config_patch():
            snapshot = build_sizing_snapshot(
                current_equity_usd=100.0,
                mid_price=100.0,
                portfolio_usdc=100.0,
                portfolio_eth=0.0,
            )

        self.assertAlmostEqual(snapshot.trade_size_usd, 10.0)
        self.assertAlmostEqual(snapshot.max_trade_size_usd, 15.0)
        self.assertAlmostEqual(snapshot.max_position_usd, 25.0)
        self.assertAlmostEqual(snapshot.target_base_usd, 50.0)
        self.assertAlmostEqual(snapshot.target_quote_usd, 50.0)
        self.assertAlmostEqual(snapshot.available_quote_to_trade_usd, 95.0)

    def test_equity_500_matches_expected_paper_scale(self) -> None:
        with self._config_patch():
            snapshot = build_sizing_snapshot(
                current_equity_usd=500.0,
                mid_price=100.0,
                portfolio_usdc=500.0,
                portfolio_eth=0.0,
            )

        self.assertAlmostEqual(snapshot.trade_size_usd, 50.0)
        self.assertAlmostEqual(snapshot.max_trade_size_usd, 75.0)
        self.assertAlmostEqual(snapshot.max_position_usd, 125.0)
        self.assertAlmostEqual(snapshot.force_trade_size_usd, 15.0)
        self.assertAlmostEqual(snapshot.target_base_usd, 250.0)
        self.assertAlmostEqual(snapshot.target_quote_usd, 250.0)

    def test_equity_1000_scales_up_proportionally(self) -> None:
        with self._config_patch():
            snapshot = build_sizing_snapshot(
                current_equity_usd=1000.0,
                mid_price=100.0,
                portfolio_usdc=1000.0,
                portfolio_eth=0.0,
            )

        self.assertAlmostEqual(snapshot.trade_size_usd, 100.0)
        self.assertAlmostEqual(snapshot.max_trade_size_usd, 150.0)
        self.assertAlmostEqual(snapshot.max_position_usd, 250.0)
        self.assertAlmostEqual(snapshot.force_trade_size_usd, 30.0)

    def test_low_equity_skips_trade_without_crash(self) -> None:
        with self._config_patch():
            snapshot = build_sizing_snapshot(
                current_equity_usd=80.0,
                mid_price=100.0,
                portfolio_usdc=80.0,
                portfolio_eth=0.0,
            )

        self.assertEqual(snapshot.trade_size_usd, 0.0)
        self.assertEqual(snapshot.force_trade_size_usd, 0.0)
        self.assertTrue(snapshot.insufficient_equity_for_min_trade)
        self.assertEqual(snapshot.clamp_reason, "below_min_notional")

    def test_force_trade_is_clamped_to_max_trade_limit(self) -> None:
        with self._config_patch(FORCE_TRADE_SIZE_PCT=0.20):
            snapshot = build_sizing_snapshot(
                current_equity_usd=200.0,
                mid_price=100.0,
                portfolio_usdc=200.0,
                portfolio_eth=0.0,
            )

        self.assertAlmostEqual(snapshot.computed_force_trade_size_usd, 40.0)
        self.assertAlmostEqual(snapshot.max_trade_size_usd, 30.0)
        self.assertAlmostEqual(snapshot.force_trade_size_usd, 30.0)
        self.assertTrue(snapshot.force_size_clamped)
        self.assertEqual(snapshot.force_clamp_reason, "max_trade_size_pct")


if __name__ == "__main__":
    unittest.main()
