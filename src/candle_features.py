from __future__ import annotations

from dataclasses import dataclass

from metrics_window import (
    bounded_ratio,
    clamp,
    direction_consistency,
    ema,
    mean,
    normalized_position,
    path_distance_pct,
    pct_change,
    pct_changes,
    percentile_band,
    realized_volatility_pct,
    safe_range,
    sign_flip_ratio,
    trailing_window,
)


@dataclass(frozen=True)
class PriceWindowFeatures:
    window_size: int
    range_width_pct: float
    net_move_pct: float
    direction_consistency: float
    realized_volatility_pct: float
    volatility_score: float
    bounce_count: int
    range_touch_count: int
    sign_flip_ratio: float
    noise_ratio: float
    body_to_wick_ratio: float
    ema_deviation_pct: float
    mean_reversion_distance_pct: float
    window_high: float
    window_low: float
    window_mean: float
    price_position_pct: float


def _count_range_behaviour(values: list[float], low: float, high: float) -> tuple[int, int]:
    width = max(high - low, 1e-9)
    lower_touch = percentile_band(low, high, 0.15)
    upper_touch = percentile_band(low, high, 0.85)
    last_touch_side = ""
    touch_count = 0
    bounce_count = 0

    for price in values:
        side = ""
        if price <= lower_touch:
            side = "lower"
        elif price >= upper_touch:
            side = "upper"

        if not side:
            continue

        touch_count += 1
        if last_touch_side and side != last_touch_side:
            bounce_count += 1
        last_touch_side = side

    if width <= 0:
        return 0, 0
    return bounce_count, touch_count


def build_price_window_features(prices: list[float], lookback: int) -> PriceWindowFeatures:
    window = trailing_window(prices, max(lookback, 2))
    if len(window) < 2:
        return PriceWindowFeatures(
            window_size=len(window),
            range_width_pct=0.0,
            net_move_pct=0.0,
            direction_consistency=0.0,
            realized_volatility_pct=0.0,
            volatility_score=0.0,
            bounce_count=0,
            range_touch_count=0,
            sign_flip_ratio=0.0,
            noise_ratio=0.0,
            body_to_wick_ratio=0.0,
            ema_deviation_pct=0.0,
            mean_reversion_distance_pct=0.0,
            window_high=window[0] if window else 0.0,
            window_low=window[0] if window else 0.0,
            window_mean=window[0] if window else 0.0,
            price_position_pct=0.5,
        )

    window_low, window_high = safe_range(window)
    window_mean = mean(window)
    width_pct = pct_change(window_low if window_low > 0 else window[0], window_high) if window_high > 0 else 0.0
    net_move_pct = pct_change(window[0], window[-1])
    changes = pct_changes(window)
    realized_vol_pct = realized_volatility_pct(window)
    consistency = direction_consistency(changes)
    flip_ratio = sign_flip_ratio(changes)
    path_pct = path_distance_pct(window)
    noise_ratio = bounded_ratio(path_pct, abs(net_move_pct), fallback=path_pct)
    body_to_wick_ratio = bounded_ratio(abs(net_move_pct), max(width_pct, 0.01), fallback=0.0)

    short_span = max(min(len(window) // 4, 6), 3)
    long_span = max(min(len(window) // 2, 12), short_span + 1)
    short_ema = ema(window, short_span)
    long_ema = ema(window, long_span)
    ema_deviation_pct = pct_change(long_ema, short_ema) if long_ema > 0 else 0.0
    mean_reversion_distance_pct = pct_change(window_mean, window[-1]) if window_mean > 0 else 0.0
    price_position_pct = normalized_position(window[-1], window_low, window_high)
    bounce_count, touch_count = _count_range_behaviour(window, window_low, window_high)

    volatility_score = clamp(
        (realized_vol_pct * 18.0) + (flip_ratio * 35.0) + max(noise_ratio - 1.0, 0.0) * 8.0,
        0.0,
        100.0,
    )

    return PriceWindowFeatures(
        window_size=len(window),
        range_width_pct=max(width_pct, 0.0),
        net_move_pct=net_move_pct,
        direction_consistency=consistency,
        realized_volatility_pct=realized_vol_pct,
        volatility_score=volatility_score,
        bounce_count=bounce_count,
        range_touch_count=touch_count,
        sign_flip_ratio=flip_ratio,
        noise_ratio=noise_ratio,
        body_to_wick_ratio=body_to_wick_ratio,
        ema_deviation_pct=ema_deviation_pct,
        mean_reversion_distance_pct=mean_reversion_distance_pct,
        window_high=window_high,
        window_low=window_low,
        window_mean=window_mean,
        price_position_pct=price_position_pct,
    )
