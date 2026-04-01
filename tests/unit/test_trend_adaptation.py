from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from intelligence import IntelligenceEngine
from intelligence_models import MarketState


def neutral_signal() -> SimpleNamespace:
    return SimpleNamespace(score=0.0, confidence=0.0, blocked=False)


def neutral_adaptive() -> SimpleNamespace:
    return SimpleNamespace(
        performance_score=0.0,
        inventory_multiplier=1.0,
        trade_size_multiplier=1.0,
        spread_multiplier=1.0,
        threshold_multiplier=1.0,
    )


class TrendAdaptationTests(unittest.TestCase):
    def _build_snapshot(self, prices: list[float], *, market_state: MarketState | None = None):
        engine = IntelligenceEngine()
        patches = [
            patch("intelligence.build_news_signal", return_value=neutral_signal()),
            patch("intelligence.build_macro_signal", return_value=neutral_signal()),
            patch("intelligence.build_onchain_signal", return_value=neutral_signal()),
            patch("intelligence.build_adaptive_state", return_value=neutral_adaptive()),
        ]
        if market_state is not None:
            patches.append(patch("intelligence.build_market_state", return_value=market_state))

        with patches[0], patches[1], patches[2], patches[3]:
            if market_state is None:
                return engine.build_snapshot(
                    prices=prices,
                    current_equity=500.0,
                    equity_peak=500.0,
                    recent_equities=[500.0] * 12,
                    inventory_usd=40.0,
                )
            with patches[4]:
                return engine.build_snapshot(
                    prices=prices,
                    current_equity=500.0,
                    equity_peak=500.0,
                    recent_equities=[500.0] * 12,
                    inventory_usd=40.0,
                )

    def test_uptrend_allows_only_buy_side(self) -> None:
        prices = [100.0 + (index * 0.35) for index in range(30)]

        snapshot = self._build_snapshot(prices)

        self.assertGreater(snapshot.short_ma, snapshot.long_ma)
        self.assertTrue(snapshot.buy_enabled)
        self.assertFalse(snapshot.sell_enabled)
        self.assertEqual(snapshot.mode, "TREND_UP")
        self.assertEqual(snapshot.current_mode, "TREND_UP")
        self.assertIn("ema_uptrend_buy_only", snapshot.reason)

    def test_downtrend_allows_only_sell_side(self) -> None:
        prices = [110.0 - (index * 0.35) for index in range(30)]

        snapshot = self._build_snapshot(prices)

        self.assertLess(snapshot.short_ma, snapshot.long_ma)
        self.assertFalse(snapshot.buy_enabled)
        self.assertTrue(snapshot.sell_enabled)
        self.assertEqual(snapshot.current_mode, "TREND_DOWN")
        self.assertIn("trend_down_sell_priority", snapshot.reason)
        self.assertIn("ema_downtrend_sell_only", snapshot.reason)

    def test_range_keeps_both_sides_enabled(self) -> None:
        prices = [
            100.00,
            100.05,
            99.98,
            100.04,
            99.99,
            100.03,
            100.01,
            99.97,
            100.02,
            100.00,
            100.01,
            99.99,
            100.02,
            100.00,
            100.01,
            99.98,
            100.02,
            100.00,
            100.01,
            100.00,
            100.02,
            99.99,
            100.01,
            100.00,
            100.02,
            100.01,
            100.00,
            99.99,
            100.01,
            100.00,
        ]

        snapshot = self._build_snapshot(prices)

        self.assertEqual(snapshot.current_mode, "RANGE")
        self.assertTrue(snapshot.buy_enabled)
        self.assertTrue(snapshot.sell_enabled)
        self.assertIn("ema_range_dual_side", snapshot.reason)

    def test_strong_drop_blocks_buy_even_in_range_snapshot(self) -> None:
        prices = [100.0] * 26 + [99.5, 99.2, 98.9, 98.6]
        snapshot = self._build_snapshot(
            prices,
            market_state=MarketState(
                regime="RANGE",
                volatility_state="NORMAL",
                short_ma=100.0,
                long_ma=100.0,
                volatility=0.001,
                trend_strength=0.0,
                market_score=0.0,
            ),
        )

        self.assertFalse(snapshot.buy_enabled)
        self.assertIn("strong_drop_buy_block", snapshot.reason)

    def test_strong_rally_blocks_sell_even_in_range_snapshot(self) -> None:
        prices = [100.0] * 26 + [100.5, 100.8, 101.1, 101.4]
        snapshot = self._build_snapshot(
            prices,
            market_state=MarketState(
                regime="RANGE",
                volatility_state="NORMAL",
                short_ma=100.0,
                long_ma=100.0,
                volatility=0.001,
                trend_strength=0.0,
                market_score=0.0,
            ),
        )

        self.assertFalse(snapshot.sell_enabled)
        self.assertIn("strong_rally_sell_block", snapshot.reason)


if __name__ == "__main__":
    unittest.main()
