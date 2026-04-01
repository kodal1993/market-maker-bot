from __future__ import annotations

from dataclasses import dataclass

from config import (
    CONFIRMATION_TIMEFRAME_SECONDS,
    ENABLE_EXECUTION_CONFIRMATION,
    ENABLE_TREND_TIMEFRAME_FILTER,
    EXECUTION_TIMEFRAME_SECONDS,
    INTELLIGENCE_WARMUP_ROWS,
    LONG_MA_WINDOW,
    PRICE_BOOTSTRAP_ROWS,
    REGIME_LOOKBACK_CANDLES,
    REENTRY_MOMENTUM_LOOKBACK,
    REENTRY_RSI_PERIOD,
    SHORT_MA_WINDOW,
    TREND_FILTER_TIMEFRAME_SECONDS,
    VOL_WINDOW,
)


EXECUTION_LOOKBACK_POINTS = max(
    INTELLIGENCE_WARMUP_ROWS,
    LONG_MA_WINDOW,
    VOL_WINDOW + 1,
    REENTRY_RSI_PERIOD + 1,
    REENTRY_MOMENTUM_LOOKBACK + 2,
    6,
)
TREND_LOOKBACK_POINTS = max(
    REGIME_LOOKBACK_CANDLES,
    LONG_MA_WINDOW,
    SHORT_MA_WINDOW,
    6,
)
CONFIRMATION_LOOKBACK_POINTS = max(REENTRY_MOMENTUM_LOOKBACK + 3, 6)


@dataclass(frozen=True)
class TimeframeSeriesSnapshot:
    execution_prices: list[float]
    trend_prices: list[float]
    confirmation_prices: list[float]
    execution_bucket_count: int
    trend_bucket_count: int
    confirmation_bucket_count: int


def steps_per_timeframe(timeframe_seconds: float, cycle_seconds: float) -> int:
    effective_cycle_seconds = max(float(cycle_seconds), 1.0)
    effective_timeframe_seconds = max(float(timeframe_seconds), effective_cycle_seconds)
    return max(int(round(effective_timeframe_seconds / effective_cycle_seconds)), 1)


def aggregate_close_prices(
    raw_prices: list[float],
    *,
    cycle_seconds: float,
    timeframe_seconds: float,
    max_points: int = 0,
) -> list[float]:
    if not raw_prices:
        return []

    step = steps_per_timeframe(timeframe_seconds, cycle_seconds)
    if step <= 1:
        series = list(raw_prices)
    else:
        candle_count = len(raw_prices) // step
        series = [raw_prices[((index + 1) * step) - 1] for index in range(candle_count)]

    if max_points > 0 and len(series) > max_points:
        return series[-max_points:]
    return series


def build_timeframe_snapshot(
    raw_prices: list[float],
    *,
    cycle_seconds: float,
    execution_timeframe_seconds: float = EXECUTION_TIMEFRAME_SECONDS,
    trend_timeframe_seconds: float = TREND_FILTER_TIMEFRAME_SECONDS,
    confirmation_timeframe_seconds: float = CONFIRMATION_TIMEFRAME_SECONDS,
    enable_trend_filter: bool = ENABLE_TREND_TIMEFRAME_FILTER,
    enable_confirmation: bool = ENABLE_EXECUTION_CONFIRMATION,
) -> TimeframeSeriesSnapshot:
    execution_prices = aggregate_close_prices(
        raw_prices,
        cycle_seconds=cycle_seconds,
        timeframe_seconds=execution_timeframe_seconds,
        max_points=EXECUTION_LOOKBACK_POINTS,
    )
    trend_source_timeframe = trend_timeframe_seconds if enable_trend_filter else execution_timeframe_seconds
    trend_prices = aggregate_close_prices(
        raw_prices,
        cycle_seconds=cycle_seconds,
        timeframe_seconds=trend_source_timeframe,
        max_points=TREND_LOOKBACK_POINTS,
    )
    confirmation_source_timeframe = confirmation_timeframe_seconds if enable_confirmation else execution_timeframe_seconds
    confirmation_prices = aggregate_close_prices(
        raw_prices,
        cycle_seconds=cycle_seconds,
        timeframe_seconds=confirmation_source_timeframe,
        max_points=CONFIRMATION_LOOKBACK_POINTS,
    )

    return TimeframeSeriesSnapshot(
        execution_prices=execution_prices,
        trend_prices=trend_prices,
        confirmation_prices=confirmation_prices,
        execution_bucket_count=len(aggregate_close_prices(
            raw_prices,
            cycle_seconds=cycle_seconds,
            timeframe_seconds=execution_timeframe_seconds,
        )),
        trend_bucket_count=len(aggregate_close_prices(
            raw_prices,
            cycle_seconds=cycle_seconds,
            timeframe_seconds=trend_source_timeframe,
        )),
        confirmation_bucket_count=len(aggregate_close_prices(
            raw_prices,
            cycle_seconds=cycle_seconds,
            timeframe_seconds=confirmation_source_timeframe,
        )),
    )


def required_bootstrap_price_rows(
    *,
    cycle_seconds: float,
    configured_rows: int = PRICE_BOOTSTRAP_ROWS,
    execution_timeframe_seconds: float = EXECUTION_TIMEFRAME_SECONDS,
    trend_timeframe_seconds: float = TREND_FILTER_TIMEFRAME_SECONDS,
    confirmation_timeframe_seconds: float = CONFIRMATION_TIMEFRAME_SECONDS,
    enable_trend_filter: bool = ENABLE_TREND_TIMEFRAME_FILTER,
    enable_confirmation: bool = ENABLE_EXECUTION_CONFIRMATION,
) -> int:
    execution_rows = EXECUTION_LOOKBACK_POINTS * steps_per_timeframe(execution_timeframe_seconds, cycle_seconds)
    trend_source_timeframe = trend_timeframe_seconds if enable_trend_filter else execution_timeframe_seconds
    trend_rows = TREND_LOOKBACK_POINTS * steps_per_timeframe(trend_source_timeframe, cycle_seconds)
    confirmation_source_timeframe = confirmation_timeframe_seconds if enable_confirmation else execution_timeframe_seconds
    confirmation_rows = CONFIRMATION_LOOKBACK_POINTS * steps_per_timeframe(confirmation_source_timeframe, cycle_seconds)
    return max(int(configured_rows), execution_rows, trend_rows, confirmation_rows, 2)
