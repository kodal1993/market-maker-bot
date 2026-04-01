from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import EXECUTION_MIN_EXPECTED_PROFIT_PCT
from execution_engine import ExecutionEngine
from trade_filter import TradeFilter
from types_bot import Quote, ReentryState


class TradePermissionTuningTests(unittest.TestCase):
    def test_trade_filter_prefers_size_reduction_over_block_for_borderline_trade(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 100),
        ):
            result = trade_filter.evaluate(
                side="buy",
                trade_reason="trend_buy",
                cycle_index=40,
                order_price=100.00,
                last_trade_cycle=39,
                last_trade_price=99.94,
                loss_streak=0,
                rsi_value=62.0,
                momentum_bps=90.0,
                regime="TREND",
                market_score=0.35,
                volatility_state="NORMAL",
                trade_count=10,
            )

        self.assertTrue(result.allow_trade)
        self.assertLess(result.size_multiplier, 1.0)
        self.assertIn("cooldown_soft_limit", result.filter_values["adjustment_reasons"])

    def test_execution_engine_allows_small_positive_edge_trade(self) -> None:
        engine = ExecutionEngine(maker_fee_bps=2.0, taker_fee_bps=5.0, base_trade_size_usd=30.0)
        quote = Quote(bid=100.0, ask=100.05, mid=100.025, spread_bps=5.0, mode="TREND_UP")
        portfolio = SimpleNamespace(eth_cost_basis=100.0)
        reentry_state = ReentryState(last_sell_price=100.20)

        decision = engine.build_decision(
            side="buy",
            quote=quote,
            size_usd=15.0,
            mode="TREND_UP",
            trade_reason="trend_buy",
            portfolio=portfolio,
            reentry_state=reentry_state,
        )

        self.assertTrue(decision.allow_trade)
        self.assertGreater(decision.expected_profit_pct, 0.0)
        self.assertLess(decision.expected_profit_pct, EXECUTION_MIN_EXPECTED_PROFIT_PCT)

    def test_trade_filter_blocks_when_min_time_between_trades_not_met(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 5.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 50),
        ):
            result = trade_filter.evaluate(
                side="buy",
                trade_reason="trend_buy",
                cycle_index=40,
                order_price=100.00,
                last_trade_cycle=39,
                last_trade_price=99.90,
                loss_streak=0,
                rsi_value=55.0,
                momentum_bps=20.0,
                regime="TREND",
                market_score=0.35,
                volatility_state="NORMAL",
                trade_count=10,
                daily_trade_count=1,
            )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.block_reason, "min_time_between_trades")
        self.assertIn("remaining_trade_gap_minutes", result.filter_values)

    def test_trade_filter_blocks_when_daily_trade_cap_hit(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 3),
        ):
            result = trade_filter.evaluate(
                side="sell",
                trade_reason="quoted_sell",
                cycle_index=40,
                order_price=101.00,
                last_trade_cycle=10,
                last_trade_price=100.00,
                loss_streak=0,
                rsi_value=45.0,
                momentum_bps=-20.0,
                regime="RANGE",
                market_score=0.10,
                volatility_state="NORMAL",
                trade_count=3,
                daily_trade_count=3,
            )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.block_reason, "max_trades_per_day")
        self.assertTrue(result.filter_values["daily_trade_limit_hit"])

    def test_trade_filter_reduces_size_after_two_losses(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 100),
            patch("trade_filter.TRADE_FILTER_LOSS_STREAK_LIMIT", 2),
            patch("trade_filter.STATE_MACHINE_LOSS_STREAK_LIMIT", 3),
        ):
            result = trade_filter.evaluate(
                side="sell",
                trade_reason="quoted_sell",
                cycle_index=40,
                order_price=101.00,
                last_trade_cycle=35,
                last_trade_price=100.00,
                loss_streak=2,
                rsi_value=45.0,
                momentum_bps=-20.0,
                regime="RANGE",
                market_score=0.10,
                volatility_state="NORMAL",
                trade_count=10,
                daily_trade_count=2,
            )

        self.assertTrue(result.allow_trade)
        self.assertLess(result.size_multiplier, 1.0)
        self.assertIn("loss_streak_size_reduction", result.filter_values["adjustment_reasons"])

    def test_trade_filter_pauses_after_three_losses(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 100),
            patch("trade_filter.TRADE_FILTER_LOSS_STREAK_LIMIT", 2),
            patch("trade_filter.STATE_MACHINE_LOSS_STREAK_LIMIT", 3),
        ):
            result = trade_filter.evaluate(
                side="buy",
                trade_reason="trend_buy",
                cycle_index=40,
                order_price=100.00,
                last_trade_cycle=30,
                last_trade_price=99.60,
                loss_streak=3,
                rsi_value=55.0,
                momentum_bps=20.0,
                regime="TREND",
                market_score=0.25,
                volatility_state="NORMAL",
                trade_count=10,
                daily_trade_count=2,
            )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.block_reason, "loss_streak_pause")
        self.assertTrue(result.filter_values["loss_streak_pause_active"])

    def test_trade_filter_reduces_buy_size_in_strong_trend(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 100),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SCORE", 0.42),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SKIP_SCORE", 0.68),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER", 0.55),
        ):
            result = trade_filter.evaluate(
                side="buy",
                trade_reason="trend_buy",
                cycle_index=40,
                order_price=100.00,
                last_trade_cycle=10,
                last_trade_price=99.20,
                loss_streak=0,
                rsi_value=55.0,
                momentum_bps=35.0,
                regime="TREND",
                market_score=0.50,
                volatility_state="NORMAL",
                trade_count=10,
                daily_trade_count=1,
            )

        self.assertTrue(result.allow_trade)
        self.assertLess(result.size_multiplier, 1.0)
        self.assertIn("strong_trend_size_reduction", result.filter_values["adjustment_reasons"])

    def test_trade_filter_skips_buy_in_extreme_trend_chase(self) -> None:
        trade_filter = TradeFilter(cycle_seconds=60.0)

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 100),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SCORE", 0.42),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SKIP_SCORE", 0.68),
            patch("trade_filter.TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER", 0.55),
        ):
            result = trade_filter.evaluate(
                side="buy",
                trade_reason="trend_buy",
                cycle_index=40,
                order_price=100.00,
                last_trade_cycle=10,
                last_trade_price=99.10,
                loss_streak=0,
                rsi_value=58.0,
                momentum_bps=110.0,
                regime="TREND",
                market_score=0.80,
                volatility_state="NORMAL",
                trade_count=10,
                daily_trade_count=1,
            )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.block_reason, "strong_trend_skip")
        self.assertTrue(result.filter_values["strong_trend_skip_active"])


if __name__ == "__main__":
    unittest.main()
