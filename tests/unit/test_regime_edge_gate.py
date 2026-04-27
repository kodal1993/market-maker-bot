from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import (
    _apply_signal_pipeline,
    _build_inventory_emergency_candidate,
    _build_open_profit_exit_plan,
    _build_profit_lock_sell_plan,
    _record_fill,
    _reset_stale_loss_streak_if_idle,
    create_runtime,
)
from edge_filter import EdgeFilter
from regime_detector import RegimeDetector
from signal_gate import SignalGate
from sizing_engine import build_sizing_snapshot
from types_bot import DecisionOutcome, EdgeAssessment, ExecutionContext, FillResult, InventoryProfile, MarketRegimeAssessment, Quote, StrategyState


def build_regime(
    regime: str,
    *,
    confidence: float = 82.0,
    range_width_pct: float = 1.0,
    net_move_pct: float = 0.15,
    direction_consistency: float = 0.72,
    volatility_score: float = 24.0,
    price_position_pct: float | None = None,
) -> MarketRegimeAssessment:
    if price_position_pct is None:
        price_position_pct = 0.35 if regime == "RANGE" else 0.75
    return MarketRegimeAssessment(
        market_regime=regime,
        regime_confidence=confidence,
        range_width_pct=range_width_pct,
        net_move_pct=net_move_pct,
        direction_consistency=direction_consistency,
        volatility_score=volatility_score,
        execution_regime="RANGE" if regime == "RANGE" else "TREND" if regime.startswith("TREND") else "NO_TRADE",
        trend_direction="down" if regime == "TREND_DOWN" else "up" if regime == "TREND_UP" else "neutral",
        range_location="lower" if regime == "RANGE" else "upper",
        bounce_count=3,
        range_touch_count=4,
        sign_flip_ratio=0.22,
        noise_ratio=1.2,
        body_to_wick_ratio=0.55,
        ema_deviation_pct=0.08 if regime != "TREND_DOWN" else -0.24,
        mean_reversion_distance_pct=-0.10,
        window_high=100.8,
        window_low=99.8,
        window_mean=100.2,
        price_position_pct=price_position_pct,
    )


def build_edge(
    *,
    edge_pass: bool = True,
    reject_reason: str = "",
    penalty_reason: str = "",
    override_reason: str = "",
    edge_score: float = 78.0,
    expected_edge_usd: float = 0.18,
    expected_edge_bps: float = 70.0,
    size_multiplier: float = 1.0,
    spread_multiplier: float = 1.0,
) -> EdgeAssessment:
    return EdgeAssessment(
        expected_edge_usd=expected_edge_usd,
        expected_edge_bps=expected_edge_bps,
        cost_estimate_usd=0.02,
        edge_score=edge_score,
        edge_pass=edge_pass,
        edge_reject_reason=reject_reason,
        slippage_estimate_bps=4.0,
        mev_risk_score=18.0,
        size_multiplier=size_multiplier,
        spread_multiplier=spread_multiplier,
        edge_penalty_reason=penalty_reason,
        edge_override_reason=override_reason,
    )


class RegimeEdgeGateTests(unittest.TestCase):
    def _recovery_runtime_and_profile(self):
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=250.0,
            start_eth=2.5,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            adaptive_flags={"enabled": False},
        )
        runtime.state_context.current_state = StrategyState.WAIT_REENTRY
        runtime.current_minutes_since_last_fill = 180.0
        runtime.current_recent_trade_count_60m = 0
        runtime.current_inventory_drift_pct = -45.0
        runtime.current_freeze_recovery_mode = True
        runtime.current_inactivity_fallback_active = False
        runtime.current_risk_governor_state = "normal"
        runtime.current_active_regime = "RANGE"
        runtime.current_strategy_mode = "RANGE_MAKER"
        runtime.current_sizing = replace(
            build_sizing_snapshot(
                current_equity_usd=500.0,
                mid_price=100.0,
                portfolio_usdc=250.0,
                portfolio_eth=2.5,
            ),
            computed_force_trade_size_usd=25.0,
            force_trade_size_usd=25.0,
            max_trade_size_usd=25.0,
            min_notional_usd=1.0,
            available_quote_to_trade_usd=250.0,
        )
        profile = InventoryProfile(
            regime_label="normal",
            lower_bound=0.42,
            upper_bound=0.58,
            inventory_ratio=0.05,
            inventory_usd=25.0,
            equity_usd=500.0,
            allow_buy=True,
            allow_sell=True,
            max_buy_usd=250.0,
            max_sell_usd=0.0,
            soft_limit_usd=350.0,
        )
        return runtime, profile

    def test_wait_reentry_freeze_recovery_allows_chop_range_reentry(self) -> None:
        runtime, profile = self._recovery_runtime_and_profile()
        runtime.current_regime_assessment = replace(build_regime("CHOP"), execution_regime="RANGE")

        decision = _apply_signal_pipeline(
            runtime,
            cycle_index=200,
            decision=DecisionOutcome(action="BUY", size_usd=25.0, reason="reentry_pullback", source="reentry", order_price=100.0),
            strategy_mode="RANGE_MAKER",
            intelligence=SimpleNamespace(target_inventory_pct=0.50, short_ma=100.0, long_ma=100.0, volatility=0.001),
            quote=Quote(bid=99.95, ask=100.05, mid=100.0, spread_bps=10.0, mode="RANGE_MAKER"),
            mid=100.0,
            spread_bps=10.0,
            source="test",
            effective_max_inventory_usd=500.0,
            inventory_profile=profile,
            inventory_usd=25.0,
        )

        self.assertEqual(decision.action, "BUY")
        self.assertTrue(decision.allow_trade)
        self.assertTrue(decision.filter_values["recovery_mode_active"])
        self.assertEqual(decision.filter_values["recovery_gate_result"], "allow")
        self.assertNotEqual(decision.block_reason, "reentry_rejected_bad_regime")

    def test_upper_tf_conflict_reduces_recovery_size_without_rejecting(self) -> None:
        runtime, profile = self._recovery_runtime_and_profile()
        runtime.current_regime_assessment = replace(build_regime("RANGE"), execution_regime="RANGE")
        runtime.current_trend_bias = "sell_only"
        runtime.current_trend_short_ma = 99.0
        runtime.current_trend_long_ma = 100.0

        decision = _apply_signal_pipeline(
            runtime,
            cycle_index=200,
            decision=DecisionOutcome(action="BUY", size_usd=25.0, reason="reentry_pullback", source="reentry", order_price=100.0),
            strategy_mode="RANGE_MAKER",
            intelligence=SimpleNamespace(target_inventory_pct=0.50, short_ma=99.0, long_ma=100.0, volatility=0.001),
            quote=Quote(bid=99.95, ask=100.05, mid=100.0, spread_bps=10.0, mode="RANGE_MAKER"),
            mid=100.0,
            spread_bps=10.0,
            source="test",
            effective_max_inventory_usd=500.0,
            inventory_profile=profile,
            inventory_usd=25.0,
        )

        self.assertEqual(decision.action, "BUY")
        self.assertTrue(decision.allow_trade)
        self.assertTrue(decision.filter_values["upper_tf_conflict"])
        self.assertTrue(decision.filter_values["passive_maker_only"])
        self.assertAlmostEqual(decision.filter_values["size_after_reduction"], 3.0)

    def test_regime_detector_classifies_chop_market(self) -> None:
        detector = RegimeDetector(lookback_candles=14)
        prices = [
            100.00,
            100.10,
            99.95,
            100.08,
            99.97,
            100.04,
            99.99,
            100.02,
            99.98,
            100.01,
            100.00,
            100.02,
            99.99,
            100.01,
        ]

        assessment = detector.assess(prices)

        self.assertEqual(assessment.market_regime, "CHOP")
        self.assertEqual(assessment.execution_regime, "RANGE")
        self.assertGreaterEqual(assessment.regime_confidence, 70.0)

    def test_regime_detector_exposes_trend_direction_for_uptrend(self) -> None:
        detector = RegimeDetector(lookback_candles=14)
        prices = [
            100.0,
            100.3,
            100.6,
            100.8,
            101.0,
            101.3,
            101.5,
            101.7,
            101.9,
            102.1,
            102.4,
            102.7,
            103.0,
            103.2,
        ]

        assessment = detector.assess(prices)

        self.assertEqual(assessment.execution_regime, "TREND")
        self.assertEqual(assessment.trend_direction, "up")

    def test_regime_detector_uses_shock_cooldown_as_no_trade_chop(self) -> None:
        detector = RegimeDetector(lookback_candles=12)
        prices = [
            100.0,
            100.1,
            100.2,
            100.15,
            100.18,
            100.22,
            100.24,
            100.26,
            101.3,
            101.28,
            101.26,
            101.25,
        ]

        assessment = detector.assess(prices)

        self.assertEqual(assessment.market_regime, "CHOP")
        self.assertEqual(assessment.execution_regime, "RANGE")

    def test_signal_gate_softens_buy_in_trend_down(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="reentry_zone_1", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("TREND_DOWN", net_move_pct=-1.4, direction_consistency=0.88),
            edge_assessment=build_edge(),
            inventory_ratio=0.50,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=99.2,
            long_ma=100.0,
            momentum_bps=-18.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertIn("ema_downtrend_buy_soft", decision.gate_details["soft_guard_reasons"])
        self.assertLess(decision.gate_details["gate_size_multiplier"], 1.0)

    def test_signal_gate_softens_sell_in_uptrend(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="SELL", size_usd=20.0, reason="quoted_sell", source="test"),
            strategy_mode="TREND_UP",
            regime_assessment=build_regime("TREND_UP", net_move_pct=1.2, direction_consistency=0.85),
            edge_assessment=build_edge(),
            inventory_ratio=0.55,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.8,
            long_ma=100.0,
            momentum_bps=20.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertIn("ema_uptrend_sell_soft", decision.gate_details["soft_guard_reasons"])
        self.assertLess(decision.gate_details["gate_size_multiplier"], 1.0)

    def test_signal_gate_skips_soft_guards_for_inventory_emergency_override(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(
                action="SELL",
                size_usd=20.0,
                reason="inventory_force_reduce",
                source="inventory",
                filter_values={"inventory_emergency_override": True},
            ),
            strategy_mode="TREND_UP",
            regime_assessment=build_regime("TREND_UP", net_move_pct=1.2, direction_consistency=0.85),
            edge_assessment=build_edge(override_reason="inventory_emergency_override"),
            inventory_ratio=0.85,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.8,
            long_ma=100.0,
            momentum_bps=20.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertTrue(decision.gate_details["inventory_emergency_override"])
        self.assertEqual(decision.gate_details["soft_guard_reasons"], [])
        self.assertEqual(decision.gate_details["gate_size_multiplier"], 1.0)

    def test_signal_gate_allows_range_signal_with_positive_edge(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("RANGE"),
            edge_assessment=build_edge(),
            inventory_ratio=0.45,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.01,
            long_ma=100.0,
            momentum_bps=6.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertEqual(decision.approved_mode, "range_entry")

    def test_signal_gate_blocks_hard_edge_reject(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("RANGE"),
            edge_assessment=build_edge(
                edge_pass=False,
                reject_reason="price_impact_too_high",
                edge_score=34.0,
                expected_edge_usd=-0.03,
                expected_edge_bps=-15.0,
            ),
            inventory_ratio=0.45,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.01,
            long_ma=100.0,
            momentum_bps=4.0,
        )

        self.assertFalse(decision.allow_trade)
        self.assertEqual(decision.blocked_reason, "price_impact_too_high")

    def test_signal_gate_allows_negative_expected_edge_when_softened(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("RANGE"),
            edge_assessment=build_edge(
                edge_pass=True,
                penalty_reason="expected_edge_bad",
                edge_score=34.0,
                expected_edge_usd=-0.03,
                expected_edge_bps=-15.0,
                size_multiplier=0.42,
                spread_multiplier=1.22,
            ),
            inventory_ratio=0.45,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.01,
            long_ma=100.0,
            momentum_bps=4.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertEqual(decision.gate_details["edge_penalty_reason"], "expected_edge_bad")

    def test_signal_gate_softens_buy_on_strong_negative_momentum(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("RANGE"),
            edge_assessment=build_edge(),
            inventory_ratio=0.48,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.0,
            long_ma=100.0,
            momentum_bps=-48.0,
        )

        self.assertTrue(decision.allow_trade)
        self.assertIn("momentum_drop_buy_soft", decision.gate_details["soft_guard_reasons"])
        self.assertGreater(decision.gate_details["gate_spread_multiplier"], 1.0)

    def test_signal_gate_softens_buy_when_confirmation_still_falling(self) -> None:
        gate = SignalGate()
        decision = gate.evaluate(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test"),
            strategy_mode="RANGE_MAKER",
            regime_assessment=build_regime("RANGE"),
            edge_assessment=build_edge(),
            inventory_ratio=0.48,
            target_base_pct=0.50,
            consecutive_losses=0,
            loss_pause_remaining_minutes=0.0,
            short_ma=100.0,
            long_ma=100.0,
            momentum_bps=-8.0,
            confirmation_enabled=True,
            confirmation_momentum_bps=-24.0,
            confirmation_slowing=False,
        )

        self.assertTrue(decision.allow_trade)
        self.assertIn("confirmation_blocks_buy_soft", decision.gate_details["soft_guard_reasons"])
        self.assertLess(decision.gate_details["gate_size_multiplier"], 1.0)

    def test_edge_filter_rejects_reentry_with_shallow_pullback(self) -> None:
        edge_filter = EdgeFilter()
        assessment = edge_filter.assess(
            signal=DecisionOutcome(
                action="BUY",
                size_usd=20.0,
                reason="reentry_zone_1",
                source="test",
                order_price=100.0,
            ),
            context=ExecutionContext(
                pair="WETH/USDC",
                router="uniswap_v3",
                mid_price=100.0,
                quote_bid=99.98,
                quote_ask=100.02,
                router_price=100.0,
                backup_price=100.0,
                onchain_ref_price=100.0,
                twap_price=100.0,
                spread_bps=4.0,
                volatility=0.001,
                liquidity_usd=1_000_000.0,
                gas_price_gwei=3.0,
                market_mode="RANGE",
            ),
            regime_assessment=build_regime("RANGE"),
            inventory_usd=40.0,
            target_base_usd=50.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=100.1,
            current_profit_pct=None,
        )

        self.assertFalse(assessment.edge_pass)
        self.assertEqual(assessment.edge_reject_reason, "reentry_low_pullback")

    def test_range_sell_with_zero_window_mean_does_not_create_huge_edge(self) -> None:
        edge_filter = EdgeFilter()
        regime = replace(build_regime("RANGE"), window_mean=0.0)

        assessment = edge_filter.assess(
            signal=DecisionOutcome(
                action="SELL",
                size_usd=10.0,
                reason="force_trade_sell",
                source="activity_floor",
                order_price=2334.0,
            ),
            context=ExecutionContext(
                pair="WETH/USDC",
                router="uniswap_v3",
                mid_price=2334.0,
                quote_bid=2333.5,
                quote_ask=2334.5,
                router_price=2334.0,
                backup_price=2334.0,
                onchain_ref_price=2334.0,
                twap_price=2334.0,
                spread_bps=4.0,
                volatility=0.001,
                liquidity_usd=1_000_000.0,
                gas_price_gwei=3.0,
                market_mode="RANGE",
            ),
            regime_assessment=regime,
            inventory_usd=250.0,
            target_base_usd=250.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
        )

        self.assertLess(abs(assessment.expected_edge_bps), 1_000.0)

    def test_expected_edge_cost_units_stay_proportional_to_order_size(self) -> None:
        edge_filter = EdgeFilter()
        assessment = edge_filter.assess(
            signal=DecisionOutcome(
                action="BUY",
                size_usd=25.0,
                reason="reentry_pullback",
                source="reentry",
                order_price=2500.0,
            ),
            context=ExecutionContext(
                pair="WETH/USDC",
                router="uniswap_v3",
                mid_price=2500.0,
                quote_bid=2499.0,
                quote_ask=2501.0,
                router_price=2500.0,
                backup_price=2500.0,
                onchain_ref_price=2500.0,
                twap_price=2500.0,
                spread_bps=4.0,
                volatility=0.001,
                liquidity_usd=1_000_000.0,
                gas_price_gwei=3.0,
                market_mode="RANGE",
                metadata={"adverse_selection_bps": 20.0},
            ),
            regime_assessment=build_regime("RANGE"),
            inventory_usd=125.0,
            target_base_usd=250.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=2520.0,
            current_profit_pct=None,
            recovery_mode_active=True,
        )

        self.assertGreater(assessment.expected_edge_usd, -1.0)
        self.assertAlmostEqual(assessment.fee_estimate_usd, 0.0125)
        self.assertAlmostEqual(assessment.adverse_selection_usd, 0.05, places=6)

    def test_edge_filter_applies_inventory_soft_limit_penalty_and_bonus(self) -> None:
        edge_filter = EdgeFilter()
        context = ExecutionContext(
            pair="WETH/USDC",
            router="uniswap_v3",
            mid_price=100.0,
            quote_bid=99.98,
            quote_ask=100.02,
            router_price=100.0,
            backup_price=100.0,
            onchain_ref_price=100.0,
            twap_price=100.0,
            spread_bps=4.0,
            volatility=0.001,
            liquidity_usd=1_000_000.0,
            gas_price_gwei=3.0,
            market_mode="RANGE",
        )
        inventory_profile = InventoryProfile(
            regime_label="normal",
            lower_bound=0.42,
            upper_bound=0.58,
            inventory_ratio=0.82,
            inventory_usd=82.0,
            equity_usd=100.0,
            allow_buy=True,
            allow_sell=True,
            max_buy_usd=10.0,
            max_sell_usd=40.0,
            soft_limit_usd=75.0,
            soft_limit_hit=True,
        )

        baseline_buy = edge_filter.assess(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test", order_price=100.0),
            context=context,
            regime_assessment=build_regime("RANGE"),
            inventory_usd=82.0,
            target_base_usd=50.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
        )
        penalized_buy = edge_filter.assess(
            signal=DecisionOutcome(action="BUY", size_usd=20.0, reason="quoted_buy", source="test", order_price=100.0),
            context=context,
            regime_assessment=build_regime("RANGE"),
            inventory_usd=82.0,
            target_base_usd=50.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
            inventory_profile=inventory_profile,
        )
        baseline_sell = edge_filter.assess(
            signal=DecisionOutcome(action="SELL", size_usd=20.0, reason="quoted_sell", source="test", order_price=100.0),
            context=context,
            regime_assessment=build_regime("RANGE"),
            inventory_usd=82.0,
            target_base_usd=50.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
        )
        boosted_sell = edge_filter.assess(
            signal=DecisionOutcome(action="SELL", size_usd=20.0, reason="quoted_sell", source="test", order_price=100.0),
            context=context,
            regime_assessment=build_regime("RANGE"),
            inventory_usd=82.0,
            target_base_usd=50.0,
            consecutive_losses=0,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
            inventory_profile=inventory_profile,
        )

        self.assertLess(penalized_buy.expected_edge_usd, baseline_buy.expected_edge_usd)
        self.assertGreater(boosted_sell.expected_edge_usd, baseline_sell.expected_edge_usd)

    def test_edge_filter_softens_negative_edge_into_penalty(self) -> None:
        edge_filter = EdgeFilter()
        assessment = edge_filter.assess(
            signal=DecisionOutcome(
                action="BUY",
                size_usd=30.0,
                reason="trend_buy",
                source="test",
                order_price=100.0,
            ),
            context=ExecutionContext(
                pair="WETH/USDC",
                router="uniswap_v3",
                mid_price=100.0,
                quote_bid=99.91,
                quote_ask=100.09,
                router_price=100.0,
                backup_price=100.0,
                onchain_ref_price=100.0,
                twap_price=100.0,
                spread_bps=18.0,
                volatility=0.002,
                liquidity_usd=10_000.0,
                gas_price_gwei=8.0,
                market_mode="TREND",
            ),
            regime_assessment=build_regime("TREND_UP", net_move_pct=0.12, price_position_pct=0.70),
            inventory_usd=40.0,
            target_base_usd=50.0,
            consecutive_losses=4,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
            min_edge_bps=-5.0,
        )

        self.assertTrue(assessment.edge_pass)
        self.assertEqual(assessment.edge_penalty_reason, "expected_edge_bad")
        self.assertLess(assessment.size_multiplier, 1.0)
        self.assertGreater(assessment.spread_multiplier, 1.0)

    def test_force_trade_active_bypasses_edge_filter(self) -> None:
        edge_filter = EdgeFilter()
        assessment = edge_filter.assess(
            signal=DecisionOutcome(
                action="BUY",
                size_usd=30.0,
                reason="trend_buy",
                source="test",
                order_price=100.0,
            ),
            context=ExecutionContext(
                pair="WETH/USDC",
                router="uniswap_v3",
                mid_price=100.0,
                quote_bid=99.91,
                quote_ask=100.09,
                router_price=100.0,
                backup_price=100.0,
                onchain_ref_price=100.0,
                twap_price=100.0,
                spread_bps=18.0,
                volatility=0.002,
                liquidity_usd=10_000.0,
                gas_price_gwei=8.0,
                market_mode="TREND",
            ),
            regime_assessment=build_regime("TREND_UP", net_move_pct=0.12, price_position_pct=0.70),
            inventory_usd=40.0,
            target_base_usd=50.0,
            consecutive_losses=4,
            last_loss_cycle=None,
            last_loss_reason="",
            cycle_index=10,
            cycle_seconds=60.0,
            last_sell_price=None,
            current_profit_pct=None,
            min_edge_bps=-5.0,
            force_trade_active=True,
        )

        self.assertTrue(assessment.edge_pass)
        self.assertEqual(assessment.edge_override_reason, "force_trade_active")
        self.assertEqual(assessment.edge_penalty_reason, "")

    def test_inventory_emergency_override_builds_forced_rebalance_candidate(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=20.0,
            start_eth=1.2,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
        )
        runtime.current_inventory_drift_pct = 40.0
        inventory_profile = InventoryProfile(
            regime_label="normal",
            lower_bound=0.42,
            upper_bound=0.58,
            inventory_ratio=0.90,
            inventory_usd=120.0,
            equity_usd=140.0,
            allow_buy=True,
            allow_sell=True,
            max_buy_usd=0.0,
            max_sell_usd=60.0,
            soft_limit_usd=100.0,
        )

        candidate = _build_inventory_emergency_candidate(
            runtime,
            strategy_mid=100.0,
            strategy_sell_price=100.2,
            trade_size_usd=15.0,
            base_trade_size_usd=20.0,
            available_quote_usd=20.0,
            inventory_profile=inventory_profile,
            sell_state_allowed=True,
            buy_state_allowed=True,
            in_cooldown=False,
            min_notional_usd=10.0,
            default_buy_inventory_cap=200.0,
            reentry_buy_inventory_cap=200.0,
            min_sell_price=None,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.action, "SELL")
        self.assertEqual(candidate.reason, "inventory_force_reduce")
        self.assertTrue(candidate.filter_values["inventory_emergency_override"])

    def test_eight_consecutive_losses_activate_loss_pause(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
        )

        fill = FillResult(
            filled=True,
            side="sell",
            price=99.0,
            size_base=0.10,
            size_usd=9.9,
            fee_usd=0.01,
            reason="filled",
            execution_type="taker",
            slippage_bps=2.0,
            trade_reason="quoted_sell",
        )

        for cycle_index in range(1, 9):
            _record_fill(
                runtime=runtime,
                cycle_index=cycle_index,
                mode="RANGE_MAKER",
                fill=fill,
                realized_pnl_delta=-1.0,
            )

        self.assertEqual(runtime.loss_streak, 8)
        self.assertIsNotNone(runtime.loss_pause_until_cycle)
        self.assertGreater(runtime.loss_pause_until_cycle or 0, 8)
        self.assertEqual(runtime.last_loss_cycle, 8)

    def test_loss_streak_resets_after_thirty_minutes_without_trades(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            cycle_seconds=60.0,
        )
        runtime.loss_streak = 5
        runtime.loss_pause_until_cycle = 120
        runtime.last_loss_cycle = 1
        runtime.last_loss_trade_reason = "quoted_sell"
        runtime.last_trade_cycle_any = 0

        _reset_stale_loss_streak_if_idle(runtime, cycle_index=31)

        self.assertEqual(runtime.loss_streak, 0)
        self.assertIsNone(runtime.loss_pause_until_cycle)
        self.assertIsNone(runtime.last_loss_cycle)
        self.assertEqual(runtime.last_loss_trade_reason, "")

    def test_time_exit_sell_plan_triggers_for_stale_position(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            cycle_seconds=60.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
        )
        runtime.current_regime_assessment = build_regime("TREND_UP", net_move_pct=0.8, direction_consistency=0.82)
        runtime.current_market_mode = "TREND"
        runtime.open_position_cycle = 0
        runtime.open_position_reason = "trend_buy"
        runtime.current_volatility_bucket = "NORMAL"
        runtime.profit_lock_state.anchor_price = 100.0

        reason, size_usd = _build_profit_lock_sell_plan(runtime, cycle_index=120, mid=100.05)

        self.assertEqual(reason, "time_exit_sell")
        self.assertGreater(size_usd, 0.0)

    def test_profit_exit_sell_plan_triggers_for_profitable_open_position(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            cycle_seconds=60.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
        )
        runtime.current_regime_assessment = build_regime("TREND_UP", net_move_pct=0.8, direction_consistency=0.82)
        runtime.current_market_mode = "TREND"
        runtime.current_volatility_bucket = "NORMAL"
        runtime.open_position_cycle = 0
        runtime.open_position_reason = "trend_buy"
        runtime.profit_lock_state.anchor_price = 100.0

        reason, size_usd = _build_open_profit_exit_plan(runtime, mid=100.20)

        self.assertEqual(reason, "profit_exit_sell")
        self.assertGreater(size_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
