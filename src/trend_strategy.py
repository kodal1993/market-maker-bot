from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendSignal:
    action: str
    confidence: float
    stop_loss_price: float
    take_profit_price: float
    reason: str


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def evaluate_trend_signal(
    *,
    price: float,
    ema20: float,
    ema50: float,
    vwap: float,
    rsi: float,
    atr: float,
    volume_change: float,
    min_volume_change: float = 0.0,
) -> TrendSignal:
    """Trend strategy with ATR stops and dynamic take-profit."""
    if min(price, ema20, ema50, vwap) <= 0:
        return TrendSignal("HOLD", 0.0, 0.0, 0.0, "invalid_inputs")

    bullish = ema20 > ema50 and price > vwap
    bearish = ema20 < ema50 and price < vwap

    rsi_long_ok = 52.0 <= rsi <= 78.0
    rsi_short_ok = 22.0 <= rsi <= 48.0
    volume_ok = volume_change >= min_volume_change
    atr_safe = atr > 0

    if bullish and rsi_long_ok and volume_ok and atr_safe:
        strength = _clamp(((ema20 - ema50) / ema50) * 1000.0, 0.0, 1.0)
        confidence = _clamp(0.55 + (strength * 0.25) + min(volume_change, 1.0) * 0.20, 0.0, 1.0)
        stop_loss = price - (1.2 * atr)
        take_profit = price + (atr * (2.0 + confidence))
        return TrendSignal("LONG", confidence, stop_loss, take_profit, "ema_vwap_rsi_volume_long")

    if bearish and rsi_short_ok and volume_ok and atr_safe:
        strength = _clamp(((ema50 - ema20) / ema50) * 1000.0, 0.0, 1.0)
        confidence = _clamp(0.55 + (strength * 0.25) + min(volume_change, 1.0) * 0.20, 0.0, 1.0)
        stop_loss = price + (1.2 * atr)
        take_profit = price - (atr * (2.0 + confidence))
        return TrendSignal("SHORT", confidence, stop_loss, take_profit, "ema_vwap_rsi_volume_short")

    return TrendSignal("HOLD", 0.0, 0.0, 0.0, "confirmation_not_met")
