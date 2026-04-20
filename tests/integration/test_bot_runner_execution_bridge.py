from __future__ import annotations

import csv
import json
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import shutil
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, process_price_tick, trade_log_headers, equity_log_headers
from csv_logger import CsvLogger
from types_bot import DecisionOutcome, EdgeAssessment, FillResult, MarketRegimeAssessment

TEST_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = TEST_ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


def build_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        regime="TREND",
        volatility_state="NORMAL",
        feed_state="NORMAL",
        mode="TREND_UP",
        short_ma=100.0,
        long_ma=99.8,
        volatility=0.0012,
        spread_multiplier=1.0,
        signal_score=0.25,
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
        target_inventory_pct=0.02,
        trade_size_multiplier=1.0,
        market_score=0.35,
        trend_strength=1.15,
        inventory_skew_multiplier=1.0,
        directional_bias=0.0,
        max_chase_bps_multiplier=1.0,
        cooldown_multiplier=1.0,
        min_edge_multiplier=1.0,
    )


def build_no_trade_snapshot() -> SimpleNamespace:
    snapshot = build_snapshot()
    snapshot.regime = "WARMUP"
    snapshot.feed_state = "BLOCK"
    snapshot.mode = "NO_TRADE"
    snapshot.strategy_mode = "NO_TRADE"
    snapshot.buy_enabled = False
    snapshot.sell_enabled = False
    snapshot.feed_score = -0.4152
    snapshot.news_score = -0.2881
    snapshot.macro_score = -0.8220
    snapshot.risk_score = 0.16
    snapshot.target_inventory_pct = 0.80
    snapshot.mm_mode = "base_mm"
    snapshot.activity_boost = 1.0
    snapshot.freeze_recovery_mode = False
    snapshot.fill_quality_score = 1.0
    snapshot.fill_quality_tier = "normal"
    snapshot.cooldown_multiplier = 1.0
    snapshot.blockers = ["warmup"]
    return snapshot


class BotRunnerExecutionBridgeTests(unittest.TestCase):
    def test_process_price_tick_logs_execution_analytics(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=True,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="trend_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )

        temp_dir = TMP_ROOT / "bridge_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        trade_path = temp_dir / "trades.csv"
        equity_path = temp_dir / "equity.csv"
        trade_logger = CsvLogger(str(trade_path), trade_log_headers())
        equity_logger = CsvLogger(str(equity_path), equity_log_headers())

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=trade_logger,
            equity_logger=equity_logger,
            log_progress=False,
        )

        with trade_path.open("r", encoding="utf-8", newline="") as trade_file:
            trade_rows = list(csv.reader(trade_file))
        with equity_path.open("r", encoding="utf-8", newline="") as equity_file:
            equity_rows = list(csv.reader(equity_file))
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertGreaterEqual(len(trade_rows), 2)
        self.assertGreaterEqual(len(equity_rows), 2)
        self.assertIn("execution_mode", trade_rows[0])
        self.assertIn("entry_price", trade_rows[0])
        self.assertIn("exit_price", trade_rows[0])
        self.assertIn("max_profit_during_trade", trade_rows[0])
        self.assertIn("mev_risk_score", equity_rows[0])
        filter_values_index = equity_rows[0].index("filter_values")
        entry_price_index = trade_rows[0].index("entry_price")
        exit_price_index = trade_rows[0].index("exit_price")
        max_profit_index = trade_rows[0].index("max_profit_during_trade")
        self.assertIn("decision_reason", equity_rows[1][filter_values_index])
        self.assertGreater(float(trade_rows[1][entry_price_index]), 0.0)
        self.assertEqual(trade_rows[1][exit_price_index], "")
        self.assertEqual(float(trade_rows[1][max_profit_index]), 0.0)
        self.assertTrue(runtime.last_execution_analytics.execution_mode)
        self.assertGreaterEqual(runtime.engine.trade_count, 1)

    def test_process_price_tick_logs_skipped_trade_reason_for_gas_spike(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=True,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="trend_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )

        with patch("runtime_execution.estimate_execution_gas_gwei", return_value=50.0):
            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )

        self.assertEqual(runtime.last_execution_analytics.execution_mode, "skip")
        self.assertEqual(runtime.last_decision_block_reason, "gas_spike_skip")
        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(filter_values["trade_blocked_reason"], "gas_spike_skip")
        self.assertEqual(filter_values["gas_price_gwei"], 50.0)
        self.assertEqual(runtime.engine.trade_count, 0)

    def test_process_price_tick_emits_entry_pipeline_logs(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=True,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="trend_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )

        with patch("bot_runner.log") as mocked_log:
            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=True,
            )

        messages = "\n".join(str(call.args[0]) for call in mocked_log.call_args_list if call.args)
        self.assertIn("ENTRY_SIGNAL", messages)
        self.assertIn("SHOULD_ENTER", messages)
        self.assertIn("ENTRY_ALLOWED", messages)
        self.assertIn("ENTRY_TRIGGERED", messages)
        self.assertIn("ORDER_RESPONSE", messages)
        self.assertIn("EXECUTION_ATTEMPT", messages)
        self.assertIn("ORDER_SENT", messages)
        self.assertIn("ORDER_FILLED", messages)
        self.assertIn("ORDER_EXECUTED", messages)
        self.assertIn("execution_attempted true", messages)
        self.assertIn("execution_success true", messages)

    def test_process_price_tick_emits_execution_failure_logs_when_router_blocks(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=True,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="BUY",
            size_usd=30.0,
            reason="trend_buy",
            source="test",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )

        with patch("runtime_execution.estimate_execution_gas_gwei", return_value=50.0):
            with patch("bot_runner.log") as mocked_log:
                process_price_tick(
                    runtime=runtime,
                    cycle_index=30,
                    mid=100.0,
                    source="test",
                    trade_logger=None,
                    equity_logger=None,
                    log_progress=True,
                )

        messages = "\n".join(str(call.args[0]) for call in mocked_log.call_args_list if call.args)
        self.assertIn("SHOULD_ENTER", messages)
        self.assertIn("ORDER_RESPONSE", messages)
        self.assertIn("EXECUTION_FAILED", messages)
        self.assertIn("execution_attempted false", messages)
        self.assertIn("execution_success false", messages)
        self.assertNotIn("ORDER_EXECUTED", messages)
        self.assertNotIn("EXECUTION_ATTEMPT", messages)

    def test_process_price_tick_allows_protective_exit_during_no_trade_mode(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=50.0,
            start_eth=2.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            execution_timeframe_seconds=60.0,
            trend_timeframe_seconds=60.0,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_no_trade_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="SELL",
            size_usd=20.0,
            reason="profit_lock_level_1",
            source="strategy",
            order_price=100.10,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: MarketRegimeAssessment(
            market_regime="RANGE",
            regime_confidence=84.0,
            range_width_pct=1.0,
            net_move_pct=0.10,
            direction_consistency=0.74,
            volatility_score=22.0,
            execution_regime="RANGE",
            trend_direction="neutral",
            range_location="upper",
            bounce_count=3,
            range_touch_count=4,
            sign_flip_ratio=0.18,
            noise_ratio=1.1,
            body_to_wick_ratio=0.62,
            ema_deviation_pct=0.08,
            mean_reversion_distance_pct=0.12,
            window_high=100.8,
            window_low=99.8,
            window_mean=100.2,
            price_position_pct=0.85,
        )
        runtime.edge_filter.assess = lambda **kwargs: EdgeAssessment(
            expected_edge_usd=0.18,
            expected_edge_bps=70.0,
            cost_estimate_usd=0.02,
            edge_score=78.0,
            edge_pass=True,
            slippage_estimate_bps=4.0,
            mev_risk_score=18.0,
        )
        runtime.portfolio.eth_cost_basis = 100.0
        runtime.profit_lock_state.anchor_price = 100.0
        runtime.engine.can_place_sell = lambda order, mode: True

        with patch("bot_runner.log") as mocked_log:
            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.10,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=True,
            )

        messages = "\n".join(str(call.args[0]) for call in mocked_log.call_args_list if call.args)
        self.assertIn("NO_TRADE_BYPASS", messages)
        self.assertIn("EXIT_TRIGGERED", messages)
        self.assertIn("ORDER_EXECUTED", messages)
        self.assertNotIn("NO_TRADE_OVERRIDE", messages)
        self.assertGreaterEqual(runtime.engine.trade_count, 1)

    def test_process_price_tick_preserves_upstream_block_reason_when_no_trade_overrides(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=50.0,
            start_eth=2.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            execution_timeframe_seconds=60.0,
            trend_timeframe_seconds=60.0,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_no_trade_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="SELL",
            size_usd=20.0,
            reason="range_sell",
            source="strategy",
            order_price=100.10,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )
        runtime.regime_detector.assess = lambda prices: MarketRegimeAssessment(
            market_regime="RANGE",
            regime_confidence=84.0,
            range_width_pct=1.0,
            net_move_pct=0.10,
            direction_consistency=0.74,
            volatility_score=22.0,
            execution_regime="RANGE",
            trend_direction="neutral",
            range_location="upper",
            bounce_count=3,
            range_touch_count=4,
            sign_flip_ratio=0.18,
            noise_ratio=1.1,
            body_to_wick_ratio=0.62,
            ema_deviation_pct=0.08,
            mean_reversion_distance_pct=0.12,
            window_high=100.8,
            window_low=99.8,
            window_mean=100.2,
            price_position_pct=0.85,
        )
        runtime.edge_filter.assess = lambda **kwargs: EdgeAssessment(
            expected_edge_usd=-0.16,
            expected_edge_bps=-39.95,
            cost_estimate_usd=0.16,
            edge_score=30.8,
            edge_pass=True,
            slippage_estimate_bps=3.6,
            mev_risk_score=14.2,
            size_multiplier=0.42,
            spread_multiplier=1.22,
            edge_penalty_reason="expected_edge_bad",
        )
        runtime.portfolio.eth_cost_basis = 100.0

        with patch("bot_runner.log") as mocked_log:
            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.10,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=True,
            )

        messages = "\n".join(str(call.args[0]) for call in mocked_log.call_args_list if call.args)
        filter_values = json.loads(runtime.last_filter_values)

        self.assertIn("NO_TRADE_OVERRIDE", messages)
        self.assertEqual(runtime.last_decision_block_reason, "no_trade")
        self.assertEqual(filter_values["trade_blocked_reason"], "no_trade")
        self.assertTrue(filter_values["no_trade_override"])
        self.assertEqual(filter_values["no_trade_override_reason"], "no_trade")
        self.assertEqual(filter_values["upstream_block_reason"], "no_trade")
        self.assertEqual(filter_values["edge_penalty_reason"], "expected_edge_bad")
        self.assertEqual(runtime.engine.trade_count, 0)

    def test_profitable_open_position_triggers_profit_exit_sell(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.01] * 30,
            reference_price=100.0,
            start_usdc=120.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            execution_timeframe_seconds=60.0,
            trend_timeframe_seconds=60.0,
            adaptive_flags={"enabled": False},
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: SimpleNamespace(
            regime="RANGE",
            volatility_state="NORMAL",
            feed_state="NORMAL",
            mode="RANGE_MAKER",
            strategy_mode="RANGE_MAKER",
            short_ma=100.0,
            long_ma=100.0,
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
            mm_mode="base_mm",
            activity_boost=0.0,
            freeze_recovery_mode=False,
            fill_quality_score=1.0,
            fill_quality_tier="normal",
            cooldown_multiplier=1.0,
            min_edge_multiplier=1.0,
        )
        runtime.regime_detector.assess = lambda prices: MarketRegimeAssessment(
            market_regime="RANGE",
            regime_confidence=84.0,
            range_width_pct=1.0,
            net_move_pct=0.10,
            direction_consistency=0.74,
            volatility_score=22.0,
            execution_regime="RANGE",
            trend_direction="neutral",
            range_location="lower",
            bounce_count=3,
            range_touch_count=4,
            sign_flip_ratio=0.18,
            noise_ratio=1.1,
            body_to_wick_ratio=0.62,
            ema_deviation_pct=0.08,
            mean_reversion_distance_pct=-0.12,
            window_high=100.8,
            window_low=99.8,
            window_mean=100.2,
            price_position_pct=0.15,
        )
        runtime.edge_filter.assess = lambda **kwargs: EdgeAssessment(
            expected_edge_usd=0.18,
            expected_edge_bps=70.0,
            cost_estimate_usd=0.02,
            edge_score=78.0,
            edge_pass=True,
            slippage_estimate_bps=4.0,
            mev_risk_score=18.0,
        )
        runtime.portfolio.eth_cost_basis = 100.0
        runtime.profit_lock_state.anchor_price = 100.0
        runtime.open_position_cycle = 0
        runtime.open_position_reason = "trend_buy"
        recorded_orders: list[tuple[str, str]] = []

        def _simulate_fill(order, mid):
            recorded_orders.append((order.side, order.trade_reason))
            return FillResult(
                filled=True,
                side=order.side,
                price=order.price,
                size_base=order.size_base,
                size_usd=order.size_usd,
                fee_usd=0.01,
                reason="fill",
                execution_type=order.execution_type,
                slippage_bps=order.slippage_bps,
                trade_reason=order.trade_reason,
            )

        runtime.engine.simulate_fill = _simulate_fill

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.01,
            source="test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )

        self.assertGreaterEqual(len(recorded_orders), 1)
        self.assertTrue(all(side == "sell" for side, _ in recorded_orders))
        self.assertTrue(all(reason == "profit_exit_sell" for _, reason in recorded_orders))


if __name__ == "__main__":
    unittest.main()
