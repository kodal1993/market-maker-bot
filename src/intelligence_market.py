from __future__ import annotations

from config import (
    EMA_RANGE_BAND_BPS,
    ADAPTIVE_LOOKBACK_CYCLES,
    EXTREME_VOL_THRESHOLD_MULTIPLIER,
    HIGH_VOL_THRESHOLD,
    INTELLIGENCE_WARMUP_ROWS,
    LOW_VOL_THRESHOLD_MULTIPLIER,
    LONG_MA_WINDOW,
    MAX_INVENTORY_MULTIPLIER,
    MAX_SPREAD_MULTIPLIER,
    MAX_TRADE_SIZE_MULTIPLIER,
    MIN_INVENTORY_MULTIPLIER,
    MIN_SPREAD_MULTIPLIER,
    MIN_TRADE_SIZE_MULTIPLIER,
    RISK_OFF_TREND_MULTIPLIER,
    SHORT_MA_WINDOW,
    TREND_THRESHOLD,
    VOL_WINDOW,
)
from intelligence_models import AdaptiveState, MarketState
from intelligence_utils import clamp, ema, market_price_window, stddev


def build_market_state(prices: list[float]) -> MarketState:
    price_window = market_price_window(prices)
    short_ma = ema(price_window, SHORT_MA_WINDOW)
    long_ma = ema(price_window, LONG_MA_WINDOW)

    returns: list[float] = []
    for index in range(1, len(price_window)):
        previous = price_window[index - 1]
        current = price_window[index]
        if previous > 0:
            returns.append((current - previous) / previous)

    recent_returns = returns[-VOL_WINDOW:] if VOL_WINDOW > 0 else returns
    volatility = stddev(recent_returns)

    trend_strength = 0.0
    if long_ma > 0:
        trend_strength = (short_ma - long_ma) / long_ma
    ema_gap_bps = abs(trend_strength) * 10000.0

    low_vol_threshold = HIGH_VOL_THRESHOLD * LOW_VOL_THRESHOLD_MULTIPLIER
    extreme_vol_threshold = HIGH_VOL_THRESHOLD * EXTREME_VOL_THRESHOLD_MULTIPLIER

    if volatility >= extreme_vol_threshold:
        volatility_state = "EXTREME"
    elif volatility >= HIGH_VOL_THRESHOLD:
        volatility_state = "HIGH"
    elif volatility <= low_vol_threshold:
        volatility_state = "LOW"
    else:
        volatility_state = "NORMAL"

    if len(prices) < INTELLIGENCE_WARMUP_ROWS:
        regime = "WARMUP"
    elif volatility_state == "EXTREME":
        regime = "RISK_OFF"
    elif (
        trend_strength <= -(TREND_THRESHOLD * RISK_OFF_TREND_MULTIPLIER)
        and volatility_state in {"HIGH", "EXTREME"}
    ):
        regime = "RISK_OFF"
    elif ema_gap_bps <= max(EMA_RANGE_BAND_BPS, 0.0) and volatility_state in {"LOW", "NORMAL"}:
        regime = "RANGE"
    elif abs(trend_strength) > TREND_THRESHOLD:
        regime = "TREND"
    else:
        regime = "RANGE"

    market_score = 0.0
    if TREND_THRESHOLD > 0:
        market_score = clamp(trend_strength / (TREND_THRESHOLD * 4.0), -1.0, 1.0)

    if regime == "RANGE":
        market_score *= 0.45
    elif regime == "RISK_OFF":
        market_score = min(market_score, -0.35)

    return MarketState(
        regime=regime,
        volatility_state=volatility_state,
        short_ma=short_ma,
        long_ma=long_ma,
        volatility=volatility,
        trend_strength=trend_strength,
        market_score=market_score,
    )


def build_adaptive_state(
    recent_equities: list[float],
    current_equity: float,
    equity_peak: float,
) -> AdaptiveState:
    if ADAPTIVE_LOOKBACK_CYCLES <= 0:
        history = recent_equities
    else:
        history = recent_equities[-ADAPTIVE_LOOKBACK_CYCLES:]

    performance_score = 0.0
    if len(history) >= 2:
        change = current_equity - history[0]
        normalization = max(abs(history[0]) * 0.01, 1.0)
        performance_score = clamp(change / normalization, -1.0, 1.0)

    drawdown_pct = 0.0
    if equity_peak > 0:
        drawdown_pct = max((equity_peak - current_equity) / equity_peak, 0.0)

    performance_score = clamp(performance_score - (drawdown_pct * 4.0), -1.0, 1.0)

    inventory_multiplier = clamp(
        1.0 + (performance_score * 0.22) - (drawdown_pct * 5.0),
        MIN_INVENTORY_MULTIPLIER,
        MAX_INVENTORY_MULTIPLIER,
    )
    trade_size_multiplier = clamp(
        1.0 + (performance_score * 0.42) - (drawdown_pct * 8.0),
        MIN_TRADE_SIZE_MULTIPLIER,
        MAX_TRADE_SIZE_MULTIPLIER,
    )
    spread_multiplier = clamp(
        1.0 - (performance_score * 0.12) + (drawdown_pct * 5.5),
        MIN_SPREAD_MULTIPLIER,
        MAX_SPREAD_MULTIPLIER,
    )
    threshold_multiplier = clamp(
        1.0 - (performance_score * 0.08) + (drawdown_pct * 3.4),
        0.80,
        1.50,
    )

    return AdaptiveState(
        performance_score=performance_score,
        inventory_multiplier=inventory_multiplier,
        trade_size_multiplier=trade_size_multiplier,
        spread_multiplier=spread_multiplier,
        threshold_multiplier=threshold_multiplier,
    )
