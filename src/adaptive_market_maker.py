from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from config import (
    ADAPTIVE_ADVERSE_DEFENSIVE_THRESHOLD,
    ADAPTIVE_ADVERSE_SIZE_REDUCE_MULTIPLIER,
    ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD,
    ADAPTIVE_ADVERSE_WIDEN_MULTIPLIER,
    ADAPTIVE_ADVERSE_WIDEN_THRESHOLD,
    ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE,
    ADAPTIVE_AGGRESSIVE_SIZE_MULTIPLIER,
    ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER,
    ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE,
    ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER,
    ADAPTIVE_DYNAMIC_QUOTING_ENABLED,
    ADAPTIVE_EDGE_ENABLED,
    ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE,
    ADAPTIVE_EDGE_STANDBY_SCORE,
    ADAPTIVE_FILL_QUALITY_ENABLED,
    ADAPTIVE_INVENTORY_BANDS_ENABLED,
    ADAPTIVE_INVENTORY_HARD_MAX,
    ADAPTIVE_INVENTORY_HARD_MIN,
    ADAPTIVE_INVENTORY_NEUTRAL_MAX,
    ADAPTIVE_INVENTORY_NEUTRAL_MIN,
    ADAPTIVE_INVENTORY_SOFT_MAX,
    ADAPTIVE_INVENTORY_SOFT_MIN,
    ADAPTIVE_INVENTORY_STRONG_MAX,
    ADAPTIVE_INVENTORY_STRONG_MIN,
    ADAPTIVE_LOGGING_ENABLED,
    ADAPTIVE_MARKET_MAKER_ENABLED,
    ADAPTIVE_MILD_TREND_SKEW_STRENGTH,
    ADAPTIVE_MM_PROFILE,
    ADAPTIVE_MODE_SELECTOR_ENABLED,
    ADAPTIVE_NORMAL_MODE_MIN_EDGE,
    ADAPTIVE_NORMAL_SIZE_MULTIPLIER,
    ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER,
    ADAPTIVE_PERFORMANCE_ADAPTATION_ENABLED,
    ADAPTIVE_PERF_EDGE_THRESHOLD_LOWER_BOUND,
    ADAPTIVE_PERF_EDGE_THRESHOLD_UPPER_BOUND,
    ADAPTIVE_PERF_WINDOW_MINUTES,
    ADAPTIVE_PERF_SIZE_CAP_LOWER_BOUND,
    ADAPTIVE_PERF_SIZE_CAP_UPPER_BOUND,
    ADAPTIVE_PERF_SKEW_LOWER_BOUND,
    ADAPTIVE_PERF_SKEW_UPPER_BOUND,
    ADAPTIVE_PERF_SPREAD_LOWER_BOUND,
    ADAPTIVE_PERF_SPREAD_UPPER_BOUND,
    ADAPTIVE_REBALANCE_ONLY_SPREAD_MULTIPLIER,
    ADAPTIVE_REBALANCE_SIZE_MULTIPLIER,
    ADAPTIVE_REBALANCE_SKEW_STRENGTH,
    ADAPTIVE_REGIME_BREAKOUT_BPS,
    ADAPTIVE_REGIME_EXTREME_EVENT_SCORE,
    ADAPTIVE_REGIME_ENABLED,
    ADAPTIVE_REGIME_EVENT_VOL_MULTIPLIER,
    ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD,
    ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS,
    ADAPTIVE_REGIME_MEDIUM_CONFIDENCE,
    ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE,
    ADAPTIVE_REGIME_TREND_CONFIDENCE,
    ADAPTIVE_REPORT_WINDOW_MINUTES,
    ADAPTIVE_RISK_GOVERNOR_ENABLED,
    ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT,
    ADAPTIVE_RISK_HARD_DRAWDOWN_PCT,
    ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT,
    ADAPTIVE_RISK_HARD_TOXIC_FILL_RATIO,
    ADAPTIVE_RISK_KILL_DRAWDOWN_PCT,
    ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES,
    ADAPTIVE_RISK_KILL_LIQUIDITY_USD,
    ADAPTIVE_RISK_KILL_TOXIC_CLUSTER_COUNT,
    ADAPTIVE_RISK_KILL_TOXIC_FILL_RATIO,
    ADAPTIVE_RISK_SOFT_LIQUIDITY_USD,
    ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT,
    ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO,
    ADAPTIVE_SKEWED_MM_SPREAD_MULTIPLIER,
    ADAPTIVE_SOFT_FILTERS_ENABLED,
    ADAPTIVE_STRONG_TREND_SKEW_STRENGTH,
    ADAPTIVE_TREND_ASSIST_SIZE_MULTIPLIER,
    ADAPTIVE_TREND_ASSIST_SPREAD_MULTIPLIER,
    HIGH_VOL_THRESHOLD,
    RANGE_TARGET_INVENTORY_MAX,
    RANGE_TARGET_INVENTORY_MIN,
)
from types_bot import EdgeAssessment


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if abs(denominator) <= 1e-9:
        return default
    return numerator / denominator


def _safe_round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _return_bps(prices: list[float], lookback: int) -> float:
    if len(prices) < max(lookback + 1, 2):
        return 0.0
    start_price = prices[-(lookback + 1)]
    end_price = prices[-1]
    if start_price <= 0:
        return 0.0
    return ((end_price / start_price) - 1.0) * 10_000.0


def _price_mean(prices: list[float], lookback: int) -> float:
    if not prices:
        return 0.0
    window = prices[-max(lookback, 1):]
    return sum(window) / len(window)


def _range_width_bps(prices: list[float], lookback: int) -> float:
    if not prices:
        return 0.0
    window = prices[-max(lookback, 1):]
    highest = max(window)
    lowest = min(window)
    midpoint = _price_mean(window, len(window))
    if midpoint <= 0:
        return 0.0
    return ((highest - lowest) / midpoint) * 10_000.0


def _direction_consistency(prices: list[float], lookback: int) -> float:
    if len(prices) < max(lookback + 1, 2):
        return 0.0
    signs: list[int] = []
    window = prices[-(lookback + 1):]
    for index in range(1, len(window)):
        delta = window[index] - window[index - 1]
        if delta > 0:
            signs.append(1)
        elif delta < 0:
            signs.append(-1)
    if not signs:
        return 0.0
    dominant = max(signs.count(1), signs.count(-1))
    return dominant / len(signs)


def _sign_flip_ratio(prices: list[float], lookback: int) -> float:
    if len(prices) < max(lookback + 1, 3):
        return 0.0
    raw_signs: list[int] = []
    window = prices[-(lookback + 1):]
    for index in range(1, len(window)):
        delta = window[index] - window[index - 1]
        if delta > 0:
            raw_signs.append(1)
        elif delta < 0:
            raw_signs.append(-1)
    if len(raw_signs) < 2:
        return 0.0
    flips = sum(1 for index in range(1, len(raw_signs)) if raw_signs[index] != raw_signs[index - 1])
    return flips / max(len(raw_signs) - 1, 1)


def _rolling_drawdown_pct(equities: list[float]) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    worst = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, max(peak - value, 0.0) / peak)
    return worst


def _drawdown_acceleration_pct(equities: list[float]) -> float:
    if len(equities) < 4:
        return 0.0
    midpoint = max(len(equities) // 2, 1)
    early = equities[:midpoint]
    late = equities[midpoint:]
    if not early or not late:
        return 0.0
    return max(_rolling_drawdown_pct(late) - _rolling_drawdown_pct(early), 0.0)


def _signed_fill_move_bps(side: str, fill_price: float, current_mid: float) -> float:
    if fill_price <= 0 or current_mid <= 0:
        return 0.0
    raw_move_bps = ((current_mid / fill_price) - 1.0) * 10_000.0
    return raw_move_bps if side == "buy" else -raw_move_bps


def _adverse_fill_move_bps(side: str, fill_price: float, current_mid: float) -> float:
    return max(-_signed_fill_move_bps(side, fill_price, current_mid), 0.0)


def _score_bucket(score: float) -> str:
    if score >= 72.0:
        return "strong_positive"
    if score >= 48.0:
        return "weak_positive"
    if score >= 25.0:
        return "slightly_negative"
    return "bad"


@dataclass(frozen=True)
class AdaptiveFeatureConfig:
    profile: str
    enabled: bool
    regime_enabled: bool
    edge_enabled: bool
    mode_selector_enabled: bool
    dynamic_quoting_enabled: bool
    risk_governor_enabled: bool
    performance_adaptation_enabled: bool
    inventory_bands_enabled: bool
    fill_quality_enabled: bool
    soft_filters_enabled: bool
    logging_enabled: bool


@dataclass
class PendingFillCheck:
    cycle_index: int
    side: str
    fill_price: float
    expected_edge_bps: float
    move_5s_bps: float | None = None
    move_15s_bps: float | None = None
    move_30s_bps: float | None = None


@dataclass(frozen=True)
class FillQualitySnapshot:
    fill_count: int = 0
    adverse_fill_ratio: float = 0.0
    toxic_fill_ratio: float = 0.0
    average_adverse_bps: float = 0.0
    expected_vs_realized_edge_bps: float = 0.0
    toxic_cluster_count: int = 0


@dataclass(frozen=True)
class MarketStateSnapshot:
    mid_price: float
    short_return_bps: float
    medium_return_bps: float
    volatility: float
    volatility_bps: float
    spread_bps: float
    liquidity_estimate_usd: float
    inventory_pct: float
    inventory_deviation_pct: float
    rolling_pnl_usd: float
    rolling_drawdown_pct: float
    recent_fill_count: int
    adverse_fill_ratio: float
    toxic_fill_ratio: float
    expected_vs_realized_edge_bps: float
    fill_rate: float
    minutes_since_last_fill: float
    price_mean: float
    mean_reversion_distance_bps: float
    range_width_bps: float
    direction_consistency: float
    sign_flip_ratio: float
    quote_pressure_score: float
    source_health_score: float


@dataclass(frozen=True)
class AdaptiveRegimeAssessment:
    regime: str
    confidence: float
    sub_scores: dict[str, float]
    trend_bias: float = 0.0


@dataclass(frozen=True)
class AdaptiveEdgeAssessment:
    total_score: float
    breakdown: dict[str, float]
    penalties: dict[str, float]
    bucket: str


@dataclass(frozen=True)
class InventoryBandState:
    zone: str
    target_inventory_pct: float
    rebalance_side: str = ""
    inventory_pressure: float = 0.0


@dataclass(frozen=True)
class PerformanceAdaptationState:
    trade_count: int
    pnl_usd: float
    drawdown_pct: float
    toxic_fill_ratio: float
    hit_rate: float
    spread_baseline_multiplier: float
    size_cap_multiplier: float
    edge_threshold_multiplier: float
    skew_strength_multiplier: float


@dataclass(frozen=True)
class RiskGovernorState:
    state: str
    size_multiplier: float
    spread_multiplier: float
    inventory_cap_multiplier: float
    quote_enabled: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModeSelection:
    mode: str
    strategy_mode: str
    quote_enabled: bool
    buy_enabled: bool
    sell_enabled: bool
    target_inventory_pct: float
    directional_bias: float
    reason: str


@dataclass(frozen=True)
class AggressivenessProfile:
    level: float
    size_multiplier: float
    spread_multiplier: float
    cooldown_multiplier: float
    skew_multiplier: float


@dataclass(frozen=True)
class AdaptiveCyclePlan:
    config: AdaptiveFeatureConfig
    snapshot: MarketStateSnapshot
    fill_quality: FillQualitySnapshot
    regime: AdaptiveRegimeAssessment
    edge: AdaptiveEdgeAssessment
    inventory_band: InventoryBandState
    performance: PerformanceAdaptationState
    risk: RiskGovernorState
    mode: ModeSelection
    aggressiveness: AggressivenessProfile


def build_adaptive_feature_config(overrides: dict[str, bool] | None = None) -> AdaptiveFeatureConfig:
    values = {
        "profile": ADAPTIVE_MM_PROFILE.upper(),
        "enabled": ADAPTIVE_MARKET_MAKER_ENABLED,
        "regime_enabled": ADAPTIVE_REGIME_ENABLED,
        "edge_enabled": ADAPTIVE_EDGE_ENABLED,
        "mode_selector_enabled": ADAPTIVE_MODE_SELECTOR_ENABLED,
        "dynamic_quoting_enabled": ADAPTIVE_DYNAMIC_QUOTING_ENABLED,
        "risk_governor_enabled": ADAPTIVE_RISK_GOVERNOR_ENABLED,
        "performance_adaptation_enabled": ADAPTIVE_PERFORMANCE_ADAPTATION_ENABLED,
        "inventory_bands_enabled": ADAPTIVE_INVENTORY_BANDS_ENABLED,
        "fill_quality_enabled": ADAPTIVE_FILL_QUALITY_ENABLED,
        "soft_filters_enabled": ADAPTIVE_SOFT_FILTERS_ENABLED,
        "logging_enabled": ADAPTIVE_LOGGING_ENABLED,
    }
    for key, value in (overrides or {}).items():
        if key in values:
            values[key] = bool(value)

    if not values["enabled"]:
        for key in list(values.keys()):
            if key not in {"profile", "enabled"}:
                values[key] = False

    return AdaptiveFeatureConfig(**values)


def _recent_fill_events(runtime, cycle_index: int, minutes: float) -> list[dict[str, float]]:
    lookback_cycles = max(int(round((max(minutes, 1.0) * 60.0) / max(runtime.cycle_seconds, 1.0))), 1)
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    events = []
    for event in list(getattr(runtime, "adaptive_fill_quality_events", [])):
        if int(event.get("cycle_index", -1)) >= floor_cycle:
            events.append(event)
    return events


def current_fill_quality_snapshot(runtime, cycle_index: int) -> FillQualitySnapshot:
    events = _recent_fill_events(runtime, cycle_index, ADAPTIVE_PERF_WINDOW_MINUTES)
    if not events:
        return FillQualitySnapshot()

    adverse_count = sum(1 for event in events if float(event.get("move_5s_bps", 0.0) or 0.0) > 0.0)
    toxic_count = sum(1 for event in events if bool(event.get("toxic", False)))
    adverse_moves = [float(event.get("move_30s_bps", 0.0) or 0.0) for event in events]
    realized_edge_deltas = [float(event.get("expected_vs_realized_edge_bps", 0.0) or 0.0) for event in events]
    recent_toxic_events = _recent_fill_events(runtime, cycle_index, 15.0)
    toxic_cluster_count = sum(1 for event in recent_toxic_events if bool(event.get("toxic", False)))
    return FillQualitySnapshot(
        fill_count=len(events),
        adverse_fill_ratio=_safe_round(adverse_count / len(events)),
        toxic_fill_ratio=_safe_round(toxic_count / len(events)),
        average_adverse_bps=_safe_round(_mean(adverse_moves)),
        expected_vs_realized_edge_bps=_safe_round(_mean(realized_edge_deltas)),
        toxic_cluster_count=toxic_cluster_count,
    )


def update_fill_quality_probes(runtime, cycle_index: int, mid: float) -> FillQualitySnapshot:
    pending: list[PendingFillCheck] = list(getattr(runtime, "adaptive_pending_fill_checks", []))
    retained: list[PendingFillCheck] = []
    for probe in pending:
        age_seconds = max((cycle_index - probe.cycle_index) * max(runtime.cycle_seconds, 1.0), 0.0)
        if probe.move_5s_bps is None and age_seconds >= 5.0:
            probe.move_5s_bps = _adverse_fill_move_bps(probe.side, probe.fill_price, mid)
        if probe.move_15s_bps is None and age_seconds >= 15.0:
            probe.move_15s_bps = _adverse_fill_move_bps(probe.side, probe.fill_price, mid)
        if probe.move_30s_bps is None and age_seconds >= 30.0:
            probe.move_30s_bps = _signed_fill_move_bps(probe.side, probe.fill_price, mid)

        if probe.move_5s_bps is not None and probe.move_15s_bps is not None and probe.move_30s_bps is not None:
            realized_edge_bps = probe.move_30s_bps
            adverse_30 = max(-realized_edge_bps, 0.0)
            toxic = adverse_30 >= 8.0 or (probe.move_15s_bps or 0.0) >= 6.0 or (probe.move_5s_bps or 0.0) >= 4.0
            runtime.adaptive_fill_quality_events.append(
                {
                    "cycle_index": probe.cycle_index,
                    "move_5s_bps": _safe_round(probe.move_5s_bps or 0.0),
                    "move_15s_bps": _safe_round(probe.move_15s_bps or 0.0),
                    "move_30s_bps": _safe_round(adverse_30),
                    "realized_edge_bps": _safe_round(realized_edge_bps),
                    "expected_edge_bps": _safe_round(probe.expected_edge_bps),
                    "expected_vs_realized_edge_bps": _safe_round((probe.expected_edge_bps - realized_edge_bps)),
                    "toxic": toxic,
                }
            )
            continue

        retained.append(probe)

    runtime.adaptive_pending_fill_checks.clear()
    runtime.adaptive_pending_fill_checks.extend(retained)
    snapshot = current_fill_quality_snapshot(runtime, cycle_index)
    runtime.current_toxic_fill_ratio = snapshot.toxic_fill_ratio
    runtime.current_adverse_fill_ratio = snapshot.adverse_fill_ratio
    runtime.current_expected_vs_realized_edge_bps = snapshot.expected_vs_realized_edge_bps
    return snapshot


def register_fill_quality_probe(runtime, cycle_index: int, fill, expected_edge_bps: float) -> None:
    if not fill or not getattr(fill, "filled", False):
        return
    runtime.adaptive_pending_fill_checks.append(
        PendingFillCheck(
            cycle_index=cycle_index,
            side=str(getattr(fill, "side", "")).lower(),
            fill_price=float(getattr(fill, "price", 0.0) or 0.0),
            expected_edge_bps=float(expected_edge_bps or 0.0),
        )
    )


def build_market_snapshot(
    runtime,
    *,
    cycle_index: int,
    prices: list[float],
    mid: float,
    spread_bps: float,
    inventory_pct: float,
    rolling_pnl_usd: float,
    base_trade_size_usd: float,
    fill_quality: FillQualitySnapshot,
) -> MarketStateSnapshot:
    lookback = max(int(round((ADAPTIVE_PERF_WINDOW_MINUTES * 60.0) / max(runtime.cycle_seconds, 1.0))), 4)
    recent_equities = list(runtime.recent_equities)[-lookback:]
    rolling_drawdown_pct = _rolling_drawdown_pct(recent_equities)
    price_mean = _price_mean(prices, min(max(lookback, 12), max(len(prices), 1)))
    mean_reversion_distance_bps = 0.0
    if price_mean > 0 and mid > 0:
        mean_reversion_distance_bps = ((mid / price_mean) - 1.0) * 10_000.0
    range_width_bps = _range_width_bps(prices, min(max(lookback, 12), max(len(prices), 1)))
    direction_consistency = _direction_consistency(prices, min(max(lookback, 12), max(len(prices) - 1, 1)))
    sign_flip_ratio = _sign_flip_ratio(prices, min(max(lookback, 12), max(len(prices) - 1, 1)))
    volatility_bps = max(getattr(getattr(runtime, "current_regime_assessment", None), "volatility_score", 0.0), 0.0)
    raw_volatility = max(float(getattr(runtime, "current_fill_quality_score", 1.0) or 0.0), 0.0)
    liquidity_estimate_usd = max(
        base_trade_size_usd * max(18.0 - min(spread_bps, 15.0), 4.0),
        base_trade_size_usd * max(12.0 - min(raw_volatility, 8.0), 4.0),
        ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD * 2.0,
    )
    short_return_bps = _return_bps(prices, 3)
    medium_return_bps = _return_bps(prices, 12)
    quote_pressure_score = _clamp(
        (fill_quality.toxic_fill_ratio * 40.0)
        + (fill_quality.adverse_fill_ratio * 30.0)
        + max(spread_bps - ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS, 0.0) * 1.5,
        0.0,
        100.0,
    )
    source_health_score = 0.0 if getattr(runtime, "consecutive_invalid_price_cycles", 0) >= 3 else 1.0
    return MarketStateSnapshot(
        mid_price=_safe_round(mid),
        short_return_bps=_safe_round(short_return_bps),
        medium_return_bps=_safe_round(medium_return_bps),
        volatility=_safe_round(max(volatility_bps, 0.0) / 10_000.0),
        volatility_bps=_safe_round(volatility_bps),
        spread_bps=_safe_round(spread_bps),
        liquidity_estimate_usd=_safe_round(liquidity_estimate_usd),
        inventory_pct=_safe_round(inventory_pct),
        inventory_deviation_pct=_safe_round((inventory_pct - 0.50) * 100.0),
        rolling_pnl_usd=_safe_round(rolling_pnl_usd),
        rolling_drawdown_pct=_safe_round(rolling_drawdown_pct),
        recent_fill_count=fill_quality.fill_count,
        adverse_fill_ratio=_safe_round(fill_quality.adverse_fill_ratio),
        toxic_fill_ratio=_safe_round(fill_quality.toxic_fill_ratio),
        expected_vs_realized_edge_bps=_safe_round(fill_quality.expected_vs_realized_edge_bps),
        fill_rate=_safe_round(getattr(runtime, "current_fill_rate", 0.0)),
        minutes_since_last_fill=_safe_round(getattr(runtime, "current_minutes_since_last_fill", 0.0)),
        price_mean=_safe_round(price_mean),
        mean_reversion_distance_bps=_safe_round(mean_reversion_distance_bps),
        range_width_bps=_safe_round(range_width_bps),
        direction_consistency=_safe_round(direction_consistency),
        sign_flip_ratio=_safe_round(sign_flip_ratio),
        quote_pressure_score=_safe_round(quote_pressure_score),
        source_health_score=_safe_round(source_health_score),
    )


def classify_regime(snapshot: MarketStateSnapshot) -> AdaptiveRegimeAssessment:
    breakout_bps = max(ADAPTIVE_REGIME_BREAKOUT_BPS, 8.0)
    high_vol_bps = max(HIGH_VOL_THRESHOLD * 10_000.0, 1.0)
    vol_ratio = snapshot.volatility_bps / high_vol_bps
    abs_short = abs(snapshot.short_return_bps)
    abs_medium = abs(snapshot.medium_return_bps)
    trend_consistency = snapshot.direction_consistency
    noise = snapshot.sign_flip_ratio
    toxicity = snapshot.toxic_fill_ratio
    adverse = snapshot.adverse_fill_ratio
    mean_distance = abs(snapshot.mean_reversion_distance_bps)
    spread = snapshot.spread_bps
    liquidity = snapshot.liquidity_estimate_usd

    trend_up_base = (
        max(snapshot.medium_return_bps, 0.0) / breakout_bps * 38.0
        + max(snapshot.short_return_bps, 0.0) / breakout_bps * 22.0
        + trend_consistency * 32.0
        - noise * 18.0
        - toxicity * 15.0
    )
    trend_down_base = (
        max(-snapshot.medium_return_bps, 0.0) / breakout_bps * 38.0
        + max(-snapshot.short_return_bps, 0.0) / breakout_bps * 22.0
        + trend_consistency * 32.0
        - noise * 18.0
        - toxicity * 15.0
    )
    range_clean = (
        max(1.0 - (abs_medium / max(breakout_bps * 1.25, 1.0)), 0.0) * 36.0
        + max(1.0 - abs(vol_ratio - 0.75), 0.0) * 18.0
        + min(mean_distance / max(breakout_bps, 1.0), 1.5) * 16.0
        + max(1.0 - toxicity, 0.0) * 16.0
        + max(min(noise * 1.15, 1.0), 0.0) * 14.0
    )
    range_dirty = (
        max(range_clean * 0.58, 0.0)
        + noise * 20.0
        + toxicity * 18.0
        + adverse * 12.0
        + max(vol_ratio - 0.75, 0.0) * 18.0
    )
    breakout = (
        abs_short / breakout_bps * 40.0
        + abs_medium / breakout_bps * 18.0
        + trend_consistency * 20.0
        + max(vol_ratio - 0.9, 0.0) * 18.0
        - toxicity * 10.0
    )
    event = (
        max(vol_ratio - ADAPTIVE_REGIME_EVENT_VOL_MULTIPLIER, 0.0) * 42.0
        + abs_short / breakout_bps * 16.0
        + toxicity * 24.0
        + adverse * 20.0
        + snapshot.rolling_drawdown_pct * 260.0
    )
    illiquid = (
        max(spread - ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS, 0.0) * 3.0
        + max((ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD - liquidity) / max(ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD, 1.0), 0.0) * 80.0
        + snapshot.quote_pressure_score * 0.30
    )

    sub_scores = {
        "trend_up_low_vol": _clamp(trend_up_base + max(1.0 - vol_ratio, 0.0) * 12.0, 0.0, 100.0),
        "trend_up_high_vol": _clamp(trend_up_base + max(vol_ratio - 0.9, 0.0) * 16.0, 0.0, 100.0),
        "trend_down_low_vol": _clamp(trend_down_base + max(1.0 - vol_ratio, 0.0) * 12.0, 0.0, 100.0),
        "trend_down_high_vol": _clamp(trend_down_base + max(vol_ratio - 0.9, 0.0) * 16.0, 0.0, 100.0),
        "range_clean": _clamp(range_clean, 0.0, 100.0),
        "range_dirty": _clamp(range_dirty, 0.0, 100.0),
        "breakout": _clamp(breakout, 0.0, 100.0),
        "event": _clamp(event, 0.0, 100.0),
        "illiquid": _clamp(illiquid, 0.0, 100.0),
    }
    ranked = sorted(sub_scores.items(), key=lambda item: (-item[1], item[0]))
    best_regime, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = _clamp(best_score - (second_score * 0.35), 0.0, 100.0)
    trend_bias = 0.0
    if best_regime.startswith("trend_up"):
        trend_bias = _clamp(best_score / 100.0, 0.0, 1.0)
    elif best_regime.startswith("trend_down"):
        trend_bias = -_clamp(best_score / 100.0, 0.0, 1.0)
    elif best_regime == "breakout":
        trend_bias = _clamp(snapshot.short_return_bps / max(breakout_bps, 1.0), -1.0, 1.0)
    return AdaptiveRegimeAssessment(
        regime=best_regime,
        confidence=_safe_round(confidence),
        sub_scores={key: _safe_round(value) for key, value in sub_scores.items()},
        trend_bias=_safe_round(trend_bias),
    )


def score_edge(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    *,
    cooldown_active: bool,
) -> AdaptiveEdgeAssessment:
    high_vol_bps = max(HIGH_VOL_THRESHOLD * 10_000.0, 1.0)
    vol_ratio = snapshot.volatility_bps / high_vol_bps
    clean_range_score = regime.sub_scores.get("range_clean", 0.0)
    trend_score = max(
        regime.sub_scores.get("trend_up_low_vol", 0.0),
        regime.sub_scores.get("trend_up_high_vol", 0.0),
        regime.sub_scores.get("trend_down_low_vol", 0.0),
        regime.sub_scores.get("trend_down_high_vol", 0.0),
        regime.sub_scores.get("breakout", 0.0),
    )
    market_quality = _clamp(
        82.0
        - max(vol_ratio - 1.0, 0.0) * 20.0
        - snapshot.toxic_fill_ratio * 24.0
        - snapshot.adverse_fill_ratio * 18.0
        - max(snapshot.spread_bps - ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS, 0.0) * 1.2,
        0.0,
        100.0,
    )
    direction_clarity = _clamp(
        max(trend_score, regime.confidence * 0.82)
        - snapshot.sign_flip_ratio * 18.0,
        0.0,
        100.0,
    )
    mean_reversion_potential = _clamp(
        clean_range_score * 0.60
        + min(abs(snapshot.mean_reversion_distance_bps) / max(ADAPTIVE_REGIME_BREAKOUT_BPS, 1.0), 2.0) * 18.0
        - regime.sub_scores.get("breakout", 0.0) * 0.25
        - regime.sub_scores.get("event", 0.0) * 0.20,
        0.0,
        100.0,
    )
    spread_capture_potential = _clamp(
        min(snapshot.spread_bps / max(snapshot.volatility_bps * 0.35, 3.0), 2.0) * 34.0
        + snapshot.fill_rate * 34.0
        + max(1.0 - snapshot.toxic_fill_ratio, 0.0) * 18.0,
        0.0,
        100.0,
    )
    execution_safety = _clamp(
        86.0
        - snapshot.rolling_drawdown_pct * 320.0
        - snapshot.toxic_fill_ratio * 30.0
        - snapshot.adverse_fill_ratio * 18.0
        - max(1.0 - snapshot.source_health_score, 0.0) * 100.0,
        0.0,
        100.0,
    )
    liquidity_stability = _clamp(
        min(snapshot.liquidity_estimate_usd / max(ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD * 2.0, 1.0), 2.0) * 40.0
        + max(1.0 - max(snapshot.spread_bps - 8.0, 0.0) / max(ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS, 1.0), 0.0) * 30.0
        + max(1.0 - snapshot.adverse_fill_ratio, 0.0) * 18.0,
        0.0,
        100.0,
    )

    penalties = {
        "cooldown_penalty": 8.0 if cooldown_active else 0.0,
        "volatility_penalty": _clamp(max(vol_ratio - 1.0, 0.0) * 12.0, 0.0, 18.0),
        "toxic_penalty": _clamp(snapshot.toxic_fill_ratio * 18.0, 0.0, 18.0),
        "illiquidity_penalty": _clamp(max(regime.sub_scores.get("illiquid", 0.0) - 50.0, 0.0) * 0.20, 0.0, 16.0),
    }
    total_score = (
        market_quality * 0.22
        + direction_clarity * 0.18
        + mean_reversion_potential * 0.15
        + spread_capture_potential * 0.20
        + execution_safety * 0.15
        + liquidity_stability * 0.10
        - sum(penalties.values())
    )
    total_score = _clamp(total_score, 0.0, 100.0)
    breakdown = {
        "market_quality": _safe_round(market_quality),
        "direction_clarity": _safe_round(direction_clarity),
        "mean_reversion_potential": _safe_round(mean_reversion_potential),
        "spread_capture_potential": _safe_round(spread_capture_potential),
        "execution_safety": _safe_round(execution_safety),
        "liquidity_stability": _safe_round(liquidity_stability),
    }
    return AdaptiveEdgeAssessment(
        total_score=_safe_round(total_score),
        breakdown=breakdown,
        penalties={key: _safe_round(value) for key, value in penalties.items()},
        bucket=_score_bucket(total_score),
    )


def classify_inventory_band(snapshot: MarketStateSnapshot) -> InventoryBandState:
    inventory_pct = snapshot.inventory_pct
    pressure = abs(inventory_pct - 0.50) / 0.15
    if ADAPTIVE_INVENTORY_NEUTRAL_MIN <= inventory_pct <= ADAPTIVE_INVENTORY_NEUTRAL_MAX:
        return InventoryBandState("neutral", 0.50, inventory_pressure=_safe_round(pressure))
    if ADAPTIVE_INVENTORY_SOFT_MIN <= inventory_pct <= ADAPTIVE_INVENTORY_SOFT_MAX:
        target = 0.48 if inventory_pct > 0.50 else 0.52
        return InventoryBandState(
            "soft_skew",
            target,
            rebalance_side="sell" if inventory_pct > 0.50 else "buy",
            inventory_pressure=_safe_round(pressure),
        )
    if ADAPTIVE_INVENTORY_STRONG_MIN <= inventory_pct <= ADAPTIVE_INVENTORY_STRONG_MAX:
        target = 0.46 if inventory_pct > 0.50 else 0.54
        return InventoryBandState(
            "strong_skew",
            target,
            rebalance_side="sell" if inventory_pct > 0.50 else "buy",
            inventory_pressure=_safe_round(pressure),
        )
    if ADAPTIVE_INVENTORY_HARD_MIN <= inventory_pct <= ADAPTIVE_INVENTORY_HARD_MAX:
        target = 0.44 if inventory_pct > 0.50 else 0.56
        return InventoryBandState(
            "hard_skew",
            target,
            rebalance_side="sell" if inventory_pct > 0.50 else "buy",
            inventory_pressure=_safe_round(max(pressure, 1.10)),
        )
    target = 0.42 if inventory_pct > 0.50 else 0.58
    return InventoryBandState(
        "outside",
        target,
        rebalance_side="sell" if inventory_pct > 0.50 else "buy",
        inventory_pressure=_safe_round(max(pressure, 1.35)),
    )


def adapt_performance(runtime, cycle_index: int, fill_quality: FillQualitySnapshot) -> PerformanceAdaptationState:
    lookback_cycles = max(int(round((ADAPTIVE_PERF_WINDOW_MINUTES * 60.0) / max(runtime.cycle_seconds, 1.0))), 1)
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    recent_trades = [trade for trade in runtime.performance.trade_history if trade.cycle_index >= floor_cycle]
    closed_trades = [trade for trade in recent_trades if trade.side == "sell"]
    realized_pnl = sum(float(getattr(trade, "realized_pnl", 0.0) or 0.0) for trade in closed_trades)
    hit_rate = (
        sum(1 for trade in closed_trades if float(getattr(trade, "realized_pnl", 0.0) or 0.0) > 0.0)
        / len(closed_trades)
        if closed_trades
        else 0.0
    )
    recent_equities = list(runtime.recent_equities)[-lookback_cycles:]
    drawdown_pct = _rolling_drawdown_pct(recent_equities)

    spread_baseline_multiplier = 1.0
    size_cap_multiplier = 1.0
    edge_threshold_multiplier = 1.0
    skew_strength_multiplier = 1.0

    if drawdown_pct >= ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT or fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO:
        spread_baseline_multiplier *= 1.08
        size_cap_multiplier *= 0.84
        edge_threshold_multiplier *= 1.10
        skew_strength_multiplier *= 0.96
    elif len(recent_trades) < max(int(round(ADAPTIVE_PERF_WINDOW_MINUTES / 12.0)), 2) and fill_quality.toxic_fill_ratio <= 0.20:
        spread_baseline_multiplier *= 0.96
        edge_threshold_multiplier *= 0.96
        size_cap_multiplier *= 1.03
        skew_strength_multiplier *= 1.02

    if realized_pnl > 0 and hit_rate >= 0.55 and fill_quality.toxic_fill_ratio <= 0.20:
        size_cap_multiplier *= 1.04
        spread_baseline_multiplier *= 0.98
        skew_strength_multiplier *= 1.04
    elif realized_pnl < 0 and len(closed_trades) >= 3:
        size_cap_multiplier *= 0.92
        edge_threshold_multiplier *= 1.06
        skew_strength_multiplier *= 0.97

    return PerformanceAdaptationState(
        trade_count=len(recent_trades),
        pnl_usd=_safe_round(realized_pnl),
        drawdown_pct=_safe_round(drawdown_pct),
        toxic_fill_ratio=_safe_round(fill_quality.toxic_fill_ratio),
        hit_rate=_safe_round(hit_rate),
        spread_baseline_multiplier=_safe_round(
            _clamp(
                spread_baseline_multiplier,
                ADAPTIVE_PERF_SPREAD_LOWER_BOUND,
                ADAPTIVE_PERF_SPREAD_UPPER_BOUND,
            )
        ),
        size_cap_multiplier=_safe_round(
            _clamp(
                size_cap_multiplier,
                ADAPTIVE_PERF_SIZE_CAP_LOWER_BOUND,
                ADAPTIVE_PERF_SIZE_CAP_UPPER_BOUND,
            )
        ),
        edge_threshold_multiplier=_safe_round(
            _clamp(
                edge_threshold_multiplier,
                ADAPTIVE_PERF_EDGE_THRESHOLD_LOWER_BOUND,
                ADAPTIVE_PERF_EDGE_THRESHOLD_UPPER_BOUND,
            )
        ),
        skew_strength_multiplier=_safe_round(
            _clamp(
                skew_strength_multiplier,
                ADAPTIVE_PERF_SKEW_LOWER_BOUND,
                ADAPTIVE_PERF_SKEW_UPPER_BOUND,
            )
        ),
    )


def govern_risk(
    runtime,
    snapshot: MarketStateSnapshot,
    fill_quality: FillQualitySnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
) -> RiskGovernorState:
    reasons: list[str] = []
    state = "normal"
    size_multiplier = 1.0
    spread_multiplier = 1.0
    inventory_cap_multiplier = 1.0
    quote_enabled = True

    recent_equities = list(getattr(runtime, "recent_equities", []))[-12:]
    drawdown_acceleration = _drawdown_acceleration_pct(recent_equities)
    invalid_price_cycles = int(getattr(runtime, "consecutive_invalid_price_cycles", 0) or 0)
    extreme_event_risk = regime.sub_scores.get("event", 0.0) >= ADAPTIVE_REGIME_EXTREME_EVENT_SCORE
    severe_illiquidity = (
        regime.sub_scores.get("illiquid", 0.0) >= ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE
        or snapshot.liquidity_estimate_usd <= ADAPTIVE_RISK_KILL_LIQUIDITY_USD
    )
    weak_liquidity = (
        snapshot.liquidity_estimate_usd <= ADAPTIVE_RISK_SOFT_LIQUIDITY_USD
        or regime.sub_scores.get("illiquid", 0.0) >= 60.0
    )

    if invalid_price_cycles >= ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES:
        reasons.append("invalid_market_data")
    if severe_illiquidity:
        reasons.append("severe_liquidity_collapse")
    if snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_KILL_DRAWDOWN_PCT:
        reasons.append("extreme_drawdown")
    if (
        fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_KILL_TOXIC_FILL_RATIO
        and fill_quality.toxic_cluster_count >= ADAPTIVE_RISK_KILL_TOXIC_CLUSTER_COUNT
    ):
        reasons.append("extreme_toxic_fill_cluster")

    if reasons:
        state = "kill_switch"
        size_multiplier = 0.0
        spread_multiplier = 1.35
        inventory_cap_multiplier = 0.60
        quote_enabled = False
    elif (
        fill_quality.toxic_cluster_count >= ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT
        or drawdown_acceleration >= ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT
        or invalid_price_cycles > 0
        or snapshot.source_health_score < 1.0
        or snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_HARD_DRAWDOWN_PCT
        or fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_HARD_TOXIC_FILL_RATIO
        or (extreme_event_risk and edge.total_score < ADAPTIVE_NORMAL_MODE_MIN_EDGE)
    ):
        state = "hard_brake"
        size_multiplier = 0.58
        spread_multiplier = 1.15
        inventory_cap_multiplier = 0.80
        reasons.append("risk_hard_brake")
        if drawdown_acceleration >= ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT:
            reasons.append("drawdown_acceleration")
        if fill_quality.toxic_cluster_count >= ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT:
            reasons.append("toxic_fill_cluster")
        if invalid_price_cycles > 0 or snapshot.source_health_score < 1.0:
            reasons.append("feed_rpc_anomaly")
    elif (
        snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT
        or fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO
        or weak_liquidity
        or fill_quality.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD
    ):
        state = "soft_brake"
        size_multiplier = 0.82
        spread_multiplier = 1.05
        inventory_cap_multiplier = 0.92
        reasons.append("risk_soft_brake")
        if weak_liquidity:
            reasons.append("liquidity_weak")
        if fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO:
            reasons.append("toxic_fill_ratio_elevated")
        if fill_quality.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD:
            reasons.append("adverse_selection_elevated")

    return RiskGovernorState(
        state=state,
        size_multiplier=_safe_round(size_multiplier),
        spread_multiplier=_safe_round(spread_multiplier),
        inventory_cap_multiplier=_safe_round(inventory_cap_multiplier),
        quote_enabled=quote_enabled,
        reasons=reasons,
    )


def select_mode(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
    inventory_band: InventoryBandState,
    performance: PerformanceAdaptationState,
    risk: RiskGovernorState,
    intelligence,
) -> ModeSelection:
    aggressive_edge = ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE * performance.edge_threshold_multiplier
    normal_edge = ADAPTIVE_NORMAL_MODE_MIN_EDGE * performance.edge_threshold_multiplier
    defensive_edge = ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE * performance.edge_threshold_multiplier
    standby_edge = ADAPTIVE_EDGE_STANDBY_SCORE * performance.edge_threshold_multiplier
    medium_confidence = ADAPTIVE_REGIME_MEDIUM_CONFIDENCE
    trend_confidence = ADAPTIVE_REGIME_TREND_CONFIDENCE
    extreme_event_risk = regime.sub_scores.get("event", 0.0) >= ADAPTIVE_REGIME_EXTREME_EVENT_SCORE
    severe_illiquidity = regime.sub_scores.get("illiquid", 0.0) >= ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE

    if risk.state == "kill_switch":
        return ModeSelection("standby", "NO_TRADE", False, False, False, inventory_band.target_inventory_pct, 0.0, "risk_kill_switch")

    if inventory_band.zone == "outside":
        if inventory_band.rebalance_side == "sell":
            return ModeSelection("rebalance_only", "OVERWEIGHT_EXIT", True, False, True, inventory_band.target_inventory_pct, -0.55, "inventory_outside_sell_rebalance")
        return ModeSelection("rebalance_only", "RANGE_MAKER", True, True, False, inventory_band.target_inventory_pct, 0.55, "inventory_outside_buy_rebalance")

    if (extreme_event_risk or severe_illiquidity) and edge.total_score <= normal_edge:
        return ModeSelection("standby", "NO_TRADE", False, False, False, inventory_band.target_inventory_pct, 0.0, "extreme_regime_standby")

    if edge.total_score <= standby_edge and risk.state == "hard_brake":
        return ModeSelection("standby", "NO_TRADE", False, False, False, inventory_band.target_inventory_pct, 0.0, "edge_and_risk_standby")

    if (
        regime.regime.startswith("trend_up")
        and regime.confidence >= trend_confidence
        and edge.total_score >= aggressive_edge
    ):
        return ModeSelection(
            "trend_assist",
            "TREND_UP",
            True,
            bool(getattr(intelligence, "buy_enabled", True)),
            bool(getattr(intelligence, "sell_enabled", True)),
            max(inventory_band.target_inventory_pct, 0.56),
            _clamp(regime.trend_bias + 0.18, -1.0, 1.0),
            "uptrend_with_edge",
        )

    if regime.regime.startswith("trend_down") and regime.confidence >= medium_confidence:
        return ModeSelection(
            "skewed_mm" if inventory_band.zone != "neutral" and edge.total_score >= defensive_edge else "defensive_mm",
            "RANGE_MAKER",
            True,
            edge.total_score >= max(ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE, standby_edge),
            True,
            min(inventory_band.target_inventory_pct, 0.44),
            _clamp(regime.trend_bias - 0.10, -1.0, 1.0),
            "downtrend_inventory_preservation",
        )

    if (
        regime.regime in {"event", "illiquid"}
        or risk.state == "hard_brake"
        or edge.total_score < defensive_edge
        or snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_DEFENSIVE_THRESHOLD
    ):
        return ModeSelection(
            "defensive_mm",
            "RANGE_MAKER",
            risk.quote_enabled and edge.total_score >= ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE,
            bool(getattr(intelligence, "buy_enabled", True)) and inventory_band.rebalance_side != "sell",
            bool(getattr(intelligence, "sell_enabled", True)),
            inventory_band.target_inventory_pct,
            regime.trend_bias * 0.45,
            "risk_defensive_regime" if risk.state == "hard_brake" else "low_edge_defensive",
        )

    if inventory_band.zone in {"soft_skew", "strong_skew", "hard_skew"} and edge.total_score >= max(normal_edge, standby_edge):
        return ModeSelection(
            "skewed_mm",
            "RANGE_MAKER",
            True,
            bool(getattr(intelligence, "buy_enabled", True)) and inventory_band.rebalance_side != "sell",
            bool(getattr(intelligence, "sell_enabled", True)) and inventory_band.rebalance_side != "buy",
            inventory_band.target_inventory_pct,
            _clamp((inventory_band.target_inventory_pct - snapshot.inventory_pct) * 4.0, -1.0, 1.0),
            "inventory_skew",
        )

    return ModeSelection(
        "passive_mm",
        "RANGE_MAKER",
        risk.quote_enabled and edge.total_score >= max(ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE, standby_edge),
        bool(getattr(intelligence, "buy_enabled", True)),
        bool(getattr(intelligence, "sell_enabled", True)),
        _clamp((RANGE_TARGET_INVENTORY_MIN + RANGE_TARGET_INVENTORY_MAX) / 2.0, 0.0, 1.0),
        regime.trend_bias * 0.35,
        "balanced_passive_market_making",
    )


def build_aggressiveness(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
    inventory_band: InventoryBandState,
    performance: PerformanceAdaptationState,
    risk: RiskGovernorState,
    mode: ModeSelection,
) -> AggressivenessProfile:
    raw_level = edge.total_score
    if risk.state == "soft_brake":
        raw_level *= 0.88
    elif risk.state == "hard_brake":
        raw_level *= 0.70

    aggressive_edge = max(ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE, ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE + 1.0)
    defensive_edge = min(ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE, aggressive_edge - 1.0)
    edge_range = max(aggressive_edge - defensive_edge, 1.0)
    score_ratio = _clamp((raw_level - defensive_edge) / edge_range, 0.0, 1.0)

    size_multiplier = 0.72 + (score_ratio * 0.26)
    spread_multiplier = 1.08 - (score_ratio * 0.10)
    cooldown_multiplier = 1.02 - (score_ratio * 0.16)

    if raw_level >= ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE:
        size_multiplier *= ADAPTIVE_AGGRESSIVE_SIZE_MULTIPLIER
    elif raw_level >= ADAPTIVE_NORMAL_MODE_MIN_EDGE:
        size_multiplier *= ADAPTIVE_NORMAL_SIZE_MULTIPLIER
    else:
        size_multiplier *= ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER

    mode_spread_multiplier = ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER
    mode_size_multiplier = ADAPTIVE_NORMAL_SIZE_MULTIPLIER
    if mode.mode == "trend_assist":
        mode_spread_multiplier = ADAPTIVE_TREND_ASSIST_SPREAD_MULTIPLIER
        mode_size_multiplier = ADAPTIVE_TREND_ASSIST_SIZE_MULTIPLIER
        cooldown_multiplier *= 0.84
    elif mode.mode == "skewed_mm":
        mode_spread_multiplier = ADAPTIVE_SKEWED_MM_SPREAD_MULTIPLIER
        mode_size_multiplier = ADAPTIVE_NORMAL_SIZE_MULTIPLIER
        cooldown_multiplier *= 0.90
    elif mode.mode == "defensive_mm":
        mode_spread_multiplier = ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER
        mode_size_multiplier = ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER
        cooldown_multiplier *= 1.06
    elif mode.mode == "rebalance_only":
        mode_spread_multiplier = ADAPTIVE_REBALANCE_ONLY_SPREAD_MULTIPLIER
        mode_size_multiplier = ADAPTIVE_REBALANCE_SIZE_MULTIPLIER
        cooldown_multiplier *= 0.94
    elif mode.mode == "standby":
        mode_spread_multiplier = max(ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER, 1.20)
        mode_size_multiplier = 0.0
        cooldown_multiplier *= 1.15

    size_multiplier *= mode_size_multiplier
    spread_multiplier *= mode_spread_multiplier

    skew_multiplier = 1.0 + (inventory_band.inventory_pressure * 0.18)
    if abs(regime.trend_bias) >= 0.55:
        skew_multiplier *= ADAPTIVE_STRONG_TREND_SKEW_STRENGTH
    elif abs(regime.trend_bias) >= 0.18:
        skew_multiplier *= ADAPTIVE_MILD_TREND_SKEW_STRENGTH
    if mode.mode == "rebalance_only":
        skew_multiplier *= ADAPTIVE_REBALANCE_SKEW_STRENGTH
    elif inventory_band.zone == "hard_skew":
        skew_multiplier *= 1.10

    size_multiplier *= performance.size_cap_multiplier
    spread_multiplier *= performance.spread_baseline_multiplier
    skew_multiplier *= performance.skew_strength_multiplier
    spread_multiplier *= risk.spread_multiplier
    size_multiplier *= risk.size_multiplier

    if mode.mode == "standby":
        size_multiplier = 0.0
        spread_multiplier = max(spread_multiplier, 1.25)
        cooldown_multiplier = max(cooldown_multiplier, 1.25)

    if snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_WIDEN_THRESHOLD:
        spread_multiplier *= ADAPTIVE_ADVERSE_WIDEN_MULTIPLIER
    if snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD:
        size_multiplier *= ADAPTIVE_ADVERSE_SIZE_REDUCE_MULTIPLIER
        cooldown_multiplier *= 1.04

    if snapshot.toxic_fill_ratio > 0.0:
        spread_multiplier *= 1.0 + min(snapshot.toxic_fill_ratio * 0.14, 0.12)
        size_multiplier *= max(1.0 - min(snapshot.toxic_fill_ratio * 0.16, 0.16), 0.46)

    return AggressivenessProfile(
        level=_safe_round(_clamp(raw_level, 0.0, 100.0)),
        size_multiplier=_safe_round(_clamp(size_multiplier, 0.0, 1.60)),
        spread_multiplier=_safe_round(_clamp(spread_multiplier, 0.78, 1.90)),
        cooldown_multiplier=_safe_round(_clamp(cooldown_multiplier, 0.50, 2.00)),
        skew_multiplier=_safe_round(_clamp(skew_multiplier, 0.80, 2.40)),
    )


def build_cycle_plan(
    runtime,
    *,
    cycle_index: int,
    intelligence,
    inventory_profile,
    prices: list[float],
    mid: float,
    spread_bps: float,
    inventory_usd: float,
    equity_usd: float,
    pnl_usd: float,
    base_trade_size_usd: float,
    cooldown_active: bool,
) -> AdaptiveCyclePlan | None:
    config = getattr(runtime, "adaptive_config", None)
    if config is None or not config.enabled:
        return None

    fill_quality = (
        update_fill_quality_probes(runtime, cycle_index, mid)
        if config.fill_quality_enabled
        else current_fill_quality_snapshot(runtime, cycle_index)
    )
    inventory_pct = _safe_div(inventory_usd, equity_usd, 0.0)
    snapshot = build_market_snapshot(
        runtime,
        cycle_index=cycle_index,
        prices=prices,
        mid=mid,
        spread_bps=spread_bps,
        inventory_pct=inventory_pct,
        rolling_pnl_usd=pnl_usd,
        base_trade_size_usd=base_trade_size_usd,
        fill_quality=fill_quality,
    )
    regime = (
        classify_regime(snapshot)
        if config.regime_enabled
        else AdaptiveRegimeAssessment("range_clean", 50.0, {"range_clean": 50.0}, trend_bias=0.0)
    )
    edge = (
        score_edge(snapshot, regime, cooldown_active=cooldown_active)
        if config.edge_enabled
        else AdaptiveEdgeAssessment(50.0, {}, {}, "weak_positive")
    )
    inventory_band = classify_inventory_band(snapshot) if config.inventory_bands_enabled else InventoryBandState("neutral", 0.50)
    performance = (
        adapt_performance(runtime, cycle_index, fill_quality)
        if config.performance_adaptation_enabled
        else PerformanceAdaptationState(0, 0.0, snapshot.rolling_drawdown_pct, fill_quality.toxic_fill_ratio, 0.0, 1.0, 1.0, 1.0, 1.0)
    )
    risk = (
        govern_risk(runtime, snapshot, fill_quality, regime, edge)
        if config.risk_governor_enabled
        else RiskGovernorState("normal", 1.0, 1.0, 1.0, True, [])
    )
    mode = (
        select_mode(snapshot, regime, edge, inventory_band, performance, risk, intelligence)
        if config.mode_selector_enabled
        else ModeSelection("passive_mm", "RANGE_MAKER", True, True, True, 0.50, 0.0, "disabled")
    )
    aggressiveness = (
        build_aggressiveness(snapshot, regime, edge, inventory_band, performance, risk, mode)
        if config.dynamic_quoting_enabled
        else AggressivenessProfile(edge.total_score, 1.0, 1.0, 1.0, 1.0)
    )
    return AdaptiveCyclePlan(
        config=config,
        snapshot=snapshot,
        fill_quality=fill_quality,
        regime=regime,
        edge=edge,
        inventory_band=inventory_band,
        performance=performance,
        risk=risk,
        mode=mode,
        aggressiveness=aggressiveness,
    )


def soften_edge_assessment(
    edge_assessment: EdgeAssessment,
    cycle_plan: AdaptiveCyclePlan | None,
) -> tuple[EdgeAssessment, dict[str, object]]:
    if cycle_plan is None or not cycle_plan.config.soft_filters_enabled:
        return edge_assessment, {}
    if edge_assessment.edge_pass:
        return edge_assessment, {}

    soft_reasons = {
        "expected_edge_bad",
        "expected_edge_below_min",
        "edge_score_too_low",
    }
    if edge_assessment.edge_reject_reason not in soft_reasons:
        return edge_assessment, {}

    softened_size = _clamp(
        min(edge_assessment.size_multiplier, max(cycle_plan.aggressiveness.size_multiplier * 0.85, 0.30)),
        0.25,
        0.90,
    )
    softened_spread = _clamp(
        max(edge_assessment.spread_multiplier, cycle_plan.aggressiveness.spread_multiplier, 1.10),
        1.0,
        1.70,
    )
    softened_cooldown = _clamp(
        max(edge_assessment.cooldown_multiplier, cycle_plan.aggressiveness.cooldown_multiplier, 1.05),
        0.75,
        1.80,
    )
    softened_edge = replace(
        edge_assessment,
        edge_score=max(edge_assessment.edge_score, cycle_plan.edge.total_score),
        edge_pass=True,
        edge_reject_reason="",
        edge_bucket=_score_bucket(cycle_plan.edge.total_score),
        size_multiplier=_safe_round(softened_size),
        spread_multiplier=_safe_round(softened_spread),
        cooldown_multiplier=_safe_round(softened_cooldown),
        aggressive_enabled=False,
    )
    return softened_edge, {
        "adaptive_edge_softened": True,
        "adaptive_original_edge_reject_reason": edge_assessment.edge_reject_reason,
        "adaptive_edge_soft_bucket": softened_edge.edge_bucket,
        "adaptive_edge_soft_size_multiplier": softened_edge.size_multiplier,
        "adaptive_edge_soft_spread_multiplier": softened_edge.spread_multiplier,
    }


def apply_intelligence_overrides(runtime, intelligence, cycle_plan: AdaptiveCyclePlan | None) -> None:
    if cycle_plan is None:
        return

    runtime.current_adaptive_regime = cycle_plan.regime.regime
    runtime.current_adaptive_regime_confidence = cycle_plan.regime.confidence
    runtime.current_adaptive_edge_score = cycle_plan.edge.total_score
    runtime.current_adaptive_mode = cycle_plan.mode.mode
    runtime.current_aggressiveness_score = cycle_plan.aggressiveness.level
    runtime.current_risk_governor_state = cycle_plan.risk.state
    runtime.current_risk_governor_reasons = "|".join(cycle_plan.risk.reasons)
    runtime.current_toxic_fill_ratio = cycle_plan.fill_quality.toxic_fill_ratio
    runtime.current_adverse_fill_ratio = cycle_plan.fill_quality.adverse_fill_ratio
    runtime.current_expected_vs_realized_edge_bps = cycle_plan.fill_quality.expected_vs_realized_edge_bps
    runtime.current_liquidity_estimate_usd = cycle_plan.snapshot.liquidity_estimate_usd
    runtime.current_quote_decision = cycle_plan.mode.reason
    runtime.current_quote_skew_multiplier = cycle_plan.aggressiveness.skew_multiplier

    intelligence.mm_mode = cycle_plan.mode.mode
    intelligence.strategy_mode = cycle_plan.mode.strategy_mode
    intelligence.quote_enabled = cycle_plan.mode.quote_enabled and cycle_plan.risk.quote_enabled
    intelligence.buy_enabled = bool(getattr(intelligence, "buy_enabled", True)) and cycle_plan.mode.buy_enabled
    intelligence.sell_enabled = bool(getattr(intelligence, "sell_enabled", True)) and cycle_plan.mode.sell_enabled
    intelligence.target_inventory_pct = cycle_plan.mode.target_inventory_pct
    intelligence.directional_bias = _clamp(cycle_plan.mode.directional_bias, -1.0, 1.0)
    intelligence.spread_multiplier *= cycle_plan.aggressiveness.spread_multiplier
    intelligence.trade_size_multiplier *= cycle_plan.aggressiveness.size_multiplier
    intelligence.cooldown_multiplier *= cycle_plan.aggressiveness.cooldown_multiplier
    intelligence.inventory_skew_multiplier *= cycle_plan.aggressiveness.skew_multiplier
    intelligence.max_inventory_multiplier *= cycle_plan.risk.inventory_cap_multiplier
    intelligence.min_edge_multiplier *= cycle_plan.performance.edge_threshold_multiplier


def quote_decision_filter_values(runtime, cycle_plan: AdaptiveCyclePlan | None) -> dict[str, object]:
    if cycle_plan is None:
        return {}
    return {
        "adaptive_profile": cycle_plan.config.profile,
        "adaptive_regime": cycle_plan.regime.regime,
        "adaptive_regime_confidence": _safe_round(cycle_plan.regime.confidence),
        "adaptive_regime_sub_scores": cycle_plan.regime.sub_scores,
        "adaptive_edge_score": _safe_round(cycle_plan.edge.total_score),
        "adaptive_edge_breakdown": cycle_plan.edge.breakdown,
        "adaptive_edge_penalties": cycle_plan.edge.penalties,
        "adaptive_mode": cycle_plan.mode.mode,
        "adaptive_mode_reason": cycle_plan.mode.reason,
        "adaptive_strategy_mode": cycle_plan.mode.strategy_mode,
        "adaptive_quote_enabled": cycle_plan.mode.quote_enabled,
        "adaptive_buy_enabled": cycle_plan.mode.buy_enabled,
        "adaptive_sell_enabled": cycle_plan.mode.sell_enabled,
        "adaptive_inventory_zone": cycle_plan.inventory_band.zone,
        "adaptive_rebalance_side": cycle_plan.inventory_band.rebalance_side,
        "adaptive_target_inventory_pct": _safe_round(cycle_plan.mode.target_inventory_pct),
        "adaptive_directional_bias": _safe_round(cycle_plan.mode.directional_bias),
        "adaptive_aggressiveness": _safe_round(cycle_plan.aggressiveness.level),
        "adaptive_size_multiplier": _safe_round(cycle_plan.aggressiveness.size_multiplier),
        "adaptive_spread_multiplier": _safe_round(cycle_plan.aggressiveness.spread_multiplier),
        "adaptive_cooldown_multiplier": _safe_round(cycle_plan.aggressiveness.cooldown_multiplier),
        "adaptive_skew_multiplier": _safe_round(cycle_plan.aggressiveness.skew_multiplier),
        "adaptive_risk_state": cycle_plan.risk.state,
        "adaptive_risk_reasons": cycle_plan.risk.reasons,
        "adaptive_liquidity_estimate_usd": _safe_round(cycle_plan.snapshot.liquidity_estimate_usd),
        "adaptive_short_return_bps": _safe_round(cycle_plan.snapshot.short_return_bps),
        "adaptive_medium_return_bps": _safe_round(cycle_plan.snapshot.medium_return_bps),
        "adaptive_rolling_pnl_usd": _safe_round(cycle_plan.snapshot.rolling_pnl_usd),
        "adaptive_rolling_drawdown_pct": _safe_round(cycle_plan.snapshot.rolling_drawdown_pct),
        "adaptive_recent_fill_count": cycle_plan.snapshot.recent_fill_count,
        "adaptive_adverse_fill_ratio": _safe_round(cycle_plan.fill_quality.adverse_fill_ratio),
        "adaptive_toxic_fill_ratio": _safe_round(cycle_plan.fill_quality.toxic_fill_ratio),
        "adaptive_expected_vs_realized_edge_bps": _safe_round(cycle_plan.fill_quality.expected_vs_realized_edge_bps),
        "adaptive_perf_trade_count": cycle_plan.performance.trade_count,
        "adaptive_perf_pnl_usd": _safe_round(cycle_plan.performance.pnl_usd),
        "adaptive_perf_drawdown_pct": _safe_round(cycle_plan.performance.drawdown_pct),
        "adaptive_perf_hit_rate": _safe_round(cycle_plan.performance.hit_rate),
        "adaptive_perf_spread_baseline_multiplier": _safe_round(cycle_plan.performance.spread_baseline_multiplier),
        "adaptive_perf_size_cap_multiplier": _safe_round(cycle_plan.performance.size_cap_multiplier),
        "adaptive_perf_edge_threshold_multiplier": _safe_round(cycle_plan.performance.edge_threshold_multiplier),
        "adaptive_perf_skew_multiplier": _safe_round(cycle_plan.performance.skew_strength_multiplier),
    }


def update_quote_decision_runtime(runtime, *, spread_bps: float, size_usd: float, bid: float, ask: float) -> None:
    runtime.current_quote_spread_bps = _safe_round(spread_bps)
    runtime.current_quote_size_usd = _safe_round(size_usd)
    cycle_plan = getattr(runtime, "current_adaptive_plan", None)
    profile = getattr(getattr(runtime, "adaptive_config", None), "profile", "")
    spread_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.spread_multiplier
    size_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.size_multiplier
    skew_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.skew_multiplier
    runtime.current_quote_decision = (
        f"spread={spread_bps:.2f}bps|size={size_usd:.2f}|bid={bid:.2f}|ask={ask:.2f}|"
        f"mode={runtime.current_mm_mode}|risk={runtime.current_risk_governor_state}|"
        f"profile={profile or '-'}|spread_mult={spread_multiplier:.2f}|"
        f"size_mult={size_multiplier:.2f}|skew={skew_multiplier:.2f}"
    )


def build_hourly_report(runtime, cycle_index: int) -> dict[str, object] | None:
    if not getattr(runtime, "adaptive_config", None) or not runtime.adaptive_config.logging_enabled:
        return None
    lookback_cycles = max(int(round((ADAPTIVE_REPORT_WINDOW_MINUTES * 60.0) / max(runtime.cycle_seconds, 1.0))), 1)
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    events = [event for event in list(runtime.adaptive_cycle_history) if int(event.get("cycle", -1)) >= floor_cycle]
    if not events:
        return None

    recent_trades = [trade for trade in runtime.performance.trade_history if trade.cycle_index >= floor_cycle]
    realized_pnl = sum(float(getattr(trade, "realized_pnl", 0.0) or 0.0) for trade in recent_trades if trade.side == "sell")
    skip_reasons = Counter(str(event.get("block_reason", "") or "") for event in events if not bool(event.get("allow_trade", False)))
    mode_counts = Counter(str(event.get("mm_mode", "") or "") for event in events)
    drawdown_pct = max(float(event.get("drawdown_pct", 0.0) or 0.0) for event in events)
    return {
        "trade_count": len(recent_trades),
        "pnl_usd": _safe_round(realized_pnl),
        "drawdown_pct": _safe_round(drawdown_pct),
        "mode_distribution": dict(mode_counts),
        "skip_reasons": dict(skip_reasons),
        "toxic_fill_ratio": _safe_round(getattr(runtime, "current_toxic_fill_ratio", 0.0)),
    }
