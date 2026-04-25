from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BreakoutSignal:
    action: str
    confidence: float
    hold_seconds: int
    reason: str


def evaluate_breakout(
    *,
    price: float,
    range_high: float,
    range_low: float,
    volume_change: float,
    atr: float,
    atr_baseline: float,
    max_hold_seconds: int = 180,
) -> BreakoutSignal:
    if min(price, range_high, range_low) <= 0:
        return BreakoutSignal("HOLD", 0.0, max_hold_seconds, "invalid_inputs")

    range_width = max(range_high - range_low, 1e-9)
    breakout_up = price > range_high
    breakout_down = price < range_low
    volume_spike = volume_change >= 0.20
    atr_expanding = atr > max(atr_baseline * 1.15, 1e-9)

    if not (volume_spike and atr_expanding):
        return BreakoutSignal("HOLD", 0.0, max_hold_seconds, "volume_or_atr_not_confirmed")

    distance = abs(price - (range_high if breakout_up else range_low)) / range_width
    confidence = min(0.99, 0.60 + min(distance, 0.40) + min(volume_change, 0.30))

    if breakout_up:
        return BreakoutSignal("LONG", confidence, max_hold_seconds, "breakout_up_confirmed")
    if breakout_down:
        return BreakoutSignal("SHORT", confidence, max_hold_seconds, "breakout_down_confirmed")
    return BreakoutSignal("HOLD", 0.0, max_hold_seconds, "inside_range")
