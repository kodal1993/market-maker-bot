from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from multi_timeframe import (
    aggregate_close_prices,
    build_timeframe_snapshot,
    required_bootstrap_price_rows,
    steps_per_timeframe,
)


class MultiTimeframeTests(unittest.TestCase):
    def test_aggregate_close_prices_builds_5m_and_15m_series_from_1m_input(self) -> None:
        raw_prices = [100.0 + index for index in range(15)]

        execution_prices = aggregate_close_prices(
            raw_prices,
            cycle_seconds=60.0,
            timeframe_seconds=300.0,
        )
        trend_prices = aggregate_close_prices(
            raw_prices,
            cycle_seconds=60.0,
            timeframe_seconds=900.0,
        )

        self.assertEqual(execution_prices, [104.0, 109.0, 114.0])
        self.assertEqual(trend_prices, [114.0])

    def test_build_timeframe_snapshot_uses_requested_modes(self) -> None:
        raw_prices = [100.0 + index for index in range(30)]

        snapshot = build_timeframe_snapshot(
            raw_prices,
            cycle_seconds=60.0,
            execution_timeframe_seconds=300.0,
            trend_timeframe_seconds=900.0,
            confirmation_timeframe_seconds=60.0,
            enable_trend_filter=True,
            enable_confirmation=True,
        )

        self.assertEqual(snapshot.execution_bucket_count, 6)
        self.assertEqual(snapshot.trend_bucket_count, 2)
        self.assertEqual(snapshot.confirmation_bucket_count, 30)
        self.assertEqual(snapshot.execution_prices[-1], 129.0)
        self.assertEqual(snapshot.trend_prices[-1], 129.0)
        self.assertEqual(snapshot.confirmation_prices[-1], 129.0)

    def test_required_bootstrap_rows_scales_with_timeframes(self) -> None:
        rows = required_bootstrap_price_rows(
            cycle_seconds=60.0,
            configured_rows=21,
            execution_timeframe_seconds=300.0,
            trend_timeframe_seconds=900.0,
            confirmation_timeframe_seconds=60.0,
            enable_trend_filter=True,
            enable_confirmation=True,
        )

        self.assertGreaterEqual(rows, 360)

    def test_steps_per_timeframe_never_returns_less_than_one(self) -> None:
        self.assertEqual(steps_per_timeframe(60.0, 60.0), 1)
        self.assertEqual(steps_per_timeframe(30.0, 60.0), 1)
        self.assertEqual(steps_per_timeframe(300.0, 60.0), 5)


if __name__ == "__main__":
    unittest.main()
