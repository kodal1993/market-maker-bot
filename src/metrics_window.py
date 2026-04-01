from __future__ import annotations

import math
from statistics import fmean, pstdev


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def trailing_window(values: list[float], lookback: int) -> list[float]:
    if lookback <= 0:
        return list(values)
    return list(values[-lookback:])


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(fmean(values))


def pct_change(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return ((end / start) - 1.0) * 100.0


def pct_changes(values: list[float]) -> list[float]:
    if len(values) < 2:
        return []
    changes: list[float] = []
    for left, right in zip(values[:-1], values[1:]):
        changes.append(pct_change(left, right))
    return changes


def realized_volatility_pct(values: list[float]) -> float:
    changes = pct_changes(values)
    if len(changes) < 2:
        return 0.0
    return float(pstdev(changes))


def direction_consistency(changes: list[float]) -> float:
    directional = [change for change in changes if abs(change) > 1e-9]
    if not directional:
        return 0.0
    up_moves = sum(1 for change in directional if change > 0)
    down_moves = sum(1 for change in directional if change < 0)
    return max(up_moves, down_moves) / len(directional)


def sign_flip_ratio(changes: list[float]) -> float:
    directional = [1 if change > 0 else -1 for change in changes if abs(change) > 1e-9]
    if len(directional) < 2:
        return 0.0
    flips = sum(1 for left, right in zip(directional[:-1], directional[1:]) if left != right)
    return flips / (len(directional) - 1)


def ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    effective_span = max(int(span), 1)
    alpha = 2.0 / (effective_span + 1.0)
    value = float(values[0])
    for current in values[1:]:
        value = (alpha * float(current)) + ((1.0 - alpha) * value)
    return value


def bounded_ratio(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    if abs(denominator) <= 1e-9:
        return fallback
    return numerator / denominator


def safe_range(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return min(values), max(values)


def path_distance_pct(values: list[float]) -> float:
    return sum(abs(change) for change in pct_changes(values))


def percentile_band(low: float, high: float, position: float) -> float:
    return low + ((high - low) * clamp(position, 0.0, 1.0))


def normalized_position(value: float, low: float, high: float) -> float:
    width = max(high - low, 1e-9)
    return clamp((value - low) / width, 0.0, 1.0)


def sqrt(value: float) -> float:
    return math.sqrt(max(value, 0.0))
