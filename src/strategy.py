from config import (
    HIGH_VOL_THRESHOLD,
    LOW_VOL_THRESHOLD_MULTIPLIER,
    SPREAD_BPS,
    MIN_SPREAD_BPS,
    MAX_SPREAD_BPS,
    RANGE_SIZE_MULTIPLIER,
    VOL_WINDOW,
    VOL_MULTIPLIER,
    TREND_THRESHOLD,
    SHORT_MA_WINDOW,
    LONG_MA_WINDOW,
    TREND_SIZE_MULTIPLIER,
    TREND_OVERWEIGHT_UNWIND_FRACTION,
    TREND_OVERWEIGHT_MAX_MULTIPLIER,
)
from types_bot import Quote


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def price_distance_bps(reference_price: float, current_price: float) -> float:
    if reference_price <= 0:
        return 0.0
    return ((current_price / reference_price) - 1.0) * 10000.0


def sma(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    if len(values) < window:
        return sum(values) / len(values)
    subset = values[-window:]
    return sum(subset) / len(subset)


def calculate_rsi(values: list[float], period: int = 14) -> float:
    if period <= 0 or len(values) <= period:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    for index in range(len(values) - period, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def detect_momentum_slowing(values: list[float], lookback: int = 4) -> bool:
    required_points = max(lookback + 2, 6)
    if len(values) < required_points:
        return False

    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = deltas[-2:]
    prior = deltas[-(lookback + 2):-2]
    if not prior:
        return False

    recent_down = [abs(delta) for delta in recent if delta < 0]
    prior_down = [abs(delta) for delta in prior if delta < 0]
    if not prior_down:
        return recent[-1] >= 0 and recent[-1] > recent[-2]

    prior_avg_down = sum(prior_down) / len(prior_down)
    recent_avg_down = (sum(recent_down) / len(recent_down)) if recent_down else 0.0
    improving = recent[-1] > prior[-1]
    return improving and recent_avg_down <= (prior_avg_down * 0.8)


def calculate_buy_zones(
    last_sell_price: float,
    zone_multipliers: tuple[float, float, float],
) -> tuple[float, float, float]:
    if last_sell_price <= 0:
        return (0.0, 0.0, 0.0)

    return tuple(last_sell_price * multiplier for multiplier in zone_multipliers)


def should_micro_trail_sell(high_price: float | None, current_price: float, pullback_bps: float) -> bool:
    if high_price is None or high_price <= 0 or current_price <= 0:
        return False

    pullback_multiplier = max(pullback_bps, 0.0) / 10000.0
    if pullback_multiplier <= 0:
        return current_price <= high_price
    return current_price <= high_price * (1.0 - pullback_multiplier)


def detect_market_mode(prices: list[float]) -> tuple[str, float, float, float]:
    if len(prices) < LONG_MA_WINDOW:
        return "NO_TRADE", 0.0, 0.0, 0.0

    short_ma = sma(prices, SHORT_MA_WINDOW)
    long_ma = sma(prices, LONG_MA_WINDOW)

    returns = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        curr = prices[i]
        if prev != 0:
            returns.append((curr - prev) / prev)

    if not returns:
        volatility = 0.0
    else:
        recent = returns[-VOL_WINDOW:]
        mean = sum(recent) / len(recent)
        variance = sum((r - mean) ** 2 for r in recent) / len(recent)
        volatility = variance ** 0.5

    trend = (short_ma - long_ma) / long_ma if long_ma != 0 else 0.0

    if trend > TREND_THRESHOLD:
        return "TREND_UP", short_ma, long_ma, volatility

    return "NO_TRADE", short_ma, long_ma, volatility


def calculate_spread(volatility: float, spread_multiplier: float = 1.0) -> float:
    normalized_volatility = max(volatility, 0.0)
    base_threshold = max(HIGH_VOL_THRESHOLD, 1e-9)
    low_threshold = max(base_threshold * LOW_VOL_THRESHOLD_MULTIPLIER, 0.0)
    spread_strength = max(VOL_MULTIPLIER, 0.0)

    # Low volatility narrows spreads, higher volatility widens them progressively.
    low_narrowing = min(0.30, 0.14 + (spread_strength * 0.18))
    mid_widening = min(0.35, 0.12 + (spread_strength * 0.22))
    high_widening = min(0.95, 0.35 + (spread_strength * 0.60))

    volatility_scale = 1.0
    if low_threshold > 0 and normalized_volatility <= low_threshold:
        low_ratio = normalized_volatility / low_threshold
        volatility_scale = 1.0 - ((1.0 - low_ratio) * low_narrowing)
    elif normalized_volatility < base_threshold:
        mid_range = max(base_threshold - low_threshold, 1e-9)
        mid_ratio = (normalized_volatility - low_threshold) / mid_range
        volatility_scale = 1.0 + (mid_ratio * mid_widening)
    else:
        extra_ratio = min((normalized_volatility - base_threshold) / base_threshold, 2.0)
        volatility_scale = 1.0 + mid_widening + (extra_ratio * high_widening)

    spread = SPREAD_BPS * volatility_scale
    spread *= max(spread_multiplier, 0.0)
    return clamp(spread, MIN_SPREAD_BPS, MAX_SPREAD_BPS)


def choose_mode(
    trend_mode: str,
    inventory_usd: float,
    max_inventory_usd: float,
    mid: float,
    min_sell_price: float | None,
    max_exit_premium_bps: float,
) -> str:
    if trend_mode == "TREND_UP":
        return trend_mode

    if max_inventory_usd <= 0 or inventory_usd <= max_inventory_usd:
        return trend_mode

    if min_sell_price is None:
        return trend_mode

    max_workable_sell_price = mid * (1.0 + (max_exit_premium_bps / 10000.0))
    if min_sell_price <= max_workable_sell_price:
        return "OVERWEIGHT_EXIT"

    return trend_mode


def should_place_trend_buy(
    mid: float,
    short_ma: float,
    long_ma: float,
    trend_strength: float,
    market_score: float,
    signal_score: float,
    confidence: float,
    inventory_usd: float,
    equity_usd: float,
    max_inventory_usd: float,
    trend_buy_target_pct: float,
    max_trend_chase_bps: float,
    max_trend_pullback_bps: float,
    trend_buy_min_market_score: float,
    trend_buy_min_signal_score: float,
    trend_buy_min_confidence: float,
    trend_buy_min_long_buffer_bps: float,
    trend_buy_min_strength_multiplier: float,
) -> bool:
    if short_ma <= 0 or long_ma <= 0:
        return False

    if equity_usd > 0:
        target_inventory = equity_usd * clamp(trend_buy_target_pct, 0.0, 1.0)
        if max_inventory_usd > 0:
            target_inventory = min(target_inventory, max_inventory_usd)
        if inventory_usd >= target_inventory:
            return False

    min_trend_strength = TREND_THRESHOLD * max(trend_buy_min_strength_multiplier, 0.0)
    if trend_strength < min_trend_strength:
        return False

    if market_score < trend_buy_min_market_score or signal_score < trend_buy_min_signal_score:
        return False
    if trend_buy_min_confidence > 0 and confidence < trend_buy_min_confidence:
        return False

    premium_to_long_bps = price_distance_bps(long_ma, mid)
    if premium_to_long_bps < trend_buy_min_long_buffer_bps:
        return False

    premium_to_short_bps = price_distance_bps(short_ma, mid)
    if premium_to_short_bps > max_trend_chase_bps:
        return False
    if premium_to_short_bps < -max(max_trend_pullback_bps, 0.0):
        return False
    return True


def requote_trend_buy_price(current_bid: float, mid: float, trend_buy_requote_bps: float) -> float:
    if mid <= 0:
        return current_bid

    tighter_bid = mid * (1.0 - (max(trend_buy_requote_bps, 0.0) / 10000.0))
    return min(mid, max(current_bid, tighter_bid))


def requote_trend_sell_price(current_ask: float, mid: float, trend_sell_spread_factor: float) -> float:
    if mid <= 0:
        return current_ask

    distance = max(current_ask - mid, 0.0)
    wider_ask = mid + (distance * max(trend_sell_spread_factor, 1.0))
    return max(current_ask, wider_ask)


def build_quotes(
    mid: float,
    spread_bps: float,
    inventory_usd: float,
    max_inventory_usd: float,
    inventory_skew_strength: float,
    directional_bias: float = 0.0,
    inventory_ratio: float | None = None,
    target_inventory_ratio: float = 0.5,
    inventory_band_low: float = 0.45,
    inventory_band_high: float = 0.55,
) -> Quote:
    spread_value = mid * (spread_bps / 10000.0)
    half_spread = spread_value / 2.0

    bid_distance = half_spread
    ask_distance = half_spread
    directional_bias = clamp(directional_bias, -1.0, 1.0)
    target_inventory_ratio = clamp(target_inventory_ratio, 0.0, 1.0)
    inventory_band_low = clamp(inventory_band_low, 0.0, target_inventory_ratio)
    inventory_band_high = clamp(inventory_band_high, target_inventory_ratio, 1.0)
    if inventory_ratio is None:
        if max_inventory_usd > 0:
            inventory_ratio = clamp(inventory_usd / max_inventory_usd, 0.0, 1.0)
        else:
            inventory_ratio = target_inventory_ratio
    inventory_ratio = clamp(inventory_ratio, 0.0, 1.0)
    band_half_width = max(
        max(target_inventory_ratio - inventory_band_low, inventory_band_high - target_inventory_ratio),
        0.01,
    )
    inventory_deviation = inventory_ratio - target_inventory_ratio
    normalized_inventory_deviation = clamp(inventory_deviation / band_half_width, -2.5, 2.5)
    reservation_shift_bps = normalized_inventory_deviation * max(
        spread_bps * (0.40 + max(inventory_skew_strength, 0.0)),
        1.0,
    )
    reservation_price = mid * (1.0 - (reservation_shift_bps / 10000.0))

    if directional_bias > 0:
        bid_distance *= max(0.55, 1.0 - (directional_bias * 0.25))
        ask_distance *= 1.0 + (directional_bias * 0.20)
    elif directional_bias < 0:
        bearish_bias = abs(directional_bias)
        bid_distance *= 1.0 + (bearish_bias * 0.20)
        ask_distance *= max(0.55, 1.0 - (bearish_bias * 0.25))

    inventory_pressure = min(abs(normalized_inventory_deviation), 2.0) / 2.0
    skew_factor = max(inventory_skew_strength, 0.0)
    if normalized_inventory_deviation > 0:
        bid_distance *= 1.0 + (inventory_pressure * (0.45 + (skew_factor * 3.0)))
        ask_distance *= max(0.28, 1.0 - (inventory_pressure * (0.30 + (skew_factor * 2.5))))
    elif normalized_inventory_deviation < 0:
        bid_distance *= max(0.28, 1.0 - (inventory_pressure * (0.30 + (skew_factor * 2.5))))
        ask_distance *= 1.0 + (inventory_pressure * (0.45 + (skew_factor * 3.0)))

    bid = reservation_price - bid_distance
    ask = reservation_price + ask_distance
    if ask <= bid:
        ask = bid + max(half_spread * 0.50, mid * 0.0001)

    return Quote(
        bid=bid,
        ask=ask,
        mid=mid,
        spread_bps=spread_bps,
        mode="TREND_UP",
    )


def choose_trade_size_usd(
    mode: str,
    base_size: float,
    inventory_usd: float,
    max_inventory_usd: float,
) -> float:
    if mode == "OVERWEIGHT_EXIT":
        if max_inventory_usd <= 0:
            return 0.0

        excess_inventory = max(inventory_usd - max_inventory_usd, 0.0)
        if excess_inventory <= 0:
            return 0.0

        unwind_size = max(base_size, excess_inventory * 0.35)
        return min(unwind_size, base_size * 3.0)

    if mode == "RANGE_MAKER":
        size = base_size * RANGE_SIZE_MULTIPLIER
        if max_inventory_usd > 0:
            imbalance_ratio = abs(inventory_usd) / max_inventory_usd
            if imbalance_ratio > 0.9:
                size *= max(0.80, 1.0 - ((imbalance_ratio - 0.9) * 0.22))
        return max(size, 0.0)

    if mode != "TREND_UP":
        return 0.0

    size = base_size * TREND_SIZE_MULTIPLIER

    if max_inventory_usd > 0:
        imbalance_ratio = abs(inventory_usd) / max_inventory_usd
        if imbalance_ratio > 1.0:
            size *= 1.0 + ((min(imbalance_ratio, 3.0) - 1.0) * 0.14)
            excess_inventory = max(inventory_usd - max_inventory_usd, 0.0)
            if excess_inventory > 0:
                size = max(
                    size,
                    base_size + (excess_inventory * max(TREND_OVERWEIGHT_UNWIND_FRACTION, 0.0)),
                )

    return min(size, base_size * max(TREND_OVERWEIGHT_MAX_MULTIPLIER, 1.0))
