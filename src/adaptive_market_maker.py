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
    BOT_MODE,
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


def _configured_band_half_width(lower: float, upper: float, fallback: float) -> float:
    low = _clamp(lower, 0.0, 1.0)
    high = _clamp(upper, 0.0, 1.0)
    if high < low:
        low, high = high, low
    half_width = (high - low) / 2.0
    return half_width if half_width > 0 else fallback


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
    if score >= 40.0:
        return "strong_positive"
    if score >= 20.0:
        return "weak_positive"
    if score >= -20.0:
        return "slightly_negative"
    return "bad"


def _edge_total_level(score: float) -> float:
    return _safe_round(_clamp((score + 100.0) / 2.0, 0.0, 100.0))


def _edge_posture(score: float) -> str:
    edge_level = _edge_total_level(score)
    if edge_level >= ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE:
        return "aggressive"
    if edge_level >= ADAPTIVE_NORMAL_MODE_MIN_EDGE:
        return "normal"
    if edge_level >= ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE:
        return "defensive"
    if edge_level >= ADAPTIVE_EDGE_STANDBY_SCORE:
        return "defensive"
    return "standby"


def _regime_confidence_score(confidence: float) -> float:
    return _safe_round(_clamp(confidence * 100.0, 0.0, 100.0))


def _severe_illiquidity_score(snapshot: "MarketStateSnapshot") -> float:
    illiquid_spread_bps = max(ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS, 1.0)
    illiquid_liquidity_usd = max(ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD, 1.0)
    spread_pressure = _clamp(
        max(snapshot.spread_bps - illiquid_spread_bps, 0.0) / illiquid_spread_bps,
        0.0,
        2.0,
    )
    liquidity_shortfall = _clamp(
        max(illiquid_liquidity_usd - snapshot.liquidity_estimate_usd, 0.0) / illiquid_liquidity_usd,
        0.0,
        1.0,
    )
    return _safe_round(
        _clamp(
            (spread_pressure * 40.0)
            + (liquidity_shortfall * 35.0)
            + (snapshot.spread_instability * 20.0)
            + (min(snapshot.quote_pressure_score / 100.0, 1.0) * 5.0),
            0.0,
            100.0,
        )
    )


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return variance ** 0.5


def _returns_bps(prices: list[float], lookback: int) -> list[float]:
    if len(prices) < max(lookback + 1, 2):
        return []
    values: list[float] = []
    window = prices[-(lookback + 1):]
    for index in range(1, len(window)):
        previous = window[index - 1]
        current = window[index]
        if previous <= 0:
            continue
        values.append(((current / previous) - 1.0) * 10_000.0)
    return values


def _realized_volatility_bps(prices: list[float], lookback: int) -> float:
    return _stdev(_returns_bps(prices, lookback))


def _microtrend_strength(prices: list[float], lookback: int) -> float:
    if len(prices) < max(lookback + 1, 2):
        return 0.0
    realized = abs(_return_bps(prices, lookback))
    persistence = _direction_consistency(prices, lookback)
    return _clamp((realized / max(ADAPTIVE_REGIME_BREAKOUT_BPS, 8.0)) * (0.55 + (persistence * 0.70)), 0.0, 2.0)


def _jump_frequency(prices: list[float], lookback: int, *, jump_threshold_bps: float) -> float:
    returns = _returns_bps(prices, lookback)
    if not returns:
        return 0.0
    threshold = max(jump_threshold_bps, 1.0)
    return sum(1 for value in returns if abs(value) >= threshold) / len(returns)


def _spread_instability(runtime, spread_bps: float, lookback_cycles: int = 24) -> float:
    spreads = [
        float(event.get("quote_spread_bps", 0.0) or 0.0)
        for event in list(getattr(runtime, "adaptive_cycle_history", []))[-lookback_cycles:]
        if float(event.get("quote_spread_bps", 0.0) or 0.0) > 0.0
    ]
    if spread_bps > 0:
        spreads.append(spread_bps)
    if len(spreads) < 2:
        return 0.0
    mean_spread = max(_mean(spreads), 1e-9)
    return _clamp(_stdev(spreads) / mean_spread, 0.0, 1.5)


def _recent_toxic_cycle_count(runtime, cycle_index: int, lookback_cycles: int = 12) -> int:
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    count = 0
    for event in list(getattr(runtime, "adaptive_cycle_history", [])):
        if int(event.get("cycle", -1)) < floor_cycle:
            continue
        if float(event.get("toxic_fill_ratio", 0.0) or 0.0) >= 0.55 or int(event.get("defensive_stage", 0) or 0) >= 2:
            count += 1
    return count


def _recent_negative_pnl_deterioration(runtime, cycle_index: int, lookback_cycles: int = 12) -> float:
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    values = [
        float(event.get("realized_pnl_delta", 0.0) or 0.0)
        for event in list(getattr(runtime, "adaptive_cycle_history", []))
        if int(event.get("cycle", -1)) >= floor_cycle
    ]
    return abs(sum(min(value, 0.0) for value in values))


def _paper_mode() -> bool:
    return BOT_MODE.strip().lower().startswith("paper")


V6_PROFILE_PRESETS: dict[str, dict[str, float]] = {
    "v6_balanced_paper": {
        "full_quote_edge": 18.0,
        "cautious_quote_edge": -1.0,
        "reduced_quote_edge": -20.0,
        "hard_block_edge": -44.0,
        "regime_sensitivity": 1.00,
        "blocker_strictness": 0.92,
        "range_spread_mult": 0.92,
        "trend_spread_mult": 1.03,
        "chaos_spread_mult": 1.26,
        "range_size_mult": 1.08,
        "trend_size_mult": 1.00,
        "chaos_size_mult": 0.62,
        "trend_inventory_shift": 0.07,
        "paper_min_quotes_per_hour": 160.0,
        "paper_min_fills_low": 3.0,
        "paper_min_fills_high": 8.0,
        "paper_tighten_after_cycles": 10.0,
        "paper_relax_after_cycles": 18.0,
        "paper_tighten_step": 0.03,
        "paper_min_spread_mult": 0.82,
        "paper_edge_relax_mult": 0.86,
        "paper_size_boost": 1.05,
        "stage1_toxic_cycles": 3.0,
        "stage2_toxic_cycles": 6.0,
        "stage3_toxic_cycles": 9.0,
        "stage4_toxic_cycles": 12.0,
    },
    "v6_aggressive_paper": {
        "full_quote_edge": 15.0,
        "cautious_quote_edge": -4.0,
        "reduced_quote_edge": -24.0,
        "hard_block_edge": -52.0,
        "regime_sensitivity": 1.08,
        "blocker_strictness": 0.84,
        "range_spread_mult": 0.88,
        "trend_spread_mult": 0.99,
        "chaos_spread_mult": 1.18,
        "range_size_mult": 1.15,
        "trend_size_mult": 1.08,
        "chaos_size_mult": 0.72,
        "trend_inventory_shift": 0.09,
        "paper_min_quotes_per_hour": 200.0,
        "paper_min_fills_low": 4.0,
        "paper_min_fills_high": 10.0,
        "paper_tighten_after_cycles": 8.0,
        "paper_relax_after_cycles": 14.0,
        "paper_tighten_step": 0.04,
        "paper_min_spread_mult": 0.78,
        "paper_edge_relax_mult": 0.78,
        "paper_size_boost": 1.10,
        "stage1_toxic_cycles": 4.0,
        "stage2_toxic_cycles": 7.0,
        "stage3_toxic_cycles": 10.0,
        "stage4_toxic_cycles": 13.0,
    },
    "ultra_aggressive_paper": {
        "full_quote_edge": 10.0,
        "cautious_quote_edge": -8.0,
        "reduced_quote_edge": -30.0,
        "hard_block_edge": -60.0,
        "regime_sensitivity": 1.06,
        "blocker_strictness": 0.78,
        "range_spread_mult": 0.84,
        "trend_spread_mult": 0.98,
        "chaos_spread_mult": 1.14,
        "range_size_mult": 1.18,
        "trend_size_mult": 1.10,
        "chaos_size_mult": 0.78,
        "trend_inventory_shift": 0.10,
        "paper_min_quotes_per_hour": 260.0,
        "paper_min_fills_low": 6.0,
        "paper_min_fills_high": 14.0,
        "paper_tighten_after_cycles": 6.0,
        "paper_relax_after_cycles": 10.0,
        "paper_tighten_step": 0.05,
        "paper_min_spread_mult": 0.76,
        "paper_edge_relax_mult": 0.72,
        "paper_size_boost": 1.14,
        "stage1_toxic_cycles": 5.0,
        "stage2_toxic_cycles": 8.0,
        "stage3_toxic_cycles": 11.0,
        "stage4_toxic_cycles": 15.0,
    },
    "v6_balanced_live": {
        "full_quote_edge": 22.0,
        "cautious_quote_edge": 2.0,
        "reduced_quote_edge": -16.0,
        "hard_block_edge": -34.0,
        "regime_sensitivity": 0.98,
        "blocker_strictness": 1.04,
        "range_spread_mult": 0.95,
        "trend_spread_mult": 1.07,
        "chaos_spread_mult": 1.34,
        "range_size_mult": 1.00,
        "trend_size_mult": 0.96,
        "chaos_size_mult": 0.52,
        "trend_inventory_shift": 0.06,
        "paper_min_quotes_per_hour": 0.0,
        "paper_min_fills_low": 0.0,
        "paper_min_fills_high": 0.0,
        "paper_tighten_after_cycles": 0.0,
        "paper_relax_after_cycles": 0.0,
        "paper_tighten_step": 0.0,
        "paper_min_spread_mult": 1.0,
        "paper_edge_relax_mult": 1.0,
        "paper_size_boost": 1.0,
        "stage1_toxic_cycles": 3.0,
        "stage2_toxic_cycles": 5.0,
        "stage3_toxic_cycles": 7.0,
        "stage4_toxic_cycles": 9.0,
    },
    "v6_defensive_live": {
        "full_quote_edge": 26.0,
        "cautious_quote_edge": 6.0,
        "reduced_quote_edge": -10.0,
        "hard_block_edge": -24.0,
        "regime_sensitivity": 0.94,
        "blocker_strictness": 1.14,
        "range_spread_mult": 1.00,
        "trend_spread_mult": 1.12,
        "chaos_spread_mult": 1.42,
        "range_size_mult": 0.94,
        "trend_size_mult": 0.90,
        "chaos_size_mult": 0.42,
        "trend_inventory_shift": 0.05,
        "paper_min_quotes_per_hour": 0.0,
        "paper_min_fills_low": 0.0,
        "paper_min_fills_high": 0.0,
        "paper_tighten_after_cycles": 0.0,
        "paper_relax_after_cycles": 0.0,
        "paper_tighten_step": 0.0,
        "paper_min_spread_mult": 1.0,
        "paper_edge_relax_mult": 1.0,
        "paper_size_boost": 1.0,
        "stage1_toxic_cycles": 2.0,
        "stage2_toxic_cycles": 4.0,
        "stage3_toxic_cycles": 6.0,
        "stage4_toxic_cycles": 8.0,
    },
}

V6_PROFILE_ALIASES = {
    "default": "",
    "balanced_paper": "v6_balanced_paper",
    "aggressive_paper": "v6_aggressive_paper",
    "ultra_aggressive_paper": "ultra_aggressive_paper",
    "ultra_aggressive": "ultra_aggressive_paper",
    "uap": "ultra_aggressive_paper",
    "balanced_live": "v6_balanced_live",
    "defensive_live": "v6_defensive_live",
    "v6_balanced_paper": "v6_balanced_paper",
    "v6_aggressive_paper": "v6_aggressive_paper",
    "v6_balanced_live": "v6_balanced_live",
    "v6_defensive_live": "v6_defensive_live",
}


def _normalize_v6_profile(profile: str | None) -> str:
    raw_profile = str(profile or "").strip().lower()
    resolved = V6_PROFILE_ALIASES.get(raw_profile, raw_profile)
    if not resolved:
        return "v6_balanced_paper" if _paper_mode() else "v6_balanced_live"
    if resolved not in V6_PROFILE_PRESETS:
        return "v6_balanced_paper" if _paper_mode() else "v6_balanced_live"
    return resolved


def _profile_settings(profile: str | None) -> dict[str, float]:
    return dict(V6_PROFILE_PRESETS[_normalize_v6_profile(profile)])


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
    microtrend_strength: float
    spread_bps: float
    spread_instability: float
    price_jump_frequency: float
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
    queue_latency_quality: float


@dataclass(frozen=True)
class AdaptiveRegimeAssessment:
    regime: str
    confidence: float
    sub_scores: dict[str, float]
    reason: list[str] = field(default_factory=list)
    trend_bias: float = 0.0


@dataclass(frozen=True)
class AdaptiveEdgeAssessment:
    total_score: float
    breakdown: dict[str, float]
    penalties: dict[str, float]
    bucket: str
    permission_state: str
    hard_block_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InventoryBandState:
    zone: str
    target_inventory_pct: float
    rebalance_side: str = ""
    inventory_pressure: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 1.0
    inventory_pressure_score: float = 0.0
    recovery_mode: bool = False


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
    stage: int
    size_multiplier: float
    spread_multiplier: float
    inventory_cap_multiplier: float
    quote_enabled: bool
    buy_enabled: bool = True
    sell_enabled: bool = True
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
class ActivityFloorState:
    state: str
    inactivity_cycles: int
    quote_opportunities_per_hour: float
    fills_per_hour: float
    spread_multiplier: float = 1.0
    size_multiplier: float = 1.0
    edge_threshold_multiplier: float = 1.0
    cooldown_multiplier: float = 1.0
    override_applied: bool = False


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
    activity_floor: ActivityFloorState
    mode: ModeSelection
    aggressiveness: AggressivenessProfile


def build_adaptive_feature_config(overrides: dict[str, bool] | None = None) -> AdaptiveFeatureConfig:
    values = {
        "profile": _normalize_v6_profile(ADAPTIVE_MM_PROFILE).upper(),
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
            if key == "profile":
                values[key] = _normalize_v6_profile(str(value)).upper()
            else:
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
    realized_volatility_bps = _realized_volatility_bps(prices, min(max(lookback, 12), max(len(prices) - 1, 1)))
    mean_reversion_distance_bps = 0.0
    if price_mean > 0 and mid > 0:
        mean_reversion_distance_bps = ((mid / price_mean) - 1.0) * 10_000.0
    range_width_bps = _range_width_bps(prices, min(max(lookback, 12), max(len(prices), 1)))
    direction_consistency = _direction_consistency(prices, min(max(lookback, 12), max(len(prices) - 1, 1)))
    sign_flip_ratio = _sign_flip_ratio(prices, min(max(lookback, 12), max(len(prices) - 1, 1)))
    regime_volatility_bps = max(getattr(getattr(runtime, "current_regime_assessment", None), "volatility_score", 0.0), 0.0)
    volatility_bps = max(regime_volatility_bps, realized_volatility_bps)
    microtrend_strength = _microtrend_strength(prices, min(max(lookback, 10), max(len(prices) - 1, 1)))
    spread_instability = _spread_instability(runtime, spread_bps)
    price_jump_frequency = _jump_frequency(
        prices,
        min(max(lookback, 12), max(len(prices) - 1, 1)),
        jump_threshold_bps=max(ADAPTIVE_REGIME_BREAKOUT_BPS * 0.45, max(realized_volatility_bps * 1.35, 6.0)),
    )
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
    queue_latency_quality = _clamp(
        source_health_score
        - min(abs(fill_quality.expected_vs_realized_edge_bps) / 25.0, 1.0) * 0.35
        - spread_instability * 0.20,
        0.0,
        1.0,
    )
    return MarketStateSnapshot(
        mid_price=_safe_round(mid),
        short_return_bps=_safe_round(short_return_bps),
        medium_return_bps=_safe_round(medium_return_bps),
        volatility=_safe_round(max(volatility_bps, 0.0) / 10_000.0),
        volatility_bps=_safe_round(volatility_bps),
        microtrend_strength=_safe_round(microtrend_strength),
        spread_bps=_safe_round(spread_bps),
        spread_instability=_safe_round(spread_instability),
        price_jump_frequency=_safe_round(price_jump_frequency),
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
        queue_latency_quality=_safe_round(queue_latency_quality),
    )


def classify_regime(snapshot: MarketStateSnapshot, profile: str | None = None) -> AdaptiveRegimeAssessment:
    settings = _profile_settings(profile)
    breakout_bps = max(ADAPTIVE_REGIME_BREAKOUT_BPS * settings["regime_sensitivity"], 8.0)
    high_vol_bps = max(HIGH_VOL_THRESHOLD * 10_000.0, 1.0)
    vol_ratio = snapshot.volatility_bps / high_vol_bps
    abs_short = abs(snapshot.short_return_bps)
    abs_medium = abs(snapshot.medium_return_bps)
    persistence = snapshot.direction_consistency
    noise = snapshot.sign_flip_ratio
    toxicity = snapshot.toxic_fill_ratio
    adverse = snapshot.adverse_fill_ratio
    spread_instability = snapshot.spread_instability
    jumps = snapshot.price_jump_frequency
    microtrend = snapshot.microtrend_strength
    mean_distance = abs(snapshot.mean_reversion_distance_bps)

    range_score = (
        max(1.25 - vol_ratio, 0.0) * 18.0
        + max(1.0 - microtrend * 0.70, 0.0) * 18.0
        + noise * 16.0
        + max(1.0 - persistence, 0.0) * 14.0
        + min(mean_distance / max(breakout_bps, 1.0), 1.5) * 14.0
        + max(1.0 - spread_instability, 0.0) * 10.0
        + max(1.0 - toxicity, 0.0) * 10.0
        + max(1.0 - jumps * 1.5, 0.0) * 10.0
    )
    trend_up_score = (
        max(snapshot.medium_return_bps, 0.0) / breakout_bps * 28.0
        + max(snapshot.short_return_bps, 0.0) / breakout_bps * 16.0
        + microtrend * 18.0
        + persistence * 18.0
        + max(1.0 - noise, 0.0) * 10.0
        + max(1.0 - spread_instability, 0.0) * 6.0
        + max(1.0 - toxicity, 0.0) * 4.0
        - toxicity * 10.0
        - adverse * 6.0
        - spread_instability * 8.0
        - jumps * 10.0
        - max(vol_ratio - 1.0, 0.0) * 8.0
    )
    trend_down_score = (
        max(-snapshot.medium_return_bps, 0.0) / breakout_bps * 28.0
        + max(-snapshot.short_return_bps, 0.0) / breakout_bps * 16.0
        + microtrend * 18.0
        + persistence * 18.0
        + max(1.0 - noise, 0.0) * 10.0
        + max(1.0 - spread_instability, 0.0) * 6.0
        + max(1.0 - toxicity, 0.0) * 4.0
        - toxicity * 10.0
        - adverse * 6.0
        - spread_instability * 8.0
        - jumps * 10.0
        - max(vol_ratio - 1.0, 0.0) * 8.0
    )
    chaos_score = (
        max(vol_ratio - 1.0, 0.0) * 26.0
        + spread_instability * 22.0
        + toxicity * 24.0
        + adverse * 16.0
        + jumps * 20.0
        + min(snapshot.quote_pressure_score / 100.0, 1.0) * 12.0
        + min(snapshot.rolling_drawdown_pct * 12.0, 1.0) * 8.0
        + microtrend * 4.0
    )

    sub_scores = {
        "RANGE": _safe_round(_clamp(range_score, 0.0, 100.0)),
        "TREND_UP": _safe_round(_clamp(trend_up_score, 0.0, 100.0)),
        "TREND_DOWN": _safe_round(_clamp(trend_down_score, 0.0, 100.0)),
        "CHAOS": _safe_round(_clamp(chaos_score, 0.0, 100.0)),
    }
    ranked = sorted(sub_scores.items(), key=lambda item: (-item[1], item[0]))
    best_regime, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = _clamp((best_score / 100.0) * 0.60 + max(best_score - second_score, 0.0) / 100.0 * 0.40, 0.0, 1.0)
    trend_bias = 0.0
    if best_regime == "TREND_UP":
        trend_bias = _clamp((snapshot.short_return_bps / max(breakout_bps, 1.0)) * 0.55 + persistence * 0.45, 0.0, 1.0)
    elif best_regime == "TREND_DOWN":
        trend_bias = -_clamp((abs(snapshot.short_return_bps) / max(breakout_bps, 1.0)) * 0.55 + persistence * 0.45, 0.0, 1.0)
    elif best_regime == "CHAOS":
        trend_bias = _clamp(snapshot.short_return_bps / max(breakout_bps * 1.5, 1.0), -0.40, 0.40)
    else:
        trend_bias = _clamp(snapshot.short_return_bps / max(breakout_bps * 2.5, 1.0), -0.18, 0.18)

    reason: list[str] = []
    if best_regime == "RANGE":
        if noise >= 0.45:
            reason.append("sign_flips_support_range")
        if mean_distance >= breakout_bps * 0.35:
            reason.append("mean_reversion_distance_present")
        if vol_ratio <= 1.05:
            reason.append("volatility_contained")
    elif best_regime == "TREND_UP":
        if snapshot.medium_return_bps > breakout_bps * 0.70:
            reason.append("positive_drift_strong")
        if persistence >= 0.65:
            reason.append("directional_persistence_high")
        if microtrend >= 0.80:
            reason.append("microtrend_strength_high")
    elif best_regime == "TREND_DOWN":
        if snapshot.medium_return_bps < -(breakout_bps * 0.70):
            reason.append("negative_drift_strong")
        if persistence >= 0.65:
            reason.append("directional_persistence_high")
        if microtrend >= 0.80:
            reason.append("microtrend_strength_high")
    else:
        if vol_ratio >= ADAPTIVE_REGIME_EVENT_VOL_MULTIPLIER:
            reason.append("realized_volatility_spike")
        if spread_instability >= 0.35:
            reason.append("spread_instability_high")
        if toxicity >= 0.45 or jumps >= 0.25:
            reason.append("toxicity_or_jumps_elevated")
    return AdaptiveRegimeAssessment(
        regime=best_regime,
        confidence=_safe_round(confidence),
        sub_scores=sub_scores,
        reason=reason,
        trend_bias=_safe_round(trend_bias),
    )


def score_edge(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    *,
    cooldown_active: bool,
    profile: str | None = None,
) -> AdaptiveEdgeAssessment:
    settings = _profile_settings(profile)
    strictness = settings["blocker_strictness"]
    high_vol_bps = max(HIGH_VOL_THRESHOLD * 10_000.0, 1.0)
    breakout_bps = max(ADAPTIVE_REGIME_BREAKOUT_BPS, 8.0)
    vol_ratio = snapshot.volatility_bps / high_vol_bps
    trend_direction = 1.0 if regime.regime == "TREND_UP" else -1.0 if regime.regime == "TREND_DOWN" else 0.0
    trend_alignment = trend_direction * _clamp(snapshot.medium_return_bps / max(breakout_bps * 1.2, 1.0), -1.0, 1.0)
    range_reversion = _clamp(
        min(abs(snapshot.mean_reversion_distance_bps) / max(breakout_bps, 1.0), 1.4) * 0.65
        + snapshot.sign_flip_ratio * 0.35
        - snapshot.price_jump_frequency * 0.45,
        -1.0,
        1.0,
    )
    if regime.regime != "RANGE":
        range_reversion *= 0.55
    spread_capture = _clamp(
        min(snapshot.spread_bps / max(snapshot.volatility_bps * 0.55, 4.0), 1.5) * 0.75
        + min(snapshot.fill_rate * 2.5, 0.35)
        - snapshot.spread_instability * 0.35,
        -1.0,
        1.0,
    )
    realized_vol_penalty = _clamp(max(vol_ratio - 0.95, 0.0) * 0.55 + snapshot.price_jump_frequency * 0.45, 0.0, 1.6)
    adverse_penalty = _clamp(snapshot.adverse_fill_ratio * 1.25 + max(snapshot.expected_vs_realized_edge_bps, 0.0) / 35.0, 0.0, 1.6)
    fill_quality_penalty = _clamp(
        snapshot.toxic_fill_ratio * 1.05
        + max(snapshot.expected_vs_realized_edge_bps, 0.0) / 40.0
        + snapshot.spread_instability * 0.35,
        0.0,
        1.6,
    )
    toxic_flow_penalty = _clamp(
        snapshot.toxic_fill_ratio * 1.20
        + snapshot.quote_pressure_score / 100.0 * 0.65
        + snapshot.price_jump_frequency * 0.40,
        0.0,
        1.8,
    )
    inventory_pressure = _clamp(abs(snapshot.inventory_pct - 0.50) / 0.18, 0.0, 1.5)
    inventory_recovery_bonus = inventory_pressure * (0.60 if regime.regime == "RANGE" else 0.42 if regime.regime.startswith("TREND") else 0.18)
    queue_latency_quality = _clamp((snapshot.queue_latency_quality * 2.0) - 1.0, -1.0, 1.0)
    if cooldown_active:
        queue_latency_quality -= 0.10

    breakdown = {
        "microtrend_alignment": _safe_round(trend_alignment * 24.0),
        "reversion_probability": _safe_round(range_reversion * 18.0),
        "expected_spread_capture": _safe_round(spread_capture * 22.0),
        "realized_vol_penalty": _safe_round(-realized_vol_penalty * 16.0 * strictness),
        "adverse_selection_penalty": _safe_round(-adverse_penalty * 18.0 * strictness),
        "fill_quality_penalty": _safe_round(-fill_quality_penalty * 15.0 * strictness),
        "mev_toxic_flow_penalty": _safe_round(-toxic_flow_penalty * 16.0 * strictness),
        "inventory_recovery_bonus": _safe_round(inventory_recovery_bonus * 12.0),
        "queue_latency_quality": _safe_round(queue_latency_quality * 10.0),
    }
    penalties = {
        "cooldown_penalty": _safe_round(4.0 if cooldown_active else 0.0),
        "spread_instability_penalty": _safe_round(max(snapshot.spread_instability - 0.25, 0.0) * 12.0 * strictness),
        "drawdown_penalty": _safe_round(min(snapshot.rolling_drawdown_pct * 80.0, 10.0) * strictness),
    }
    total_score = _clamp(sum(breakdown.values()) - sum(penalties.values()), -100.0, 100.0)
    hard_block_reasons: list[str] = []
    if snapshot.mid_price <= 0 or snapshot.spread_bps <= 0:
        hard_block_reasons.append("invalid_quote_inputs")
    if snapshot.source_health_score < 1.0 and snapshot.queue_latency_quality < 0.40:
        hard_block_reasons.append("source_health_degraded")
    if snapshot.spread_bps >= ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS * 2.4 and snapshot.spread_instability >= 0.45:
        hard_block_reasons.append("spread_quality_collapsed")
    if snapshot.toxic_fill_ratio >= (0.90 + max(1.0 - strictness, 0.0) * 0.05):
        hard_block_reasons.append("toxic_flow_extreme")
    if snapshot.expected_vs_realized_edge_bps >= 18.0 and snapshot.recent_fill_count >= 3:
        hard_block_reasons.append("fill_quality_severely_negative")

    if hard_block_reasons:
        permission_state = "BLOCKED"
        total_score = min(total_score, settings["hard_block_edge"] - 4.0)
    elif total_score >= settings["full_quote_edge"]:
        permission_state = "FULL"
    elif total_score >= settings["cautious_quote_edge"]:
        permission_state = "CAUTIOUS"
    elif total_score >= settings["reduced_quote_edge"]:
        permission_state = "REDUCED"
    elif total_score >= settings["hard_block_edge"]:
        permission_state = "DEFENSIVE_ONLY"
    else:
        permission_state = "BLOCKED"
    return AdaptiveEdgeAssessment(
        total_score=_safe_round(total_score),
        breakdown=breakdown,
        penalties=penalties,
        bucket=_score_bucket(total_score),
        permission_state=permission_state,
        hard_block_reasons=hard_block_reasons,
    )


def classify_inventory_band(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment | None = None,
) -> InventoryBandState:
    inventory_pct = snapshot.inventory_pct
    regime_label = "RANGE" if regime is None else regime.regime
    trend_shift = 0.0 if regime is None else regime.trend_bias
    base_center = _clamp((ADAPTIVE_INVENTORY_NEUTRAL_MIN + ADAPTIVE_INVENTORY_NEUTRAL_MAX) / 2.0, 0.0, 1.0)
    neutral_half = _configured_band_half_width(
        ADAPTIVE_INVENTORY_NEUTRAL_MIN,
        ADAPTIVE_INVENTORY_NEUTRAL_MAX,
        0.04,
    )
    soft_half = max(
        _configured_band_half_width(
            ADAPTIVE_INVENTORY_SOFT_MIN,
            ADAPTIVE_INVENTORY_SOFT_MAX,
            0.08,
        ),
        neutral_half + 0.01,
    )
    strong_half = max(
        _configured_band_half_width(
            ADAPTIVE_INVENTORY_STRONG_MIN,
            ADAPTIVE_INVENTORY_STRONG_MAX,
            0.12,
        ),
        soft_half + 0.01,
    )
    hard_half = max(
        _configured_band_half_width(
            ADAPTIVE_INVENTORY_HARD_MIN,
            ADAPTIVE_INVENTORY_HARD_MAX,
            0.17,
        ),
        strong_half + 0.01,
    )
    trend_shift_budget = min(strong_half * 0.75, 0.12)

    if regime_label == "RANGE":
        center = base_center
        recovery_mode = abs(inventory_pct - center) >= neutral_half
    elif regime_label == "TREND_UP":
        center = _clamp(base_center + max(trend_shift, 0.0) * trend_shift_budget, base_center, 0.68)
        recovery_mode = inventory_pct > center + neutral_half
    elif regime_label == "TREND_DOWN":
        center = _clamp(base_center + min(trend_shift, 0.0) * trend_shift_budget, 0.32, base_center)
        recovery_mode = inventory_pct < center - neutral_half or inventory_pct > center + neutral_half
    else:
        center = base_center
        neutral_half *= 0.80
        soft_half *= 0.80
        strong_half *= 0.80
        hard_half *= 0.80
        recovery_mode = True

    lower_neutral = _clamp(center - neutral_half, 0.0, 1.0)
    upper_neutral = _clamp(center + neutral_half, 0.0, 1.0)
    lower_soft = _clamp(center - soft_half, 0.0, 1.0)
    upper_soft = _clamp(center + soft_half, 0.0, 1.0)
    lower_strong = _clamp(center - strong_half, 0.0, 1.0)
    upper_strong = _clamp(center + strong_half, 0.0, 1.0)
    lower_hard = _clamp(center - hard_half, 0.0, 1.0)
    upper_hard = _clamp(center + hard_half, 0.0, 1.0)
    pressure = abs(inventory_pct - center) / max(hard_half, 0.01)
    pressure_score = _clamp(pressure * 100.0, 0.0, 100.0)
    rebalance_side = "sell" if inventory_pct > center else "buy"

    if lower_neutral <= inventory_pct <= upper_neutral:
        zone = "neutral"
        target = center
    elif lower_soft <= inventory_pct <= upper_soft:
        zone = "soft_skew"
        target = center - 0.02 if inventory_pct > center else center + 0.02
    elif lower_strong <= inventory_pct <= upper_strong:
        zone = "strong_skew"
        target = center - 0.03 if inventory_pct > center else center + 0.03
    elif lower_hard <= inventory_pct <= upper_hard:
        zone = "hard_skew"
        target = center - 0.05 if inventory_pct > center else center + 0.05
    else:
        zone = "hard_breach"
        target = center - 0.07 if inventory_pct > center else center + 0.07

    return InventoryBandState(
        zone=zone,
        target_inventory_pct=_safe_round(_clamp(target, 0.0, 1.0)),
        rebalance_side=rebalance_side if zone != "neutral" else "",
        inventory_pressure=_safe_round(pressure),
        lower_bound=_safe_round(lower_neutral),
        upper_bound=_safe_round(upper_neutral),
        inventory_pressure_score=_safe_round(pressure_score),
        recovery_mode=recovery_mode,
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


def assess_activity_floor(
    runtime,
    cycle_index: int,
    snapshot: MarketStateSnapshot,
    *,
    profile: str | None,
) -> ActivityFloorState:
    settings = _profile_settings(profile)
    inactivity_cycles = (
        max(cycle_index - int(getattr(runtime, "last_fill_cycle", cycle_index)), 0)
        if getattr(runtime, "last_fill_cycle", None) is not None
        else cycle_index + 1
    )
    if not _paper_mode() or settings["paper_min_quotes_per_hour"] <= 0:
        return ActivityFloorState("disabled", inactivity_cycles, 0.0, 0.0)

    lookback_cycles = max(int(round(3600.0 / max(runtime.cycle_seconds, 1.0))), 1)
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    quote_opportunities = sum(1 for cycle in list(getattr(runtime, "quote_cycle_history", [])) if cycle >= floor_cycle)
    fills = sum(1 for cycle in list(getattr(runtime, "fill_cycle_history", [])) if cycle >= floor_cycle)
    elapsed_hours = max(min((cycle_index + 1) / lookback_cycles, 1.0), 1.0 / max(lookback_cycles, 1))
    quotes_per_hour = quote_opportunities / elapsed_hours
    fills_per_hour = fills / elapsed_hours
    state = "healthy"
    spread_multiplier = 1.0
    size_multiplier = 1.0
    edge_threshold_multiplier = 1.0
    cooldown_multiplier = 1.0
    override_applied = False

    needs_quote_floor = quotes_per_hour < settings["paper_min_quotes_per_hour"]
    needs_fill_floor = fills_per_hour < settings["paper_min_fills_low"]
    if needs_quote_floor or needs_fill_floor or inactivity_cycles >= settings["paper_tighten_after_cycles"]:
        steps = 1 + int(
            max(inactivity_cycles - settings["paper_tighten_after_cycles"], 0.0)
            // max(settings["paper_tighten_after_cycles"] / 2.0, 1.0)
        )
        spread_multiplier = max(
            1.0 - (steps * settings["paper_tighten_step"]),
            settings["paper_min_spread_mult"],
        )
        size_multiplier = settings["paper_size_boost"]
        cooldown_multiplier = 0.94
        state = "tighten_quotes"
        override_applied = True

    if inactivity_cycles >= settings["paper_relax_after_cycles"]:
        edge_threshold_multiplier = settings["paper_edge_relax_mult"]
        spread_multiplier = max(spread_multiplier - 0.03, settings["paper_min_spread_mult"])
        size_multiplier = max(size_multiplier, settings["paper_size_boost"])
        cooldown_multiplier = min(cooldown_multiplier, 0.90)
        state = "filters_relaxed"
        override_applied = True

    if fills_per_hour >= settings["paper_min_fills_low"] and fills_per_hour <= max(settings["paper_min_fills_high"], settings["paper_min_fills_low"]):
        if state == "healthy":
            state = "within_fill_band"

    return ActivityFloorState(
        state=state,
        inactivity_cycles=inactivity_cycles,
        quote_opportunities_per_hour=_safe_round(quotes_per_hour),
        fills_per_hour=_safe_round(fills_per_hour),
        spread_multiplier=_safe_round(spread_multiplier),
        size_multiplier=_safe_round(size_multiplier),
        edge_threshold_multiplier=_safe_round(edge_threshold_multiplier),
        cooldown_multiplier=_safe_round(cooldown_multiplier),
        override_applied=override_applied,
    )


def govern_risk(
    runtime,
    cycle_index: int,
    snapshot: MarketStateSnapshot,
    fill_quality: FillQualitySnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
    inventory_band: InventoryBandState,
) -> RiskGovernorState:
    settings = _profile_settings(getattr(getattr(runtime, "adaptive_config", None), "profile", None))
    reasons: list[str] = []
    stage = 0
    state = "normal"
    size_multiplier = 1.0
    spread_multiplier = 1.0
    inventory_cap_multiplier = 1.0
    quote_enabled = True
    buy_enabled = True
    sell_enabled = True

    recent_equities = list(getattr(runtime, "recent_equities", []))[-14:]
    drawdown_acceleration = _drawdown_acceleration_pct(recent_equities)
    invalid_price_cycles = int(getattr(runtime, "consecutive_invalid_price_cycles", 0) or 0)
    extreme_toxic_cluster = (
        fill_quality.toxic_cluster_count >= ADAPTIVE_RISK_KILL_TOXIC_CLUSTER_COUNT
        and fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_KILL_TOXIC_FILL_RATIO
    )
    toxic_cycles = _recent_toxic_cycle_count(runtime, cycle_index, int(settings["stage4_toxic_cycles"]))
    toxic_clustered = (
        fill_quality.toxic_cluster_count >= ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT
        or (
            fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_HARD_TOXIC_FILL_RATIO
            and toxic_cycles >= int(settings["stage3_toxic_cycles"])
        )
    )
    feed_rpc_anomaly = (
        invalid_price_cycles >= max(ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES - 1, 1)
        or (snapshot.source_health_score < 0.85 and snapshot.queue_latency_quality < 0.45)
    )
    pnl_deterioration = _recent_negative_pnl_deterioration(runtime, cycle_index)
    inventory_hard_breach = inventory_band.zone == "hard_breach"
    severe_illiquidity = (
        snapshot.spread_bps >= ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS * 2.0
        or snapshot.liquidity_estimate_usd <= ADAPTIVE_RISK_KILL_LIQUIDITY_USD
    )
    weak_liquidity = (
        snapshot.liquidity_estimate_usd <= ADAPTIVE_RISK_SOFT_LIQUIDITY_USD
        or snapshot.spread_bps >= ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS * 1.35
    )

    if invalid_price_cycles >= ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES:
        reasons.append("invalid_market_data")
    if extreme_toxic_cluster:
        reasons.append("toxic_cluster_extreme")
    if severe_illiquidity and snapshot.spread_instability >= 0.40:
        reasons.append("spread_quality_collapsed")
    if snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_KILL_DRAWDOWN_PCT:
        reasons.append("extreme_drawdown")
    if edge.hard_block_reasons:
        reasons.extend(edge.hard_block_reasons)

    if reasons:
        stage = 4
        state = "hard_pause"
        size_multiplier = 0.0
        spread_multiplier = 1.35
        inventory_cap_multiplier = 0.65
        quote_enabled = False
        buy_enabled = False
        sell_enabled = False
    elif inventory_hard_breach:
        stage = 3
        state = "inventory_rebalance"
        size_multiplier = 0.58
        spread_multiplier = 1.16
        inventory_cap_multiplier = 0.72
        reasons.append("inventory_hard_cap_breach")
    elif (
        toxic_clustered
        or drawdown_acceleration >= ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT
        or feed_rpc_anomaly
    ):
        stage = 3
        state = "defensive_only"
        size_multiplier = 0.46
        spread_multiplier = 1.22
        inventory_cap_multiplier = 0.78
        reasons.append("defensive_stage_3")
        if toxic_clustered:
            reasons.append("toxic_fill_cluster")
        if drawdown_acceleration >= ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT:
            reasons.append("drawdown_acceleration_high")
        if feed_rpc_anomaly:
            reasons.append("feed_rpc_anomaly")
        if regime.regime == "CHAOS" and snapshot.toxic_fill_ratio >= 0.78:
            if inventory_band.rebalance_side == "sell":
                buy_enabled = False
            elif inventory_band.rebalance_side == "buy":
                sell_enabled = False
    elif (
        fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO
        or fill_quality.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD
        or toxic_cycles >= int(settings["stage2_toxic_cycles"])
        or snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_HARD_DRAWDOWN_PCT
        or pnl_deterioration >= max(abs(snapshot.rolling_pnl_usd) * 0.35, 1.0)
    ):
        stage = 2
        state = "strong_defense"
        size_multiplier = 0.70
        spread_multiplier = 1.12
        inventory_cap_multiplier = 0.88
        reasons.append("defensive_stage_2")
        if fill_quality.toxic_fill_ratio >= ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO:
            reasons.append("toxic_fill_ratio_elevated")
        if fill_quality.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD:
            reasons.append("adverse_selection_elevated")
        if snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_HARD_DRAWDOWN_PCT:
            reasons.append("rolling_drawdown_high")
        if pnl_deterioration >= max(abs(snapshot.rolling_pnl_usd) * 0.35, 1.0):
            reasons.append("recent_pnl_deterioration")
    elif (
        toxic_cycles >= int(settings["stage1_toxic_cycles"])
        or fill_quality.adverse_fill_ratio >= ADAPTIVE_ADVERSE_WIDEN_THRESHOLD
        or snapshot.rolling_drawdown_pct >= ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT
        or fill_quality.toxic_fill_ratio >= (ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO * 0.85)
        or weak_liquidity
        or edge.permission_state == "REDUCED"
    ):
        stage = 1
        state = "mild_defense"
        size_multiplier = 0.88
        spread_multiplier = 1.05
        inventory_cap_multiplier = 0.95
        reasons.append("defensive_stage_1")
        if weak_liquidity:
            reasons.append("liquidity_weak")

    return RiskGovernorState(
        state=state,
        stage=stage,
        size_multiplier=_safe_round(size_multiplier),
        spread_multiplier=_safe_round(spread_multiplier),
        inventory_cap_multiplier=_safe_round(inventory_cap_multiplier),
        quote_enabled=quote_enabled,
        buy_enabled=buy_enabled,
        sell_enabled=sell_enabled,
        reasons=reasons,
    )


def select_mode(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
    inventory_band: InventoryBandState,
    performance: PerformanceAdaptationState,
    risk: RiskGovernorState,
    activity_floor: ActivityFloorState,
    intelligence,
    *,
    profile: str | None = None,
) -> ModeSelection:
    del performance
    settings = _profile_settings(profile)
    permission_state = edge.permission_state
    edge_posture = _edge_posture(edge.total_score)
    confidence_score = _regime_confidence_score(regime.confidence)
    medium_confidence = confidence_score >= ADAPTIVE_REGIME_MEDIUM_CONFIDENCE
    trend_confidence = confidence_score >= ADAPTIVE_REGIME_TREND_CONFIDENCE
    extreme_event_risk = float(regime.sub_scores.get("CHAOS", 0.0) or 0.0) >= ADAPTIVE_REGIME_EXTREME_EVENT_SCORE
    severe_illiquidity = _severe_illiquidity_score(snapshot) >= ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE
    if risk.stage >= 4 or extreme_event_risk or severe_illiquidity:
        reason = "hard_pause"
        if risk.stage < 4:
            reason = "extreme_event_risk" if extreme_event_risk else "severe_illiquidity"
        return ModeSelection("standby", "NO_TRADE", False, False, False, inventory_band.target_inventory_pct, 0.0, reason)

    quote_enabled = risk.quote_enabled
    buy_enabled = bool(getattr(intelligence, "buy_enabled", True)) and risk.buy_enabled
    sell_enabled = bool(getattr(intelligence, "sell_enabled", True)) and risk.sell_enabled

    if inventory_band.zone == "hard_breach":
        if inventory_band.rebalance_side == "sell":
            return ModeSelection("rebalance_only", "OVERWEIGHT_EXIT", quote_enabled, False, sell_enabled, inventory_band.target_inventory_pct, -0.70, "inventory_hard_breach")
        return ModeSelection("rebalance_only", "RANGE_MAKER", quote_enabled, buy_enabled, False, inventory_band.target_inventory_pct, 0.70, "inventory_hard_breach")

    if permission_state == "BLOCKED":
        permission_state = "DEFENSIVE_ONLY" if edge_posture != "standby" else "REDUCED"

    if risk.stage >= 3 and permission_state in {"FULL", "CAUTIOUS", "REDUCED"}:
        permission_state = "DEFENSIVE_ONLY"
    elif risk.stage >= 1 and permission_state == "FULL":
        permission_state = "CAUTIOUS"
    if snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_DEFENSIVE_THRESHOLD and permission_state in {"FULL", "CAUTIOUS", "REDUCED"}:
        permission_state = "DEFENSIVE_ONLY"

    if regime.regime == "RANGE":
        directional_bias = 0.0 if inventory_band.zone == "neutral" else _clamp((inventory_band.target_inventory_pct - snapshot.inventory_pct) * 4.0, -0.45, 0.45)
        if permission_state == "DEFENSIVE_ONLY":
            if inventory_band.rebalance_side == "sell":
                buy_enabled = False
            elif inventory_band.rebalance_side == "buy":
                sell_enabled = False
        return ModeSelection(
            "base_mm" if permission_state in {"FULL", "CAUTIOUS"} else ("skewed_mm" if inventory_band.zone != "neutral" else "defensive_mm"),
            "RANGE_MAKER",
            quote_enabled,
            quote_enabled and buy_enabled,
            quote_enabled and sell_enabled,
            0.50 if inventory_band.zone == "neutral" else inventory_band.target_inventory_pct,
            directional_bias,
            "paper_activity_floor" if activity_floor.override_applied else "range_reversion",
        )

    if regime.regime == "TREND_UP":
        trend_conf_floor = max(regime.confidence, 0.35 if medium_confidence else 0.22)
        target_inventory_pct = _clamp(
            max(inventory_band.target_inventory_pct, 0.50 + (settings["trend_inventory_shift"] * trend_conf_floor)),
            0.50 if not medium_confidence else 0.52,
            0.68,
        )
        if permission_state == "DEFENSIVE_ONLY" and inventory_band.rebalance_side == "sell":
            buy_enabled = False
        mode_name = "trend_assist" if trend_confidence and permission_state in {"FULL", "CAUTIOUS"} else ("skewed_mm" if medium_confidence else "base_mm")
        strategy_mode = "TREND_UP" if medium_confidence else "RANGE_MAKER"
        directional_bias = (
            _clamp(max(regime.trend_bias, 0.18) + 0.08, -1.0, 1.0)
            if medium_confidence
            else _clamp(max(regime.trend_bias, 0.10) * 0.55, -0.35, 0.35)
        )
        reason = "trend_up_edge_support" if trend_confidence else ("trend_up_medium_confidence" if medium_confidence else "trend_up_low_confidence")
        return ModeSelection(
            mode_name,
            strategy_mode,
            quote_enabled,
            quote_enabled and buy_enabled,
            quote_enabled and sell_enabled,
            target_inventory_pct,
            directional_bias,
            reason,
        )

    if regime.regime == "TREND_DOWN":
        trend_conf_floor = max(regime.confidence, 0.35 if medium_confidence else 0.22)
        target_inventory_pct = _clamp(
            min(inventory_band.target_inventory_pct, 0.50 - (settings["trend_inventory_shift"] * trend_conf_floor)),
            0.32,
            0.50 if not medium_confidence else 0.48,
        )
        if permission_state == "DEFENSIVE_ONLY":
            buy_enabled = inventory_band.rebalance_side == "buy"
        mode_name = "skewed_mm" if medium_confidence and permission_state in {"FULL", "CAUTIOUS"} else "defensive_mm"
        strategy_mode = "RANGE_MAKER" if not medium_confidence else "TREND_DOWN"
        directional_bias = (
            _clamp(min(regime.trend_bias, -0.18) - 0.08, -1.0, 1.0)
            if medium_confidence
            else _clamp(min(regime.trend_bias, -0.10) * 0.55, -0.35, 0.35)
        )
        reason = "trend_down_inventory_protection" if medium_confidence else "trend_down_low_confidence"
        return ModeSelection(
            mode_name,
            strategy_mode,
            quote_enabled,
            quote_enabled and buy_enabled and inventory_band.rebalance_side != "sell",
            quote_enabled and sell_enabled,
            target_inventory_pct,
            directional_bias,
            reason,
        )

    if snapshot.toxic_fill_ratio >= 0.80 or snapshot.adverse_fill_ratio >= 0.82:
        if inventory_band.rebalance_side == "sell":
            buy_enabled = False
        elif inventory_band.rebalance_side == "buy":
            sell_enabled = False
        elif regime.trend_bias >= 0:
            sell_enabled = False
        else:
            buy_enabled = False
    if permission_state == "DEFENSIVE_ONLY":
        if inventory_band.rebalance_side == "sell":
            buy_enabled = False
        elif inventory_band.rebalance_side == "buy":
            sell_enabled = False
    return ModeSelection(
        "defensive_mm",
        "RANGE_MAKER",
        quote_enabled and (buy_enabled or sell_enabled),
        quote_enabled and buy_enabled,
        quote_enabled and sell_enabled,
        inventory_band.target_inventory_pct,
        _clamp(regime.trend_bias * 0.35, -0.40, 0.40),
        "chaos_protection",
    )


def build_aggressiveness(
    snapshot: MarketStateSnapshot,
    regime: AdaptiveRegimeAssessment,
    edge: AdaptiveEdgeAssessment,
    inventory_band: InventoryBandState,
    performance: PerformanceAdaptationState,
    risk: RiskGovernorState,
    mode: ModeSelection,
    activity_floor: ActivityFloorState,
    *,
    profile: str | None = None,
) -> AggressivenessProfile:
    del profile
    permission = edge.permission_state
    edge_posture = _edge_posture(edge.total_score)
    mode_spread = {
        "passive_mm": ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER,
        "base_mm": ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER,
        "skewed_mm": ADAPTIVE_SKEWED_MM_SPREAD_MULTIPLIER,
        "defensive_mm": ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER,
        "trend_assist": ADAPTIVE_TREND_ASSIST_SPREAD_MULTIPLIER,
        "rebalance_only": ADAPTIVE_REBALANCE_ONLY_SPREAD_MULTIPLIER,
        "standby": max(ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER, 1.28),
    }.get(mode.mode, 1.0)
    mode_size = {
        "trend_assist": ADAPTIVE_TREND_ASSIST_SIZE_MULTIPLIER,
        "rebalance_only": ADAPTIVE_REBALANCE_SIZE_MULTIPLIER,
    }.get(mode.mode, 1.0)
    posture_size = {
        "aggressive": ADAPTIVE_AGGRESSIVE_SIZE_MULTIPLIER,
        "normal": ADAPTIVE_NORMAL_SIZE_MULTIPLIER,
        "defensive": ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER,
        "standby": 0.0,
    }[edge_posture]
    if mode.mode == "defensive_mm":
        posture_size = min(posture_size, ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER)
    if permission == "REDUCED":
        posture_size *= 0.88
    elif permission == "DEFENSIVE_ONLY":
        posture_size = min(posture_size, ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER * 0.82)
    elif permission == "BLOCKED":
        posture_size = min(posture_size, ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER * 0.72)
    permission_spread = {"FULL": 0.96, "CAUTIOUS": 1.02, "REDUCED": 1.08, "DEFENSIVE_ONLY": 1.18, "BLOCKED": 1.22}.get(permission, 1.05)
    cooldown_multiplier = {"FULL": 0.88, "CAUTIOUS": 0.96, "REDUCED": 1.05, "DEFENSIVE_ONLY": 1.18, "BLOCKED": 1.30}.get(permission, 1.0)
    if mode.mode in {"base_mm", "skewed_mm"}:
        cooldown_multiplier *= 0.94
    elif mode.mode in {"defensive_mm", "standby"}:
        cooldown_multiplier *= 1.16

    size_multiplier = posture_size * mode_size * performance.size_cap_multiplier * risk.size_multiplier * activity_floor.size_multiplier
    spread_multiplier = mode_spread * permission_spread * performance.spread_baseline_multiplier * risk.spread_multiplier * activity_floor.spread_multiplier
    cooldown_multiplier *= activity_floor.cooldown_multiplier
    skew_multiplier = 1.0 + (inventory_band.inventory_pressure * (0.20 if mode.mode in {"base_mm", "skewed_mm"} else 0.16))
    if inventory_band.recovery_mode:
        skew_multiplier *= 1.10
    if abs(regime.trend_bias) >= 0.55:
        skew_multiplier *= ADAPTIVE_STRONG_TREND_SKEW_STRENGTH
    elif abs(regime.trend_bias) >= 0.18:
        skew_multiplier *= ADAPTIVE_MILD_TREND_SKEW_STRENGTH
    if mode.mode == "rebalance_only":
        skew_multiplier *= ADAPTIVE_REBALANCE_SKEW_STRENGTH

    if snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_WIDEN_THRESHOLD:
        spread_multiplier *= ADAPTIVE_ADVERSE_WIDEN_MULTIPLIER
    if snapshot.adverse_fill_ratio >= ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD:
        size_multiplier *= ADAPTIVE_ADVERSE_SIZE_REDUCE_MULTIPLIER
    if snapshot.toxic_fill_ratio > 0.0:
        spread_multiplier *= 1.0 + min(snapshot.toxic_fill_ratio * 0.18, 0.18)
        size_multiplier *= max(1.0 - min(snapshot.toxic_fill_ratio * 0.22, 0.28), 0.36)
    if mode.mode == "standby":
        size_multiplier = 0.0
        spread_multiplier = max(spread_multiplier, 1.28)
        cooldown_multiplier = max(cooldown_multiplier, 1.22)
    level = _clamp(((edge.total_score + 100.0) / 2.0) - (risk.stage * 8.0) + (8.0 if activity_floor.override_applied else 0.0), 0.0, 100.0)

    return AggressivenessProfile(
        level=_safe_round(level),
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
    profile = getattr(config, "profile", "")

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
        classify_regime(snapshot, profile=profile)
        if config.regime_enabled
        else AdaptiveRegimeAssessment("RANGE", 0.50, {"RANGE": 50.0, "TREND_UP": 0.0, "TREND_DOWN": 0.0, "CHAOS": 0.0}, reason=["adaptive_disabled"], trend_bias=0.0)
    )
    edge = (
        score_edge(snapshot, regime, cooldown_active=cooldown_active, profile=profile)
        if config.edge_enabled
        else AdaptiveEdgeAssessment(10.0, {}, {}, "weak_positive", "CAUTIOUS", [])
    )
    inventory_band = classify_inventory_band(snapshot, regime) if config.inventory_bands_enabled else InventoryBandState("neutral", 0.50)
    performance = (
        adapt_performance(runtime, cycle_index, fill_quality)
        if config.performance_adaptation_enabled
        else PerformanceAdaptationState(0, 0.0, snapshot.rolling_drawdown_pct, fill_quality.toxic_fill_ratio, 0.0, 1.0, 1.0, 1.0, 1.0)
    )
    activity_floor = assess_activity_floor(runtime, cycle_index, snapshot, profile=profile)
    risk = (
        govern_risk(runtime, cycle_index, snapshot, fill_quality, regime, edge, inventory_band)
        if config.risk_governor_enabled
        else RiskGovernorState("normal", 0, 1.0, 1.0, 1.0, True, True, True, [])
    )
    mode = (
        select_mode(snapshot, regime, edge, inventory_band, performance, risk, activity_floor, intelligence, profile=profile)
        if config.mode_selector_enabled
        else ModeSelection("passive_mm", "RANGE_MAKER", True, True, True, 0.50, 0.0, "disabled")
    )
    aggressiveness = (
        build_aggressiveness(snapshot, regime, edge, inventory_band, performance, risk, mode, activity_floor, profile=profile)
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
        activity_floor=activity_floor,
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
        "expected_edge_negative",
    }
    if edge_assessment.edge_reject_reason not in soft_reasons:
        return edge_assessment, {}
    if cycle_plan.edge.permission_state == "BLOCKED" or cycle_plan.risk.stage >= 4:
        return edge_assessment, {}

    softened_size = _clamp(
        min(edge_assessment.size_multiplier, max(cycle_plan.aggressiveness.size_multiplier * 0.88, 0.28)),
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
        edge_score=max(edge_assessment.edge_score, ((cycle_plan.edge.total_score + 100.0) / 2.0)),
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
        "trade_permission_state": cycle_plan.edge.permission_state,
    }


def apply_intelligence_overrides(runtime, intelligence, cycle_plan: AdaptiveCyclePlan | None) -> None:
    if cycle_plan is None:
        return

    runtime.current_adaptive_regime = cycle_plan.regime.regime
    runtime.current_adaptive_regime_confidence = cycle_plan.regime.confidence
    runtime.current_adaptive_edge_score = cycle_plan.edge.total_score
    runtime.current_adaptive_edge_level = _edge_total_level(cycle_plan.edge.total_score)
    runtime.current_adaptive_edge_posture = _edge_posture(cycle_plan.edge.total_score)
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
    runtime.current_activity_state = cycle_plan.activity_floor.state
    runtime.current_inactivity_cycles = cycle_plan.activity_floor.inactivity_cycles
    runtime.current_activity_floor_state = cycle_plan.activity_floor.state
    runtime.current_paper_activity_override = cycle_plan.activity_floor.override_applied
    runtime.current_defensive_stage = cycle_plan.risk.stage
    runtime.current_inventory_pressure_score = cycle_plan.inventory_band.inventory_pressure_score
    runtime.current_inventory_recovery_mode = cycle_plan.inventory_band.recovery_mode
    runtime.current_trade_permission_state = cycle_plan.edge.permission_state
    runtime.current_regime_reason = "|".join(cycle_plan.regime.reason)
    runtime.current_edge_components = cycle_plan.edge.breakdown

    intelligence.mm_mode = cycle_plan.mode.mode
    intelligence.strategy_mode = cycle_plan.mode.strategy_mode
    intelligence.quote_enabled = cycle_plan.mode.quote_enabled and cycle_plan.risk.quote_enabled
    intelligence.buy_enabled = bool(getattr(intelligence, "buy_enabled", True)) and cycle_plan.mode.buy_enabled and cycle_plan.risk.buy_enabled
    intelligence.sell_enabled = bool(getattr(intelligence, "sell_enabled", True)) and cycle_plan.mode.sell_enabled and cycle_plan.risk.sell_enabled
    intelligence.target_inventory_pct = cycle_plan.mode.target_inventory_pct
    intelligence.directional_bias = _clamp(cycle_plan.mode.directional_bias, -1.0, 1.0)
    intelligence.spread_multiplier = float(getattr(intelligence, "spread_multiplier", 1.0)) * cycle_plan.aggressiveness.spread_multiplier
    intelligence.trade_size_multiplier = float(getattr(intelligence, "trade_size_multiplier", 1.0)) * cycle_plan.aggressiveness.size_multiplier
    intelligence.cooldown_multiplier = float(getattr(intelligence, "cooldown_multiplier", 1.0)) * cycle_plan.aggressiveness.cooldown_multiplier
    intelligence.inventory_skew_multiplier = float(getattr(intelligence, "inventory_skew_multiplier", 1.0)) * cycle_plan.aggressiveness.skew_multiplier
    intelligence.max_inventory_multiplier = float(getattr(intelligence, "max_inventory_multiplier", 1.0)) * cycle_plan.risk.inventory_cap_multiplier
    intelligence.min_edge_multiplier = float(getattr(intelligence, "min_edge_multiplier", 1.0)) * (
        cycle_plan.performance.edge_threshold_multiplier * cycle_plan.activity_floor.edge_threshold_multiplier
    )


def quote_decision_filter_values(runtime, cycle_plan: AdaptiveCyclePlan | None) -> dict[str, object]:
    if cycle_plan is None:
        return {}
    return {
        "adaptive_profile": cycle_plan.config.profile,
        "adaptive_regime": cycle_plan.regime.regime,
        "adaptive_regime_confidence": _safe_round(cycle_plan.regime.confidence),
        "adaptive_regime_confidence_score": _regime_confidence_score(cycle_plan.regime.confidence),
        "regime_label": cycle_plan.regime.regime,
        "regime_confidence": _safe_round(cycle_plan.regime.confidence),
        "regime_reason": cycle_plan.regime.reason,
        "adaptive_regime_sub_scores": cycle_plan.regime.sub_scores,
        "adaptive_edge_score": _safe_round(cycle_plan.edge.total_score),
        "adaptive_edge_level": _edge_total_level(cycle_plan.edge.total_score),
        "adaptive_edge_posture": _edge_posture(cycle_plan.edge.total_score),
        "edge_score": _safe_round(cycle_plan.edge.total_score),
        "adaptive_edge_breakdown": cycle_plan.edge.breakdown,
        "adaptive_edge_penalties": cycle_plan.edge.penalties,
        "edge_components": cycle_plan.edge.breakdown,
        "trade_permission_state": cycle_plan.edge.permission_state,
        "adaptive_mode": cycle_plan.mode.mode,
        "adaptive_mode_reason": cycle_plan.mode.reason,
        "adaptive_strategy_mode": cycle_plan.mode.strategy_mode,
        "adaptive_quote_enabled": cycle_plan.mode.quote_enabled,
        "adaptive_buy_enabled": cycle_plan.mode.buy_enabled,
        "adaptive_sell_enabled": cycle_plan.mode.sell_enabled,
        "adaptive_inventory_zone": cycle_plan.inventory_band.zone,
        "adaptive_rebalance_side": cycle_plan.inventory_band.rebalance_side,
        "inventory_pressure_score": _safe_round(cycle_plan.inventory_band.inventory_pressure_score),
        "inventory_recovery_mode": cycle_plan.inventory_band.recovery_mode,
        "adaptive_target_inventory_pct": _safe_round(cycle_plan.mode.target_inventory_pct),
        "adaptive_directional_bias": _safe_round(cycle_plan.mode.directional_bias),
        "adaptive_aggressiveness": _safe_round(cycle_plan.aggressiveness.level),
        "adaptive_size_multiplier": _safe_round(cycle_plan.aggressiveness.size_multiplier),
        "adaptive_spread_multiplier": _safe_round(cycle_plan.aggressiveness.spread_multiplier),
        "adaptive_cooldown_multiplier": _safe_round(cycle_plan.aggressiveness.cooldown_multiplier),
        "adaptive_skew_multiplier": _safe_round(cycle_plan.aggressiveness.skew_multiplier),
        "adaptive_risk_state": cycle_plan.risk.state,
        "defensive_stage": cycle_plan.risk.stage,
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
        "activity_floor_state": cycle_plan.activity_floor.state,
        "inactivity_cycles": cycle_plan.activity_floor.inactivity_cycles,
        "paper_activity_override": cycle_plan.activity_floor.override_applied,
    }


def update_quote_decision_runtime(runtime, *, spread_bps: float, size_usd: float, bid: float, ask: float) -> None:
    runtime.current_quote_spread_bps = _safe_round(spread_bps)
    runtime.current_quote_size_usd = _safe_round(size_usd)
    cycle_plan = getattr(runtime, "current_adaptive_plan", None)
    spread_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.spread_multiplier
    size_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.size_multiplier
    skew_multiplier = 1.0 if cycle_plan is None else cycle_plan.aggressiveness.skew_multiplier
    defensive_stage = 0 if cycle_plan is None else cycle_plan.risk.stage
    edge_score = 0.0 if cycle_plan is None else cycle_plan.edge.total_score
    edge_posture = "-" if cycle_plan is None else _edge_posture(cycle_plan.edge.total_score)
    permission_state = "-" if cycle_plan is None else cycle_plan.edge.permission_state
    regime_label = "-" if cycle_plan is None else cycle_plan.regime.regime
    runtime.current_quote_decision = (
        f"regime={regime_label}|edge={edge_score:.1f}|stage={defensive_stage}|"
        f"spread_mult={spread_multiplier:.2f}|size_mult={size_multiplier:.2f}|"
        f"skew={skew_multiplier:.2f}|perm={permission_state}|posture={edge_posture}"
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
