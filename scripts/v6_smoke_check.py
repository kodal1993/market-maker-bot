from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bot_runner import build_summary, create_runtime, process_price_tick
from adaptive_market_maker import (
    ActivityFloorState,
    build_aggressiveness,
    classify_inventory_band,
    classify_regime,
    score_edge,
    select_mode,
)


def make_snapshot(**overrides):
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
    from adaptive_market_maker import MarketStateSnapshot

    return MarketStateSnapshot(**values)


def adaptive_flags(profile: str) -> dict[str, object]:
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


def run_scenario(bootstrap_prices: list[float], follow_prices: list[float], *, profile: str, toxic_events: list[dict[str, object]] | None = None):
    runtime = create_runtime(
        bootstrap_prices=bootstrap_prices,
        reference_price=bootstrap_prices[-1],
        start_usdc=bootstrap_prices[-1],
        start_eth=1.0,
        start_eth_usd=0.0,
        execution_timeframe_seconds=60.0,
        trend_timeframe_seconds=60.0,
        confirmation_timeframe_seconds=60.0,
        enable_trend_timeframe_filter=False,
        enable_confirmation_filter=False,
        enable_execution_engine=False,
        enable_trade_filter=False,
        enable_state_machine=False,
        adaptive_flags=adaptive_flags(profile),
    )
    for event in toxic_events or []:
        runtime.adaptive_fill_quality_events.append(event)
    for cycle_index, mid in enumerate(follow_prices, start=len(bootstrap_prices) + 1):
        process_price_tick(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=mid,
            source="v6_smoke",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )
    return build_summary(runtime), runtime


def run_session(prices: list[float], *, profile: str | None):
    bootstrap = prices[:40]
    runtime = create_runtime(
        bootstrap_prices=bootstrap,
        reference_price=bootstrap[-1],
        start_usdc=bootstrap[-1] * 0.65,
        start_eth=0.35,
        start_eth_usd=0.0,
        execution_timeframe_seconds=60.0,
        trend_timeframe_seconds=60.0,
        confirmation_timeframe_seconds=60.0,
        enable_trend_timeframe_filter=False,
        enable_confirmation_filter=False,
        enable_execution_engine=False,
        enable_trade_filter=False,
        enable_state_machine=False,
        adaptive_flags=None if profile is None else adaptive_flags(profile),
    )
    for cycle_index, mid in enumerate(prices[40:], start=40):
        process_price_tick(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=mid,
            source="v6_smoke_session",
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )
    summary = build_summary(runtime)
    return {
        "quotes": len(runtime.quote_cycle_history),
        "trades": runtime.engine.trade_count,
        "buys": summary["buy_count"],
        "sells": summary["sell_count"],
        "activity_floor_state": summary["activity_floor_state"],
        "trade_permission_state": summary["trade_permission_state"],
        "quote_decision": summary["quote_decision"],
    }


def main() -> None:
    range_bootstrap = [100.0 + ((index % 4) - 1.5) * 0.05 for index in range(40)]
    trend_bootstrap = [100.0 + (index * 0.55) for index in range(40)]
    chaos_bootstrap = [100.0, 101.6, 98.4, 102.2, 97.8] * 8

    range_summary, _ = run_scenario(range_bootstrap, [99.96, 100.02, 99.98, 100.01], profile="v6_balanced_paper")
    trend_summary, _ = run_scenario(
        trend_bootstrap,
        [trend_bootstrap[-1] + step for step in (0.60, 1.20, 1.85, 2.40)],
        profile="v6_balanced_paper",
    )
    chaos_events = [
        {
            "cycle_index": 34 + index,
            "move_5s_bps": 6.0,
            "move_15s_bps": 10.0,
            "move_30s_bps": 14.0,
            "expected_vs_realized_edge_bps": 22.0,
            "toxic": True,
        }
        for index in range(6)
    ]
    chaos_summary, _ = run_scenario(
        chaos_bootstrap,
        [101.7, 98.2, 102.4, 97.6],
        profile="v6_defensive_live",
        toxic_events=chaos_events,
    )

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
        queue_latency_quality=0.32,
    )
    chaos_regime = classify_regime(chaos_snapshot, profile="v6_defensive_live")
    chaos_edge = score_edge(chaos_snapshot, chaos_regime, cooldown_active=True, profile="v6_defensive_live")
    chaos_band = classify_inventory_band(chaos_snapshot, chaos_regime)
    chaos_mode = select_mode(
        chaos_snapshot,
        chaos_regime,
        chaos_edge,
        chaos_band,
        SimpleNamespace(trade_count=0, pnl_usd=0.0, drawdown_pct=0.0, toxic_fill_ratio=0.0, hit_rate=0.0, spread_baseline_multiplier=1.0, size_cap_multiplier=1.0, edge_threshold_multiplier=1.0, skew_strength_multiplier=1.0),
        SimpleNamespace(state="defensive_only", stage=3, size_multiplier=0.46, spread_multiplier=1.22, inventory_cap_multiplier=0.78, quote_enabled=True, buy_enabled=True, sell_enabled=False, reasons=["defensive_stage_3"]),
        ActivityFloorState("disabled", 0, 0.0, 0.0),
        SimpleNamespace(buy_enabled=True, sell_enabled=True),
        profile="v6_defensive_live",
    )
    chaos_aggr = build_aggressiveness(
        chaos_snapshot,
        chaos_regime,
        chaos_edge,
        chaos_band,
        SimpleNamespace(trade_count=0, pnl_usd=0.0, drawdown_pct=0.0, toxic_fill_ratio=0.0, hit_rate=0.0, spread_baseline_multiplier=1.0, size_cap_multiplier=1.0, edge_threshold_multiplier=1.0, skew_strength_multiplier=1.0),
        SimpleNamespace(state="defensive_only", stage=3, size_multiplier=0.46, spread_multiplier=1.22, inventory_cap_multiplier=0.78, quote_enabled=True, buy_enabled=True, sell_enabled=False, reasons=["defensive_stage_3"]),
        chaos_mode,
        ActivityFloorState("disabled", 0, 0.0, 0.0),
        profile="v6_defensive_live",
    )

    session_prices = range_bootstrap + [100.0 + ((index % 6) - 2.5) * 0.10 for index in range(40, 140)]
    baseline = run_session(session_prices, profile=None)
    aggressive = run_session(session_prices, profile="v6_aggressive_paper")

    print(
        json.dumps(
            {
                "range": {
                    "adaptive_regime": range_summary["adaptive_regime"],
                    "quote_enabled": range_summary["quote_enabled"],
                    "quote_decision": range_summary["quote_decision"],
                },
                "trend": {
                    "adaptive_regime": trend_summary["adaptive_regime"],
                    "adaptive_mode": trend_summary["adaptive_mode"],
                    "quote_skew_multiplier": trend_summary["quote_skew_multiplier"],
                    "quote_decision": trend_summary["quote_decision"],
                },
                "chaos": {
                    "bot_regime": chaos_summary["adaptive_regime"],
                    "classified_regime": chaos_regime.regime,
                    "defensive_stage": chaos_summary["defensive_stage"],
                    "risk_governor_state": chaos_summary["risk_governor_state"],
                    "quote_decision": chaos_summary["quote_decision"],
                    "model_defensive_mode": chaos_mode.mode,
                    "model_spread_mult": chaos_aggr.spread_multiplier,
                    "model_size_mult": chaos_aggr.size_multiplier,
                },
                "activity_comparison": {
                    "baseline": baseline,
                    "aggressive_paper": aggressive,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
