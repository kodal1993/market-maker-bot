from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from adaptive_market_maker import (
    ActivityFloorState,
    AdaptiveCyclePlan,
    AdaptiveEdgeAssessment,
    AdaptiveFeatureConfig,
    AdaptiveRegimeAssessment,
    AggressivenessProfile,
    FillQualitySnapshot,
    InventoryBandState,
    MarketStateSnapshot,
    ModeSelection,
    PerformanceAdaptationState,
    RiskGovernorState,
    assess_activity_floor,
    build_aggressiveness,
    classify_inventory_band,
    classify_regime,
    govern_risk,
    quote_decision_filter_values,
    score_edge,
    select_mode,
    soften_edge_assessment,
)
from bot_runner import build_summary, create_runtime, process_price_tick
from types_bot import EdgeAssessment


def adaptive_flags(profile: str = "v6_balanced_paper") -> dict[str, object]:
    return {
        "profile": profile,
        "enabled": True,
        "regime_enabled": True,
        "edge_enabled": True,
        "mode_selector_enabled": True,
        "dynamic_quoting_enabled": True,
        "risk_governor_enabled": True,
        "performance_adaptation_enabled": True,
        "inventory_bands_enabled": True,
        "fill_quality_enabled": True,
        "soft_filters_enabled": True,
        "logging_enabled": True,
    }


def make_snapshot(**overrides) -> MarketStateSnapshot:
    values = {
        "mid_price": 100.0,
        "short_return_bps": 4.0,
        "medium_return_bps": 8.0,
        "volatility": 0.001,
        "volatility_bps": 8.0,
        "microtrend_strength": 0.35,
        "spread_bps": 7.0,
        "spread_instability": 0.08,
        "price_jump_frequency": 0.05,
        "liquidity_estimate_usd": 600.0,
        "inventory_pct": 0.50,
        "inventory_deviation_pct": 0.0,
        "rolling_pnl_usd": 0.0,
        "rolling_drawdown_pct": 0.01,
        "recent_fill_count": 4,
        "adverse_fill_ratio": 0.10,
        "toxic_fill_ratio": 0.08,
        "expected_vs_realized_edge_bps": 2.0,
        "fill_rate": 0.18,
        "minutes_since_last_fill": 2.0,
        "price_mean": 100.0,
        "mean_reversion_distance_bps": 5.0,
        "range_width_bps": 24.0,
        "direction_consistency": 0.48,
        "sign_flip_ratio": 0.44,
        "quote_pressure_score": 10.0,
        "source_health_score": 1.0,
        "queue_latency_quality": 0.92,
    }
    values.update(overrides)
    return MarketStateSnapshot(**values)


def make_cycle_plan() -> AdaptiveCyclePlan:
    return AdaptiveCyclePlan(
        config=AdaptiveFeatureConfig(
            profile="V6_BALANCED_PAPER",
            enabled=True,
            regime_enabled=True,
            edge_enabled=True,
            mode_selector_enabled=True,
            dynamic_quoting_enabled=True,
            risk_governor_enabled=True,
            performance_adaptation_enabled=True,
            inventory_bands_enabled=True,
            fill_quality_enabled=True,
            soft_filters_enabled=True,
            logging_enabled=True,
        ),
        snapshot=make_snapshot(),
        fill_quality=FillQualitySnapshot(fill_count=4, adverse_fill_ratio=0.12, toxic_fill_ratio=0.08),
        regime=AdaptiveRegimeAssessment("RANGE", 0.74, {"RANGE": 74.0, "TREND_UP": 28.0, "TREND_DOWN": 18.0, "CHAOS": 12.0}, reason=["sign_flips_support_range"], trend_bias=0.08),
        edge=AdaptiveEdgeAssessment(18.0, {"expected_spread_capture": 16.0}, {"cooldown_penalty": 4.0}, "slightly_negative", "CAUTIOUS", []),
        inventory_band=InventoryBandState("soft_skew", 0.48, rebalance_side="sell", inventory_pressure=0.35, lower_bound=0.46, upper_bound=0.54, inventory_pressure_score=35.0, recovery_mode=True),
        performance=PerformanceAdaptationState(5, 1.2, 0.01, 0.0, 0.6, 1.0, 1.0, 1.0, 1.0),
        risk=RiskGovernorState("mild_defense", 1, 0.88, 1.08, 0.95, True, True, True, ["defensive_stage_1"]),
        activity_floor=ActivityFloorState("tighten_quotes", 14, 92.0, 2.0, 0.90, 1.05, 0.84, 0.92, True),
        mode=ModeSelection("base_mm", "RANGE_MAKER", True, True, True, 0.48, -0.16, "paper_activity_floor"),
        aggressiveness=AggressivenessProfile(63.0, 0.84, 0.94, 0.88, 1.18),
    )


class AdaptiveRegimeAwareTests(unittest.TestCase):
    def test_regime_classification_outputs_range_with_confidence_and_reason(self) -> None:
        snapshot = make_snapshot(
            short_return_bps=2.0,
            medium_return_bps=4.0,
            microtrend_strength=0.22,
            sign_flip_ratio=0.62,
            direction_consistency=0.38,
            mean_reversion_distance_bps=11.0,
        )

        assessment = classify_regime(snapshot, profile="v6_balanced_paper")

        self.assertEqual(assessment.regime, "RANGE")
        self.assertGreaterEqual(assessment.confidence, 0.0)
        self.assertLessEqual(assessment.confidence, 1.0)
        self.assertTrue(assessment.reason)

    def test_regime_classification_outputs_trend_and_chaos(self) -> None:
        trend_snapshot = make_snapshot(
            short_return_bps=22.0,
            medium_return_bps=48.0,
            microtrend_strength=1.20,
            direction_consistency=0.82,
            sign_flip_ratio=0.12,
        )
        chaos_snapshot = make_snapshot(
            short_return_bps=46.0,
            medium_return_bps=20.0,
            volatility_bps=42.0,
            spread_bps=32.0,
            spread_instability=0.72,
            price_jump_frequency=0.58,
            toxic_fill_ratio=0.74,
            adverse_fill_ratio=0.58,
            quote_pressure_score=78.0,
        )

        trend = classify_regime(trend_snapshot, profile="v6_balanced_paper")
        chaos = classify_regime(chaos_snapshot, profile="v6_balanced_paper")

        self.assertEqual(trend.regime, "TREND_UP")
        self.assertEqual(chaos.regime, "CHAOS")

    def test_edge_score_boundaries_and_permission_transitions(self) -> None:
        range_snapshot = make_snapshot()
        range_regime = classify_regime(range_snapshot, profile="v6_balanced_paper")
        positive_edge = score_edge(range_snapshot, range_regime, cooldown_active=False, profile="v6_balanced_paper")

        toxic_snapshot = make_snapshot(
            volatility_bps=42.0,
            spread_bps=32.0,
            spread_instability=0.62,
            price_jump_frequency=0.48,
            adverse_fill_ratio=0.68,
            toxic_fill_ratio=0.78,
            expected_vs_realized_edge_bps=24.0,
            quote_pressure_score=75.0,
            queue_latency_quality=0.32,
        )
        toxic_regime = classify_regime(toxic_snapshot, profile="v6_defensive_live")
        defensive_edge = score_edge(toxic_snapshot, toxic_regime, cooldown_active=True, profile="v6_defensive_live")

        self.assertGreaterEqual(positive_edge.total_score, -100.0)
        self.assertLessEqual(positive_edge.total_score, 100.0)
        self.assertIn(positive_edge.permission_state, {"FULL", "CAUTIOUS", "REDUCED"})
        self.assertLessEqual(defensive_edge.total_score, 100.0)
        self.assertGreaterEqual(defensive_edge.total_score, -100.0)
        self.assertIn(defensive_edge.permission_state, {"DEFENSIVE_ONLY", "BLOCKED"})

    def test_paper_activity_floor_triggers_after_inactivity(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 40,
            reference_price=100.0,
            start_usdc=100.0,
            start_eth=0.0,
            start_eth_usd=0.0,
            adaptive_flags=adaptive_flags("v6_aggressive_paper"),
        )
        runtime.quote_cycle_history.extend([1, 6, 12])

        with patch("adaptive_market_maker.BOT_MODE", "paper"):
            floor = assess_activity_floor(runtime, cycle_index=30, snapshot=make_snapshot(), profile="v6_aggressive_paper")

        self.assertTrue(floor.override_applied)
        self.assertIn(floor.state, {"tighten_quotes", "filters_relaxed"})
        self.assertGreater(floor.inactivity_cycles, 0)

    def test_defensive_stage_escalates_when_toxicity_is_high(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 40,
            reference_price=100.0,
            start_usdc=100.0,
            start_eth=0.0,
            start_eth_usd=0.0,
            adaptive_flags=adaptive_flags("v6_defensive_live"),
        )
        snapshot = make_snapshot(
            spread_bps=24.0,
            spread_instability=0.48,
            toxic_fill_ratio=0.72,
            adverse_fill_ratio=0.54,
            expected_vs_realized_edge_bps=18.0,
        )
        regime = classify_regime(snapshot, profile="v6_defensive_live")
        edge = score_edge(snapshot, regime, cooldown_active=True, profile="v6_defensive_live")
        inventory_band = classify_inventory_band(snapshot, regime)
        risk = govern_risk(
            runtime,
            cycle_index=40,
            snapshot=snapshot,
            fill_quality=FillQualitySnapshot(fill_count=5, adverse_fill_ratio=0.54, toxic_fill_ratio=0.72),
            regime=regime,
            edge=edge,
            inventory_band=inventory_band,
        )

        self.assertGreaterEqual(risk.stage, 2)
        self.assertIn(risk.state, {"strong_defense", "defensive_only", "hard_pause"})

    def test_trend_skew_is_bounded(self) -> None:
        snapshot = make_snapshot(short_return_bps=20.0, medium_return_bps=44.0, microtrend_strength=1.10, direction_consistency=0.80, sign_flip_ratio=0.14)
        regime = classify_regime(snapshot, profile="v6_balanced_paper")
        edge = score_edge(snapshot, regime, cooldown_active=False, profile="v6_balanced_paper")
        inventory_band = classify_inventory_band(snapshot, regime)
        performance = PerformanceAdaptationState(4, 1.0, 0.01, 0.0, 0.6, 1.0, 1.0, 1.0, 1.0)
        risk = RiskGovernorState("normal", 0, 1.0, 1.0, 1.0, True, True, True, [])
        mode = select_mode(
            snapshot,
            regime,
            edge,
            inventory_band,
            performance,
            risk,
            ActivityFloorState("disabled", 0, 0.0, 0.0),
            SimpleNamespace(buy_enabled=True, sell_enabled=True),
            profile="v6_balanced_paper",
        )

        self.assertEqual(mode.strategy_mode, "TREND_UP")
        self.assertGreater(mode.directional_bias, 0.0)
        self.assertLessEqual(mode.directional_bias, 1.0)

    def test_chaos_mode_cuts_size_and_widens_spread(self) -> None:
        range_snapshot = make_snapshot()
        range_regime = classify_regime(range_snapshot, profile="v6_balanced_paper")
        range_edge = score_edge(range_snapshot, range_regime, cooldown_active=False, profile="v6_balanced_paper")
        range_band = classify_inventory_band(range_snapshot, range_regime)
        performance = PerformanceAdaptationState(4, 1.0, 0.01, 0.0, 0.6, 1.0, 1.0, 1.0, 1.0)
        risk = RiskGovernorState("normal", 0, 1.0, 1.0, 1.0, True, True, True, [])
        floor = ActivityFloorState("disabled", 0, 0.0, 0.0)
        range_mode = select_mode(range_snapshot, range_regime, range_edge, range_band, performance, risk, floor, SimpleNamespace(buy_enabled=True, sell_enabled=True), profile="v6_balanced_paper")
        range_aggr = build_aggressiveness(range_snapshot, range_regime, range_edge, range_band, performance, risk, range_mode, floor, profile="v6_balanced_paper")

        chaos_snapshot = make_snapshot(
            short_return_bps=42.0,
            medium_return_bps=18.0,
            volatility_bps=42.0,
            spread_bps=32.0,
            spread_instability=0.72,
            price_jump_frequency=0.54,
            toxic_fill_ratio=0.74,
            adverse_fill_ratio=0.58,
            expected_vs_realized_edge_bps=18.0,
            quote_pressure_score=82.0,
        )
        chaos_regime = classify_regime(chaos_snapshot, profile="v6_balanced_paper")
        chaos_edge = score_edge(chaos_snapshot, chaos_regime, cooldown_active=True, profile="v6_balanced_paper")
        chaos_band = classify_inventory_band(chaos_snapshot, chaos_regime)
        chaos_risk = RiskGovernorState("strong_defense", 2, 0.70, 1.12, 0.88, True, True, True, ["defensive_stage_2"])
        chaos_mode = select_mode(chaos_snapshot, chaos_regime, chaos_edge, chaos_band, performance, chaos_risk, floor, SimpleNamespace(buy_enabled=True, sell_enabled=True), profile="v6_balanced_paper")
        chaos_aggr = build_aggressiveness(chaos_snapshot, chaos_regime, chaos_edge, chaos_band, performance, chaos_risk, chaos_mode, floor, profile="v6_balanced_paper")

        self.assertEqual(chaos_regime.regime, "CHAOS")
        self.assertGreater(chaos_aggr.spread_multiplier, range_aggr.spread_multiplier)
        self.assertLess(chaos_aggr.size_multiplier, range_aggr.size_multiplier)

    def test_soften_edge_assessment_uses_permission_state(self) -> None:
        cycle_plan = make_cycle_plan()
        edge_assessment = EdgeAssessment(
            expected_edge_usd=-0.01,
            expected_edge_bps=-3.0,
            cost_estimate_usd=0.02,
            edge_score=18.0,
            edge_pass=False,
            edge_reject_reason="expected_edge_negative",
            size_multiplier=1.0,
            spread_multiplier=1.0,
            cooldown_multiplier=1.0,
        )

        softened, filter_values = soften_edge_assessment(edge_assessment, cycle_plan)

        self.assertTrue(softened.edge_pass)
        self.assertEqual(filter_values["trade_permission_state"], cycle_plan.edge.permission_state)
        self.assertLess(softened.size_multiplier, 1.0)
        self.assertGreater(softened.spread_multiplier, 1.0)

    def test_quote_decision_filter_values_expose_v6_logging_fields(self) -> None:
        filter_values = quote_decision_filter_values(SimpleNamespace(), make_cycle_plan())

        for key in {
            "regime_label",
            "regime_confidence",
            "regime_reason",
            "edge_score",
            "edge_components",
            "activity_floor_state",
            "inactivity_cycles",
            "defensive_stage",
            "inventory_pressure_score",
            "trade_permission_state",
        }:
            self.assertIn(key, filter_values)

    def test_process_price_tick_exposes_v6_summary_fields(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0 + ((index % 4) * 0.04) for index in range(40)],
            reference_price=100.16,
            start_usdc=100.16,
            start_eth=1.0,
            start_eth_usd=0.0,
            adaptive_flags=adaptive_flags("v6_balanced_paper"),
        )

        process_price_tick(
            runtime=runtime,
            cycle_index=41,
            mid=100.12,
            source="adaptive_test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )
        summary = build_summary(runtime)

        self.assertIn(summary["adaptive_regime"], {"RANGE", "TREND_UP", "TREND_DOWN", "CHAOS"})
        self.assertIn("regime=", summary["quote_decision"])
        self.assertIn("perm=", summary["quote_decision"])
        self.assertIn("defensive_stage", summary)
        self.assertIn("trade_permission_state", summary)

    def test_inventory_band_uses_hard_limit_before_rebalance_only(self) -> None:
        snapshot = make_snapshot(inventory_pct=0.68, inventory_deviation_pct=18.0)
        regime = AdaptiveRegimeAssessment("RANGE", 0.70, {"RANGE": 70.0, "TREND_UP": 15.0, "TREND_DOWN": 10.0, "CHAOS": 5.0}, reason=["sign_flips_support_range"], trend_bias=0.0)

        band = classify_inventory_band(snapshot, regime)

        self.assertEqual(band.zone, "hard_breach")
        self.assertEqual(band.rebalance_side, "sell")


if __name__ == "__main__":
    unittest.main()
