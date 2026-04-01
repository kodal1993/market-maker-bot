from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from intelligence import IntelligenceEngine, resolve_drawdown_stage


def build_market() -> SimpleNamespace:
    return SimpleNamespace(
        regime="TREND",
        volatility_state="NORMAL",
        short_ma=101.0,
        long_ma=100.0,
        volatility=0.0012,
        trend_strength=1.12,
        market_score=0.34,
    )


def build_signal() -> SimpleNamespace:
    return SimpleNamespace(
        score=0.0,
        confidence=0.0,
        blocked=False,
    )


def build_adaptive() -> SimpleNamespace:
    return SimpleNamespace(
        performance_score=0.0,
        inventory_multiplier=1.0,
        trade_size_multiplier=1.0,
        spread_multiplier=1.0,
        threshold_multiplier=1.0,
    )


class DrawdownControlTests(unittest.TestCase):
    def _build_snapshot(self, current_equity: float, equity_peak: float = 100.0):
        engine = IntelligenceEngine()
        with (
            patch("intelligence.build_market_state", return_value=build_market()),
            patch("intelligence.build_news_signal", return_value=build_signal()),
            patch("intelligence.build_macro_signal", return_value=build_signal()),
            patch("intelligence.build_onchain_signal", return_value=build_signal()),
            patch("intelligence.build_adaptive_state", return_value=build_adaptive()),
        ):
            return engine.build_snapshot(
                prices=[100.0] * 40,
                current_equity=current_equity,
                equity_peak=equity_peak,
                recent_equities=[equity_peak, current_equity],
                inventory_usd=20.0,
            )

    def test_drawdown_stage_thresholds_are_exact(self) -> None:
        self.assertEqual(resolve_drawdown_stage(0.00), "normal")
        self.assertEqual(resolve_drawdown_stage(0.03), "size_reduce")
        self.assertEqual(resolve_drawdown_stage(0.05), "aggression_reduce")
        self.assertEqual(resolve_drawdown_stage(0.08), "pause")

    def test_three_percent_drawdown_reduces_trade_size(self) -> None:
        baseline = self._build_snapshot(100.0)
        reduced = self._build_snapshot(97.0)

        self.assertIn("drawdown_size_reduce", reduced.blockers)
        self.assertLess(reduced.trade_size_multiplier, baseline.trade_size_multiplier)
        self.assertNotEqual(reduced.mode, "NO_TRADE")

    def test_five_percent_drawdown_reduces_aggression(self) -> None:
        baseline = self._build_snapshot(100.0)
        reduced = self._build_snapshot(95.0)

        self.assertIn("drawdown_aggression_reduce", reduced.blockers)
        self.assertLess(reduced.confidence, baseline.confidence)
        self.assertLess(abs(reduced.directional_bias), abs(baseline.directional_bias))
        self.assertGreaterEqual(reduced.spread_multiplier, baseline.spread_multiplier)

    def test_eight_percent_drawdown_pauses_trading(self) -> None:
        paused = self._build_snapshot(92.0)

        self.assertEqual(paused.mode, "NO_TRADE")
        self.assertFalse(paused.buy_enabled)
        self.assertFalse(paused.sell_enabled)
        self.assertIn("drawdown_pause", paused.blockers)


if __name__ == "__main__":
    unittest.main()
