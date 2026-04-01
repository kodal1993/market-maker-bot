from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from performance import PerformanceTracker, build_report, flatten_report


class PerformanceSummaryTests(unittest.TestCase):
    def test_build_summary_contains_realized_unrealized_and_daily_stats(self) -> None:
        tracker = PerformanceTracker(
            start_usdc=1_000.0,
            start_eth=0.0,
            start_price=100.0,
        )
        tracker.record_equity(
            cycle_index=0,
            mid_price=100.0,
            equity_usd=1_000.0,
            inventory_usd=0.0,
        )
        tracker.record_trade(
            cycle_index=1,
            side="sell",
            price=105.0,
            size_usd=50.0,
            fee_usd=0.5,
            realized_pnl=10.0,
            usdc_after=1_010.0,
            eth_after=0.0,
        )
        tracker.record_trade(
            cycle_index=2,
            side="sell",
            price=98.0,
            size_usd=50.0,
            fee_usd=0.5,
            realized_pnl=-4.0,
            usdc_after=1_006.0,
            eth_after=0.0,
        )

        summary = tracker.build_summary(
            final_mid=100.0,
            final_usdc=1_020.0,
            final_eth=0.0,
            realized_pnl=6.0,
        )

        self.assertEqual(summary["realized_pnl"], 6.0)
        self.assertEqual(summary["unrealized_pnl"], 14.0)
        self.assertEqual(summary["avg_profit"], 10.0)
        self.assertEqual(summary["avg_loss"], -4.0)
        self.assertEqual(summary["avg_loss_abs_usd"], 4.0)
        self.assertEqual(summary["daily_stats"]["trade_count"], 2)
        self.assertEqual(summary["daily_stats"]["avg_profit"], 10.0)
        self.assertEqual(summary["daily_stats"]["avg_loss_abs_usd"], 4.0)

    def test_build_report_includes_v4_metrics(self) -> None:
        summary = {
            "start_value": 1000.0,
            "end_value": 1015.0,
            "hodl_value": 1008.0,
            "alpha": 7.0,
            "alpha_vs_hodl": 7.0,
            "total_pnl": 15.0,
            "realized_pnl": 10.0,
            "unrealized_pnl": 5.0,
            "profit_factor": 1.5,
            "win_rate": 55.0,
            "avg_win": 5.0,
            "avg_loss": -3.0,
            "avg_profit": 5.0,
            "avg_loss_abs_usd": 3.0,
            "max_drawdown_pct": 2.0,
            "max_loss_streak": 2,
            "trade_count": 12,
            "closed_trade_count": 6,
            "pnl_per_trade": 1.25,
            "daily_trade_count": 12,
            "regime_trade_counts": {"TREND": 4, "RANGE": 8, "NO_TRADE": 0},
            "regime_realized_pnl_usd": {"TREND": 3.0, "RANGE": 7.0, "NO_TRADE": 0.0},
            "avg_hold_minutes": 42.5,
            "inventory_drift_avg_pct": 6.5,
            "inventory_drift_max_pct": 12.0,
            "rejection_reason_stats": {"edge_score_too_low": 5},
            "verdict": "REVIEW",
            "verdict_reasons": ["mixed_signals"],
            "minimum_trade_count_met": False,
            "pass_condition_profit_factor": True,
            "pass_condition_drawdown": True,
            "pass_condition_alpha": True,
            "fail_condition_profit_factor": False,
            "fail_condition_alpha": False,
            "verdict_profit_factor_display": "1.5000",
        }

        report = build_report(summary, run_label="demo")
        flat = flatten_report(report)

        self.assertEqual(report["metrics"]["daily_trade_count"], 12)
        self.assertEqual(report["metrics"]["avg_hold_minutes"], 42.5)
        self.assertEqual(report["metrics"]["rejection_reason_stats"]["edge_score_too_low"], 5)
        self.assertIn("\"RANGE\":8", flat["regime_trade_counts"])
        self.assertIn("\"edge_score_too_low\":5", flat["rejection_reason_stats"])


if __name__ == "__main__":
    unittest.main()
