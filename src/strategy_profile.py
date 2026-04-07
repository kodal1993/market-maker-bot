from __future__ import annotations

from config import (
    ALLOW_MICRO_EDGE_ENTRIES,
    ACTIVITY_AUTO_LOOSEN_ENTRY_BPS,
    ACTIVITY_AUTO_LOOSEN_MIN_EDGE_BPS,
    EXECUTION_BASE_ENTRY_THRESHOLD_BPS,
    INACTIVITY_FORCE_ENTRY_THRESHOLD_BPS,
    INACTIVITY_FORCE_MIN_EDGE_BPS,
    MICRO_EDGE_MIN_BPS,
    RANGE_ENTRY_THRESHOLD_BPS,
    RANGE_MAX_HOLD_MINUTES,
    RANGE_MID_NO_TRADE_ZONE_PCT,
    RANGE_MIN_EDGE_BPS,
    RANGE_SOFT_TAKE_PROFIT_BPS,
    RANGE_TAKE_PROFIT_BPS,
    RANGE_TIME_STOP_MINUTES,
    RANGE_TOP_ZONE_PCT,
    RANGE_BOTTOM_ZONE_PCT,
    TREND_ENTRY_THRESHOLD_BPS,
    TREND_MAX_HOLD_MINUTES,
    TREND_MIN_EDGE_BPS,
    TREND_SOFT_TAKE_PROFIT_BPS,
    TREND_TAKE_PROFIT_BPS,
    TREND_TIME_STOP_MINUTES,
    VOLATILITY_HIGH_VOL_ENTRY_THRESHOLD_BPS,
    VOLATILITY_HIGH_VOL_MAX_HOLD_MINUTES,
    VOLATILITY_HIGH_VOL_TAKE_PROFIT_BPS,
    VOLATILITY_LOW_VOL_ENTRY_THRESHOLD_BPS,
    VOLATILITY_LOW_VOL_MAX_HOLD_MINUTES,
    VOLATILITY_LOW_VOL_TAKE_PROFIT_BPS,
    VOLATILITY_MID_VOL_ENTRY_THRESHOLD_BPS,
    VOLATILITY_MID_VOL_MAX_HOLD_MINUTES,
    VOLATILITY_MID_VOL_TAKE_PROFIT_BPS,
)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _normalized_regime(active_regime: str) -> str:
    normalized = str(active_regime).upper()
    return "TREND" if "TREND" in normalized else "RANGE"


def _normalized_volatility_bucket(volatility_bucket: str) -> str:
    bucket = str(volatility_bucket).upper()
    if bucket == "LOW":
        return "LOW"
    if bucket in {"HIGH", "EXTREME"}:
        return "HIGH"
    return "MID"


def _normalized_activity_state(activity_state: str) -> str:
    return str(activity_state).strip().lower()


def resolve_range_location(price_position_pct: float) -> str:
    normalized = _clamp(price_position_pct, 0.0, 1.0)
    bottom_zone = _clamp(RANGE_BOTTOM_ZONE_PCT, 0.0, 0.49)
    top_zone_start = 1.0 - _clamp(RANGE_TOP_ZONE_PCT, 0.0, 0.49)
    mid_half_width = _clamp(RANGE_MID_NO_TRADE_ZONE_PCT, 0.0, 0.80) / 2.0
    mid_low = 0.5 - mid_half_width
    mid_high = 0.5 + mid_half_width

    if normalized <= bottom_zone:
        return "bottom"
    if normalized >= top_zone_start:
        return "top"
    if normalized < mid_low:
        return "lower"
    if normalized > mid_high:
        return "upper"
    return "middle"


def resolve_logging_zone(range_location: str) -> str:
    location = str(range_location).strip().lower()
    if location == "top":
        return "top"
    if location == "bottom":
        return "bottom"
    return "mid"


def resolve_entry_threshold_bps(active_regime: str, volatility_bucket: str) -> float:
    regime = _normalized_regime(active_regime)
    bucket = _normalized_volatility_bucket(volatility_bucket)
    base_threshold = RANGE_ENTRY_THRESHOLD_BPS if regime == "RANGE" else TREND_ENTRY_THRESHOLD_BPS
    volatility_threshold = {
        "LOW": VOLATILITY_LOW_VOL_ENTRY_THRESHOLD_BPS,
        "MID": VOLATILITY_MID_VOL_ENTRY_THRESHOLD_BPS,
        "HIGH": VOLATILITY_HIGH_VOL_ENTRY_THRESHOLD_BPS,
    }[bucket]
    volatility_delta = volatility_threshold - VOLATILITY_MID_VOL_ENTRY_THRESHOLD_BPS
    return max(base_threshold + volatility_delta, 0.0)


def resolve_entry_threshold_multiplier(active_regime: str, volatility_bucket: str) -> float:
    threshold_bps = resolve_entry_threshold_bps(active_regime, volatility_bucket)
    return max(threshold_bps / max(EXECUTION_BASE_ENTRY_THRESHOLD_BPS, 1.0), 0.35)


def resolve_low_activity_entry_factor(active_regime: str, volatility_bucket: str) -> float:
    threshold_bps = resolve_entry_threshold_bps(active_regime, volatility_bucket)
    loosen_bps = min(ACTIVITY_AUTO_LOOSEN_ENTRY_BPS, threshold_bps * 0.50)
    return _clamp((threshold_bps - loosen_bps) / max(threshold_bps, 1.0), 0.50, 1.0)


def resolve_effective_entry_threshold_bps(
    active_regime: str,
    volatility_bucket: str,
    activity_state: str = "normal",
) -> float:
    threshold_bps = resolve_entry_threshold_bps(active_regime, volatility_bucket)
    normalized_activity_state = _normalized_activity_state(activity_state)
    if normalized_activity_state == "inactivity_fallback" and _normalized_regime(active_regime) == "RANGE":
        threshold_bps = min(threshold_bps, max(INACTIVITY_FORCE_ENTRY_THRESHOLD_BPS, 0.0))
    elif normalized_activity_state == "low_activity_relax":
        threshold_bps *= resolve_low_activity_entry_factor(active_regime, volatility_bucket)
    return max(threshold_bps, 0.0)


def resolve_min_edge_bps(active_regime: str) -> float:
    return RANGE_MIN_EDGE_BPS if _normalized_regime(active_regime) == "RANGE" else TREND_MIN_EDGE_BPS


def resolve_min_edge_multiplier(active_regime: str) -> float:
    required_edge_bps = resolve_min_edge_bps(active_regime)
    return max(required_edge_bps / max(RANGE_MIN_EDGE_BPS, 0.5), 0.35)


def resolve_low_activity_edge_factor(active_regime: str) -> float:
    required_edge_bps = resolve_min_edge_bps(active_regime)
    loosen_bps = min(ACTIVITY_AUTO_LOOSEN_MIN_EDGE_BPS, required_edge_bps * 0.50)
    return _clamp((required_edge_bps - loosen_bps) / max(required_edge_bps, 1.0), 0.50, 1.0)


def resolve_effective_min_edge_bps(active_regime: str, activity_state: str = "normal") -> float:
    required_edge_bps = resolve_min_edge_bps(active_regime)
    normalized_activity_state = _normalized_activity_state(activity_state)
    if normalized_activity_state == "inactivity_fallback" and _normalized_regime(active_regime) == "RANGE":
        required_edge_bps = min(required_edge_bps, max(INACTIVITY_FORCE_MIN_EDGE_BPS, 0.0))
    elif normalized_activity_state == "low_activity_relax":
        required_edge_bps *= resolve_low_activity_edge_factor(active_regime)
    if _normalized_regime(active_regime) == "RANGE" and ALLOW_MICRO_EDGE_ENTRIES:
        required_edge_bps = min(required_edge_bps, max(MICRO_EDGE_MIN_BPS, 0.0))
    return max(required_edge_bps, 0.0)


def resolve_take_profit_targets_bps(active_regime: str, volatility_bucket: str) -> tuple[float, float]:
    regime = _normalized_regime(active_regime)
    bucket = _normalized_volatility_bucket(volatility_bucket)
    soft_base = RANGE_SOFT_TAKE_PROFIT_BPS if regime == "RANGE" else TREND_SOFT_TAKE_PROFIT_BPS
    hard_base = RANGE_TAKE_PROFIT_BPS if regime == "RANGE" else TREND_TAKE_PROFIT_BPS
    volatility_target = {
        "LOW": VOLATILITY_LOW_VOL_TAKE_PROFIT_BPS,
        "MID": VOLATILITY_MID_VOL_TAKE_PROFIT_BPS,
        "HIGH": VOLATILITY_HIGH_VOL_TAKE_PROFIT_BPS,
    }[bucket]
    volatility_delta = volatility_target - VOLATILITY_MID_VOL_TAKE_PROFIT_BPS
    soft_target = max(soft_base + (volatility_delta * 0.50), 1.0)
    hard_target = max(hard_base + volatility_delta, soft_target)
    return soft_target, hard_target


def resolve_hold_limits_minutes(active_regime: str, volatility_bucket: str) -> tuple[float, float]:
    regime = _normalized_regime(active_regime)
    bucket = _normalized_volatility_bucket(volatility_bucket)
    base_time_stop = RANGE_TIME_STOP_MINUTES if regime == "RANGE" else TREND_TIME_STOP_MINUTES
    base_max_hold = RANGE_MAX_HOLD_MINUTES if regime == "RANGE" else TREND_MAX_HOLD_MINUTES
    bucket_max_hold = {
        "LOW": VOLATILITY_LOW_VOL_MAX_HOLD_MINUTES,
        "MID": VOLATILITY_MID_VOL_MAX_HOLD_MINUTES,
        "HIGH": VOLATILITY_HIGH_VOL_MAX_HOLD_MINUTES,
    }[bucket]
    hold_delta = bucket_max_hold - VOLATILITY_MID_VOL_MAX_HOLD_MINUTES
    max_hold_minutes = max(base_max_hold + hold_delta, 1.0)
    time_stop_minutes = min(max(base_time_stop + (hold_delta * 0.50), 1.0), max_hold_minutes)
    return time_stop_minutes, max_hold_minutes
