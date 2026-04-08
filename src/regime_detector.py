from __future__ import annotations

from candle_features import build_price_window_features
from config import (
    ENABLE_REGIME_DETECTOR,
    REGIME_DEFAULT_REGIME,
    REGIME_LOOKBACK_CANDLES,
    REGIME_POST_SHOCK_COOLDOWN_BARS,
    REGIME_RANGE_LOOKBACK_BARS,
    REGIME_RANGE_MAX_DISTANCE_FROM_VWAP_PCT,
    REGIME_RANGE_MAX_NET_MOVE_PCT,
    REGIME_RANGE_PRIORITY_WHEN_UNCLEAR,
    REGIME_RANGE_STRUCTURE_REQUIRED,
    REGIME_SHOCK_BAR_MOVE_PCT,
    REGIME_TREND_LOOKBACK_BARS,
    REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT,
    REGIME_TREND_MIN_NET_MOVE_PCT,
    REGIME_TREND_MIN_SAME_DIRECTION_BARS,
    REGIME_TREND_MOMENTUM_ZSCORE_MIN,
    REGIME_TREND_OVERRIDE_ONLY_IF_STRONG,
    REGIME_MAX_WICK_TO_BODY_RATIO,
)
from metrics_window import clamp
from strategy_profile import resolve_range_location
from types_bot import MarketRegimeAssessment


def _trailing_window(values: list[float], size: int) -> list[float]:
    return values[-max(size, 2):]


def _price_changes_pct(values: list[float]) -> list[float]:
    changes: list[float] = []
    for index in range(1, len(values)):
        previous = values[index - 1]
        current = values[index]
        if previous <= 0:
            continue
        changes.append(((current / previous) - 1.0) * 100.0)
    return changes


def _same_direction_bars(values: list[float], lookback: int) -> int:
    window = _trailing_window(values, lookback)
    changes = _price_changes_pct(window)
    if not changes:
        return 0

    net_move = window[-1] - window[0]
    if net_move >= 0:
        return sum(1 for change in changes if change > 0)
    return sum(1 for change in changes if change < 0)


def _momentum_zscore(values: list[float], lookback: int) -> float:
    window = _trailing_window(values, lookback)
    changes = _price_changes_pct(window)
    if len(changes) < 4:
        return 0.0

    recent_span = min(3, len(changes) - 1)
    recent_mean = sum(changes[-recent_span:]) / recent_span
    baseline = changes[:-recent_span]
    mean = sum(baseline) / len(baseline)
    variance = sum((change - mean) ** 2 for change in baseline) / len(baseline)
    stddev = variance ** 0.5
    if stddev <= 1e-9:
        return 0.0
    return (recent_mean - mean) / stddev


def _recent_shock_active(values: list[float], *, threshold_pct: float, cooldown_bars: int) -> bool:
    if cooldown_bars <= 0 or threshold_pct <= 0:
        return False
    changes = _price_changes_pct(values)
    if not changes:
        return False
    recent_changes = changes[-(cooldown_bars + 1):]
    return any(abs(change) >= threshold_pct for change in recent_changes)


def _wick_to_body_ratio(features) -> float:
    if features.body_to_wick_ratio <= 1e-9:
        return float("inf")
    return 1.0 / features.body_to_wick_ratio


def _trend_confidence(features, *, same_direction_bars: int, momentum_zscore: float, shock_active: bool) -> float:
    move_component = min(abs(features.net_move_pct) / max(REGIME_TREND_MIN_NET_MOVE_PCT, 0.01), 2.0) * 28.0
    direction_component = min(same_direction_bars / max(REGIME_TREND_MIN_SAME_DIRECTION_BARS, 1), 1.5) * 22.0
    distance_component = (
        min(abs(features.mean_reversion_distance_pct) / max(REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT, 0.01), 2.0) * 18.0
    )
    momentum_component = (
        min(abs(momentum_zscore) / max(REGIME_TREND_MOMENTUM_ZSCORE_MIN, 0.10), 2.0) * 18.0
    )
    structure_component = clamp(abs(features.price_position_pct - 0.5) * 2.0, 0.0, 1.0) * 10.0
    noise_penalty = clamp(features.sign_flip_ratio, 0.0, 1.0) * 18.0
    wick_penalty = max(_wick_to_body_ratio(features) - REGIME_MAX_WICK_TO_BODY_RATIO, 0.0) * 6.0
    shock_penalty = 18.0 if shock_active else 0.0
    return clamp(
        16.0
        + move_component
        + direction_component
        + distance_component
        + momentum_component
        + structure_component
        - noise_penalty
        - wick_penalty
        - shock_penalty,
        0.0,
        100.0,
    )


def _range_confidence(features, *, shock_active: bool) -> float:
    move_component = max(
        1.0 - (abs(features.net_move_pct) / max(REGIME_RANGE_MAX_NET_MOVE_PCT, 0.01)),
        0.0,
    ) * 30.0
    distance_component = max(
        1.0 - (abs(features.mean_reversion_distance_pct) / max(REGIME_RANGE_MAX_DISTANCE_FROM_VWAP_PCT, 0.01)),
        0.0,
    ) * 24.0
    structure_component = min(features.bounce_count, 4) * 7.0
    touch_component = min(features.range_touch_count, 6) * 4.0
    balance_component = max(1.0 - abs(features.price_position_pct - 0.5) * 1.8, 0.0) * 10.0
    noise_component = clamp(features.sign_flip_ratio, 0.0, 1.0) * 14.0
    shock_penalty = 18.0 if shock_active else 0.0
    return clamp(
        14.0
        + move_component
        + distance_component
        + structure_component
        + touch_component
        + balance_component
        + noise_component
        - shock_penalty,
        0.0,
        100.0,
    )


def _chop_confidence(features) -> float:
    return clamp(
        18.0
        + (clamp(features.sign_flip_ratio, 0.0, 1.0) * 32.0)
        + (max(features.noise_ratio - 1.0, 0.0) * 10.0)
        + (max(_wick_to_body_ratio(features) - 1.0, 0.0) * 8.0),
        0.0,
        100.0,
    )


class RegimeDetector:
    def __init__(self, *, enabled: bool = ENABLE_REGIME_DETECTOR, lookback_candles: int = REGIME_LOOKBACK_CANDLES) -> None:
        self.enabled = enabled
        self.lookback_candles = max(int(lookback_candles), 6)
        self.trend_lookback_bars = max(int(REGIME_TREND_LOOKBACK_BARS or lookback_candles), 6)
        self.range_lookback_bars = max(int(REGIME_RANGE_LOOKBACK_BARS or lookback_candles), 6)

    def assess(self, prices: list[float]) -> MarketRegimeAssessment:
        trend_features = build_price_window_features(prices, self.trend_lookback_bars)
        range_features = build_price_window_features(prices, self.range_lookback_bars)
        same_direction_bars = _same_direction_bars(prices, self.trend_lookback_bars)
        momentum_zscore = _momentum_zscore(prices, self.trend_lookback_bars)
        shock_active = _recent_shock_active(
            prices,
            threshold_pct=REGIME_SHOCK_BAR_MOVE_PCT,
            cooldown_bars=REGIME_POST_SHOCK_COOLDOWN_BARS,
        )

        default_regime = REGIME_DEFAULT_REGIME if REGIME_DEFAULT_REGIME in {"RANGE", "CHOP"} else "RANGE"
        default_execution_regime = "RANGE"
        default_features = range_features if default_regime == "RANGE" else trend_features

        if not self.enabled:
            return MarketRegimeAssessment(
                market_regime=default_regime,
                regime_confidence=0.0,
                range_width_pct=default_features.range_width_pct,
                net_move_pct=default_features.net_move_pct,
                direction_consistency=default_features.direction_consistency,
                volatility_score=default_features.volatility_score,
                execution_regime=default_execution_regime,
                trend_direction="neutral",
                range_location=resolve_range_location(default_features.price_position_pct),
                bounce_count=default_features.bounce_count,
                range_touch_count=default_features.range_touch_count,
                sign_flip_ratio=default_features.sign_flip_ratio,
                noise_ratio=default_features.noise_ratio,
                body_to_wick_ratio=default_features.body_to_wick_ratio,
                ema_deviation_pct=default_features.ema_deviation_pct,
                mean_reversion_distance_pct=default_features.mean_reversion_distance_pct,
                window_high=default_features.window_high,
                window_low=default_features.window_low,
                window_mean=default_features.window_mean,
                price_position_pct=default_features.price_position_pct,
                shock_active=shock_active,
            )

        strong_directional_trend = (
            abs(trend_features.net_move_pct) >= (REGIME_TREND_MIN_NET_MOVE_PCT * 2.0)
            and trend_features.direction_consistency >= 0.85
        )
        trend_common = (
            same_direction_bars >= max(REGIME_TREND_MIN_SAME_DIRECTION_BARS, 1)
            and (
                abs(momentum_zscore) >= max(REGIME_TREND_MOMENTUM_ZSCORE_MIN, 0.0)
                or strong_directional_trend
            )
            and _wick_to_body_ratio(trend_features) <= max(REGIME_MAX_WICK_TO_BODY_RATIO, 0.1)
            and not shock_active
        )
        is_trend_up = (
            trend_common
            and trend_features.net_move_pct >= REGIME_TREND_MIN_NET_MOVE_PCT
            and trend_features.mean_reversion_distance_pct >= REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT
            and trend_features.price_position_pct >= 0.58
        )
        is_trend_down = (
            trend_common
            and trend_features.net_move_pct <= -REGIME_TREND_MIN_NET_MOVE_PCT
            and trend_features.mean_reversion_distance_pct <= -REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT
            and trend_features.price_position_pct <= 0.42
        )
        range_structure_ok = (
            (range_features.bounce_count >= 2 and range_features.range_touch_count >= 4)
            if REGIME_RANGE_STRUCTURE_REQUIRED
            else True
        )
        is_range = (
            not shock_active
            and abs(range_features.net_move_pct) <= REGIME_RANGE_MAX_NET_MOVE_PCT
            and abs(range_features.mean_reversion_distance_pct) <= REGIME_RANGE_MAX_DISTANCE_FROM_VWAP_PCT
            and _wick_to_body_ratio(range_features) <= max(REGIME_MAX_WICK_TO_BODY_RATIO, 0.1)
            and range_structure_ok
        )
        is_chop = (
            shock_active
            or (
                abs(range_features.net_move_pct) <= max(REGIME_RANGE_MAX_NET_MOVE_PCT * 0.55, 0.05)
                and range_features.sign_flip_ratio >= 0.40
                and range_features.noise_ratio >= 1.8
            )
        )

        trend_confidence = _trend_confidence(
            trend_features,
            same_direction_bars=same_direction_bars,
            momentum_zscore=momentum_zscore,
            shock_active=shock_active,
        )
        range_confidence = _range_confidence(range_features, shock_active=shock_active)
        chop_confidence = _chop_confidence(range_features)

        market_regime = default_regime
        confidence = range_confidence if market_regime == "RANGE" else chop_confidence

        if is_chop:
            market_regime = "CHOP"
            confidence = max(chop_confidence, 68.0 if shock_active else chop_confidence)
        elif is_trend_up and is_range:
            if REGIME_TREND_OVERRIDE_ONLY_IF_STRONG and trend_confidence >= (range_confidence + 10.0):
                market_regime = "TREND_UP"
                confidence = trend_confidence
            elif REGIME_RANGE_PRIORITY_WHEN_UNCLEAR:
                market_regime = "RANGE"
                confidence = range_confidence
            else:
                market_regime = "TREND_UP" if trend_confidence >= range_confidence else "RANGE"
                confidence = max(trend_confidence, range_confidence)
        elif is_trend_down and is_range:
            if REGIME_TREND_OVERRIDE_ONLY_IF_STRONG and trend_confidence >= (range_confidence + 10.0):
                market_regime = "TREND_DOWN"
                confidence = trend_confidence
            elif REGIME_RANGE_PRIORITY_WHEN_UNCLEAR:
                market_regime = "RANGE"
                confidence = range_confidence
            else:
                market_regime = "TREND_DOWN" if trend_confidence >= range_confidence else "RANGE"
                confidence = max(trend_confidence, range_confidence)
        elif is_trend_up:
            market_regime = "TREND_UP"
            confidence = trend_confidence
        elif is_trend_down:
            market_regime = "TREND_DOWN"
            confidence = trend_confidence
        elif is_range:
            market_regime = "RANGE"
            confidence = range_confidence
        elif REGIME_RANGE_PRIORITY_WHEN_UNCLEAR:
            market_regime = "RANGE"
            confidence = max(range_confidence * 0.85, 42.0)
        elif trend_features.net_move_pct > 0:
            market_regime = "TREND_UP"
            confidence = trend_confidence * 0.75
        elif trend_features.net_move_pct < 0:
            market_regime = "TREND_DOWN"
            confidence = trend_confidence * 0.75

        execution_regime = "RANGE"
        trend_direction = "neutral"
        if market_regime == "TREND_UP":
            execution_regime = "TREND"
            trend_direction = "up"
        elif market_regime == "TREND_DOWN":
            execution_regime = "TREND"
            trend_direction = "down"
        elif market_regime == "CHOP":
            execution_regime = "RANGE"

        active_features = range_features if market_regime == "RANGE" else trend_features
        if market_regime == "CHOP":
            active_features = range_features

        return MarketRegimeAssessment(
            market_regime=market_regime,
            regime_confidence=round(confidence, 6),
            range_width_pct=round(active_features.range_width_pct, 6),
            net_move_pct=round(active_features.net_move_pct, 6),
            direction_consistency=round(active_features.direction_consistency, 6),
            volatility_score=round(max(trend_features.volatility_score, range_features.volatility_score), 6),
            execution_regime=execution_regime,
            trend_direction=trend_direction,
            range_location=resolve_range_location(active_features.price_position_pct),
            bounce_count=active_features.bounce_count,
            range_touch_count=active_features.range_touch_count,
            sign_flip_ratio=round(active_features.sign_flip_ratio, 6),
            noise_ratio=round(active_features.noise_ratio, 6),
            body_to_wick_ratio=round(active_features.body_to_wick_ratio, 6),
            ema_deviation_pct=round(active_features.ema_deviation_pct, 6),
            mean_reversion_distance_pct=round(active_features.mean_reversion_distance_pct, 6),
            window_high=round(active_features.window_high, 6),
            window_low=round(active_features.window_low, 6),
            window_mean=round(active_features.window_mean, 6),
            price_position_pct=round(active_features.price_position_pct, 6),
            shock_active=shock_active,
        )
