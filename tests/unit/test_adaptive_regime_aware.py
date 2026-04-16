from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from adaptive_market_maker import (
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
    classify_inventory_band,
    register_fill_quality_probe,
    soften_edge_assessment,
    update_fill_quality_probes,
)
from bot_runner import build_summary, create_runtime, process_price_tick
from types_bot import EdgeAssessment, FillResult


class AdaptiveRegimeAwareTests(unittest.TestCase):
    def test_fill_quality_probe_marks_toxic_buy_fill(self) -> None:
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 40,
            reference_price=100.0,
            start_eth_usd=0.0,
            adaptive_flags={
                "enabled": True,
                "fill_quality_enabled": True,
            },
        )
        fill = FillResult(
            filled=True,
            side="buy",
            price=100.0,
            size_base=0.1,
            size_usd=10.0,
            fee_usd=0.02,
            reason="fill",
            trade_reason="range_buy",
        )
        register_fill_quality_probe(runtime, cycle_index=0, fill=fill, expected_edge_bps=6.0)

        snapshot = update_fill_quality_probes(runtime, cycle_index=5, mid=99.0)

        self.assertEqual(snapshot.fill_count, 1)
        self.assertAlmostEqual(snapshot.toxic_fill_ratio, 1.0, delta=1e-6)
        self.assertGreater(snapshot.average_adverse_bps, 0.0)

    def test_soften_edge_assessment_turns_low_edge_reject_into_soft_penalty(self) -> None:
        cycle_plan = AdaptiveCyclePlan(
            config=AdaptiveFeatureConfig(
                profile="TEST",
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
            snapshot=None,  # type: ignore[arg-type]
            fill_quality=FillQualitySnapshot(),
            regime=AdaptiveRegimeAssessment("range_clean", 72.0, {"range_clean": 72.0}),
            edge=AdaptiveEdgeAssessment(44.0, {"market_quality": 60.0}, {"cooldown_penalty": 4.0}, "slightly_negative"),
            inventory_band=InventoryBandState("soft_skew", 0.48),
            performance=PerformanceAdaptationState(5, 1.2, 0.01, 0.0, 0.6, 1.0, 1.0, 1.0, 1.0),
            risk=RiskGovernorState("soft_brake", 0.72, 1.08, 0.88, True, ["risk_soft_brake"]),
            mode=ModeSelection("defensive_mm", "RANGE_MAKER", True, True, True, 0.48, -0.2, "soft_edge"),
            aggressiveness=AggressivenessProfile(36.0, 0.55, 1.16, 1.10, 1.20),
        )
        edge_assessment = EdgeAssessment(
            expected_edge_usd=-0.01,
            expected_edge_bps=-3.0,
            cost_estimate_usd=0.02,
            edge_score=18.0,
            edge_pass=False,
            edge_reject_reason="expected_edge_below_min",
            size_multiplier=1.0,
            spread_multiplier=1.0,
            cooldown_multiplier=1.0,
        )

        softened, filter_values = soften_edge_assessment(edge_assessment, cycle_plan)

        self.assertTrue(softened.edge_pass)
        self.assertEqual(softened.edge_reject_reason, "")
        self.assertLess(softened.size_multiplier, 1.0)
        self.assertGreater(softened.spread_multiplier, 1.0)
        self.assertTrue(filter_values["adaptive_edge_softened"])

    def test_process_price_tick_exposes_adaptive_summary_fields(self) -> None:
        random.seed(7)
        runtime = create_runtime(
            bootstrap_prices=[100.0 + (index * 0.08) for index in range(40)],
            reference_price=103.2,
            start_usdc=103.2,
            start_eth=1.0,
            start_eth_usd=0.0,
            adaptive_flags={
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
            },
        )

        process_price_tick(
            runtime=runtime,
            cycle_index=41,
            mid=103.6,
            source="adaptive_test",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )
        summary = build_summary(runtime)

        self.assertTrue(summary["adaptive_regime"].startswith("trend_") or summary["adaptive_regime"] in {"range_clean", "breakout"})
        self.assertNotEqual(summary["adaptive_mode"], "")
        self.assertIn(summary["risk_governor_state"], {"normal", "soft_brake", "hard_brake", "kill_switch"})
        self.assertNotEqual(summary["quote_decision"], "")

    def test_inventory_band_uses_hard_limit_before_rebalance_only(self) -> None:
        snapshot = MarketStateSnapshot(
            mid_price=100.0,
            short_return_bps=10.0,
            medium_return_bps=20.0,
            volatility=0.001,
            volatility_bps=10.0,
            spread_bps=8.0,
            liquidity_estimate_usd=500.0,
            inventory_pct=0.68,
            inventory_deviation_pct=18.0,
            rolling_pnl_usd=0.0,
            rolling_drawdown_pct=0.01,
            recent_fill_count=2,
            adverse_fill_ratio=0.0,
            toxic_fill_ratio=0.0,
            expected_vs_realized_edge_bps=0.0,
            fill_rate=0.2,
            minutes_since_last_fill=1.0,
            price_mean=100.0,
            mean_reversion_distance_bps=4.0,
            range_width_bps=30.0,
            direction_consistency=0.4,
            sign_flip_ratio=0.2,
            quote_pressure_score=10.0,
            source_health_score=1.0,
        )

        with (
            patch("adaptive_market_maker.ADAPTIVE_INVENTORY_HARD_MIN", 0.30),
            patch("adaptive_market_maker.ADAPTIVE_INVENTORY_HARD_MAX", 0.70),
        ):
            band = classify_inventory_band(snapshot)

        self.assertEqual(band.zone, "hard_skew")
        self.assertEqual(band.rebalance_side, "sell")


if __name__ == "__main__":
    unittest.main()
