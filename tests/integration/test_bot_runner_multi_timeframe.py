from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, process_price_tick
from types_bot import DecisionOutcome, EdgeAssessment, MarketRegimeAssessment


def build_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        regime="RANGE",
        volatility_state="NORMAL",
        feed_state="NORMAL",
        mode="RANGE_MAKER",
        short_ma=100.0,
        long_ma=100.0,
        volatility=0.0010,
        spread_multiplier=1.0,
        signal_score=0.20,
        feed_score=0.0,
        risk_score=0.05,
        news_score=0.0,
        macro_score=0.0,
        onchain_score=0.0,
        adaptive_score=0.0,
        confidence=0.8,
        buy_enabled=True,
        sell_enabled=True,
        max_inventory_multiplier=5.0,
        target_inventory_pct=0.5,
        trade_size_multiplier=1.0,
        market_score=0.15,
        trend_strength=0.20,
        inventory_skew_multiplier=1.0,
        directional_bias=0.0,
        max_chase_bps_multiplier=1.0,
    )


def build_regime() -> MarketRegimeAssessment:
    return MarketRegimeAssessment(
        market_regime="RANGE",
        regime_confidence=70.0,
        range_width_pct=1.0,
        net_move_pct=0.1,
        direction_consistency=0.65,
        volatility_score=18.0,
        bounce_count=3,
        range_touch_count=4,
        sign_flip_ratio=0.18,
        noise_ratio=1.1,
        body_to_wick_ratio=0.65,
        ema_deviation_pct=0.04,
        mean_reversion_distance_pct=-0.05,
        window_high=101.0,
        window_low=99.0,
        window_mean=100.0,
        price_position_pct=0.55,
    )


def build_edge() -> EdgeAssessment:
    return EdgeAssessment(
        expected_edge_usd=0.20,
        expected_edge_bps=75.0,
        cost_estimate_usd=0.02,
        edge_score=80.0,
        edge_pass=True,
        slippage_estimate_bps=4.0,
        mev_risk_score=15.0,
    )


class BotRunnerMultiTimeframeTests(unittest.TestCase):
    def _prepare_runtime(self, *, trend_filter: bool, confirmation_filter: bool):
        runtime = create_runtime(
            bootstrap_prices=[],
            reference_price=120.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            cycle_seconds=60.0,
            execution_timeframe_seconds=300.0,
            trend_timeframe_seconds=900.0,
            confirmation_timeframe_seconds=60.0,
            enable_trend_timeframe_filter=trend_filter,
            enable_confirmation_filter=confirmation_filter,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.regime_detector.assess = lambda prices: build_regime()
        runtime.edge_filter.assess = lambda **kwargs: build_edge()
        return runtime

    def test_trend_filter_blocks_buy_on_15m_downtrend(self) -> None:
        random.seed(0)
        runtime = self._prepare_runtime(trend_filter=True, confirmation_filter=False)
        runtime.decision_engine.decide = lambda cycle_index, **kwargs: (
            DecisionOutcome(
                action="BUY",
                size_usd=20.0,
                reason="quoted_buy",
                source="test",
                order_price=999.0,
                inventory_cap_usd=5_000.0,
                allow_trade=True,
                filter_values={},
            )
            if cycle_index >= 44
            else DecisionOutcome(action="NONE", block_reason="no_signal", filter_values={})
        )

        prices = [140.0 - float(index) for index in range(45)]
        for cycle_index, price in enumerate(prices):
            process_price_tick(
                runtime=runtime,
                cycle_index=cycle_index,
                mid=price,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )

        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "ema_downtrend_buy_blocked")
        self.assertEqual(runtime.current_trend_bias, "sell_only")

    def test_same_signal_can_trade_when_upper_filter_disabled(self) -> None:
        random.seed(0)
        runtime = self._prepare_runtime(trend_filter=False, confirmation_filter=False)
        runtime.decision_engine.decide = lambda cycle_index, **kwargs: (
            DecisionOutcome(
                action="BUY",
                size_usd=20.0,
                reason="quoted_buy",
                source="test",
                order_price=999.0,
                inventory_cap_usd=5_000.0,
                allow_trade=True,
                filter_values={},
            )
            if cycle_index >= 29
            else DecisionOutcome(action="NONE", block_reason="no_signal", filter_values={})
        )

        prices = [100.0] * 30
        for cycle_index, price in enumerate(prices):
            process_price_tick(
                runtime=runtime,
                cycle_index=cycle_index,
                mid=price,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )

        self.assertGreaterEqual(runtime.engine.trade_count, 1)
        self.assertTrue(runtime.last_allow_trade)

    def test_confirmation_filter_blocks_buy_until_1m_momentum_slows(self) -> None:
        random.seed(0)
        runtime = self._prepare_runtime(trend_filter=False, confirmation_filter=True)
        runtime.decision_engine.decide = lambda cycle_index, **kwargs: (
            DecisionOutcome(
                action="BUY",
                size_usd=20.0,
                reason="quoted_buy",
                source="test",
                order_price=999.0,
                inventory_cap_usd=5_000.0,
                allow_trade=True,
                filter_values={},
            )
            if cycle_index >= 29
            else DecisionOutcome(action="NONE", block_reason="no_signal", filter_values={})
        )

        prices = ([100.0] * 25) + [100.2, 100.1, 100.0, 99.9, 99.8]
        for cycle_index, price in enumerate(prices):
            process_price_tick(
                runtime=runtime,
                cycle_index=cycle_index,
                mid=price,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )

        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "confirmation_blocks_buy")


if __name__ == "__main__":
    unittest.main()
