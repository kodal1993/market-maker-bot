from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, process_price_tick
from strategy_profile import resolve_entry_threshold_bps, resolve_min_edge_bps
from types_bot import DecisionOutcome, EdgeAssessment, InventoryProfile, MarketRegimeAssessment


def build_snapshot(*, mode: str = "RANGE_MAKER", short_ma: float = 100.0, long_ma: float = 99.9) -> SimpleNamespace:
    return SimpleNamespace(
        regime="RANGE",
        volatility_state="NORMAL",
        feed_state="NORMAL",
        mode=mode,
        short_ma=short_ma,
        long_ma=long_ma,
        volatility=0.0010,
        spread_multiplier=1.0,
        signal_score=0.28,
        feed_score=0.1,
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
        market_score=0.30,
        trend_strength=1.0,
        inventory_skew_multiplier=1.0,
        directional_bias=0.0,
        max_chase_bps_multiplier=1.0,
    )


def build_regime(
    regime: str,
    *,
    range_location: str = "middle",
    price_position_pct: float | None = None,
) -> MarketRegimeAssessment:
    return MarketRegimeAssessment(
        market_regime=regime,
        regime_confidence=84.0,
        range_width_pct=1.0,
        net_move_pct=-1.4 if regime == "TREND_DOWN" else 0.12,
        direction_consistency=0.88 if regime == "TREND_DOWN" else 0.74,
        volatility_score=22.0,
        bounce_count=3,
        range_touch_count=4,
        sign_flip_ratio=0.18,
        noise_ratio=1.1,
        body_to_wick_ratio=0.62,
        ema_deviation_pct=-0.25 if regime == "TREND_DOWN" else 0.08,
        mean_reversion_distance_pct=-0.12,
        window_high=100.8,
        window_low=99.8,
        window_mean=100.2,
        range_location=range_location,
        price_position_pct=0.25 if price_position_pct is None and regime == "TREND_DOWN" else (0.35 if price_position_pct is None else price_position_pct),
    )


def build_edge(*, edge_pass: bool = True, reject_reason: str = "") -> EdgeAssessment:
    return EdgeAssessment(
        expected_edge_usd=0.15 if edge_pass else -0.05,
        expected_edge_bps=65.0 if edge_pass else -20.0,
        cost_estimate_usd=0.02,
        edge_score=76.0 if edge_pass else 28.0,
        edge_pass=edge_pass,
        edge_reject_reason=reject_reason,
        slippage_estimate_bps=4.0,
        mev_risk_score=18.0,
    )


class BotRunnerSignalGateIntegrationTests(unittest.TestCase):
    def test_process_price_tick_blocks_chop_market_signal(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="quoted_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: build_regime("CHOP")
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "chop_market")
        self.assertEqual(filter_values["market_regime"], "CHOP")
        self.assertEqual(filter_values["gate_decision"], "reject")

    def test_process_price_tick_blocks_trend_down_reentry_buy(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[110.0 - (index * 0.3) for index in range(30)],
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot(short_ma=99.2, long_ma=100.0)
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="reentry_zone_1",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: build_regime("TREND_DOWN")
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "ema_downtrend_buy_blocked")

    def test_process_price_tick_allows_range_signal_with_positive_edge(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="quoted_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: build_regime("RANGE")
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(runtime.engine.trade_count, 1)
        self.assertTrue(runtime.last_allow_trade)
        self.assertEqual(filter_values["gate_decision"], "allow")
        self.assertEqual(filter_values["approved_mode"], "range_entry")

    def test_process_price_tick_logs_v5_regime_fields(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=50.0,
            start_eth=0.5,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="quoted_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: build_regime("RANGE")
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(filter_values["detected_regime"], "RANGE")
        self.assertEqual(filter_values["zone"], "mid")
        self.assertAlmostEqual(
            filter_values["entry_threshold_bps"],
            resolve_entry_threshold_bps("RANGE", "NORMAL"),
        )
        self.assertAlmostEqual(filter_values["min_edge_bps"], resolve_min_edge_bps("RANGE"))
        self.assertAlmostEqual(filter_values["inventory_drift_pct"], 0.0, delta=1e-6)
        self.assertEqual(filter_values["trade_blocked_reason"], "")

    def test_process_price_tick_blocks_same_side_buy_when_inventory_is_base_heavy(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=175.0,
            start_eth=2.9,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            enable_state_machine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.regime_detector.assess = lambda prices: build_regime(
            "RANGE",
            range_location="bottom",
            price_position_pct=0.10,
        )
        runtime.inventory_manager.build_profile = lambda regime, inventory_usd, equity_usd: InventoryProfile(
            regime_label="normal",
            lower_bound=0.30,
            upper_bound=0.70,
            inventory_ratio=inventory_usd / equity_usd,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            allow_buy=True,
            allow_sell=True,
            max_buy_usd=200.0,
            max_sell_usd=200.0,
        )

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "inventory_drift_same_side_buy_blocked")
        self.assertEqual(runtime.last_buy_debug_reason, "inventory_drift_same_side_buy_blocked")
        self.assertEqual(filter_values["trade_blocked_reason"], "inventory_drift_same_side_buy_blocked")
        self.assertEqual(filter_values["inventory_state"], "base_heavy")
        self.assertGreater(filter_values["inventory_drift_pct"], 0.0)

    def test_process_price_tick_prioritizes_inventory_force_reduce(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=175.0,
            start_eth=2.9,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            enable_state_machine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.regime_detector.assess = lambda prices: build_regime("RANGE")
        runtime.inventory_manager.build_profile = lambda regime, inventory_usd, equity_usd: InventoryProfile(
            regime_label="normal",
            lower_bound=0.30,
            upper_bound=0.70,
            inventory_ratio=inventory_usd / equity_usd,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            allow_buy=False,
            allow_sell=True,
            max_buy_usd=0.0,
            max_sell_usd=120.0,
            soft_limit_usd=250.0,
            hard_limit_usd=260.0,
            force_limit_usd=270.0,
            soft_limit_hit=True,
            hard_limit_hit=True,
            force_limit_hit=True,
            reduction_only=True,
        )
        captured: dict[str, DecisionOutcome | None] = {}

        def _decide(**kwargs):
            captured["strategy_sell_candidate"] = kwargs.get("strategy_sell_candidate")
            return kwargs.get("strategy_sell_candidate") or DecisionOutcome(
                action="NONE",
                reason="no_signal",
                source="test",
                block_reason="no_signal",
            )

        runtime.decision_engine.decide = _decide
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        self.assertIsNotNone(captured["strategy_sell_candidate"])
        self.assertEqual(captured["strategy_sell_candidate"].reason, "inventory_force_reduce")
        self.assertEqual(runtime.last_decision_reason, "inventory_force_reduce")
        self.assertEqual(runtime.current_inventory_limit_state, "force_limit")

    def test_process_price_tick_blocks_sell_when_ema_uptrend_active(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0 + (index * 0.3) for index in range(30)],
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            trend_timeframe_seconds=60.0,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot(
            mode="TREND_UP",
            short_ma=100.8,
            long_ma=100.0,
        )
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="SELL",
            size_usd=30.0,
            reason="quoted_sell",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: build_regime("TREND_UP")
        runtime.edge_filter.assess = lambda **kwargs: build_edge(edge_pass=True)

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        self.assertEqual(runtime.engine.trade_count, 0)
        self.assertEqual(runtime.last_decision_block_reason, "ema_uptrend_sell_blocked")


if __name__ == "__main__":
    unittest.main()
