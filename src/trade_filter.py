from __future__ import annotations

import math

from config import (
    LOW_ACTIVITY_LOOKBACK_HOURS,
    MAX_TRADES_PER_DAY,
    MIN_TRADE_DISTANCE_PCT,
    MIN_TRADE_RATE_PER_HOUR,
    MIN_TIME_BETWEEN_TRADES_MINUTES,
    STATE_MACHINE_LOSS_STREAK_LIMIT,
    TRADE_COOLDOWN_MINUTES,
    TRADE_FILTER_BUY_RSI_MAX,
    TRADE_FILTER_DEBUG_MODE,
    TRADE_FILTER_FORCE_TRADE_MINUTES,
    TRADE_FILTER_MOMENTUM_LIMIT_HIGH_VOL_BPS,
    TRADE_FILTER_MOMENTUM_LIMIT_LOW_VOL_BPS,
    TRADE_FILTER_MOMENTUM_LIMIT_MID_VOL_BPS,
    TRADE_FILTER_LOSS_STREAK_LIMIT,
    TRADE_FILTER_SELL_RSI_MIN,
    TRADE_FILTER_STRONG_TREND_SCORE,
    TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER,
    TRADE_FILTER_STRONG_TREND_SKIP_SCORE,
    TRADE_FILTER_TREND_AGAINST_SCORE,
)
from types_bot import TradeFilterResult

COOLDOWN_EXEMPT_REASONS = {
    "failsafe_sell",
    "inventory_force_reduce",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
    "reentry_pullback",
    "reentry_max_miss",
    "reentry_runaway",
}

ANTI_OVERTRADING_EXEMPT_REASONS = {
    "failsafe_sell",
    "inventory_force_reduce",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
}

LOSS_STREAK_SENSITIVE_REASONS = {
    "force_trade_buy",
    "inventory_rebalance",
    "partial_reset",
    "range_buy",
    "inactivity_range_buy",
    "quoted_sell",
    "range_sell",
    "mean_reversion_exit",
    "trend_rally_sell",
    "trend_buy",
}

STRONG_TREND_BUY_REASONS = {
    "force_trade_buy",
    "inventory_rebalance",
    "partial_reset",
    "trend_buy",
}

PASSIVE_SELL_REASONS = {
    "quoted_sell",
    "range_sell",
    "mean_reversion_exit",
    "trend_rally_sell",
}

PASSIVE_BUY_REASONS = {
    "trend_buy",
    "partial_reset",
    "range_buy",
    "inactivity_range_buy",
}


def calculate_recent_momentum_bps(prices: list[float], lookback: int = 3) -> float:
    if len(prices) < max(lookback + 1, 2):
        return 0.0

    start_price = prices[-(lookback + 1)]
    end_price = prices[-1]
    if start_price <= 0:
        return 0.0
    return ((end_price / start_price) - 1.0) * 10000.0


class TradeFilter:
    def __init__(self, cycle_seconds: float):
        self.cycle_seconds = max(cycle_seconds, 1.0)

    def _cooldown_cycles(self) -> int:
        if TRADE_COOLDOWN_MINUTES <= 0:
            return 0
        return max(int(math.ceil((TRADE_COOLDOWN_MINUTES * 60.0) / self.cycle_seconds)), 0)

    def _momentum_limit_bps(self, volatility_state: str, low_trade_rate_active: bool) -> float:
        if volatility_state == "LOW":
            limit_bps = TRADE_FILTER_MOMENTUM_LIMIT_LOW_VOL_BPS
        elif volatility_state in {"HIGH", "EXTREME"}:
            limit_bps = TRADE_FILTER_MOMENTUM_LIMIT_HIGH_VOL_BPS
        else:
            limit_bps = TRADE_FILTER_MOMENTUM_LIMIT_MID_VOL_BPS

        if low_trade_rate_active:
            limit_bps *= 1.35
        return max(limit_bps, 0.0)

    def evaluate(
        self,
        side: str,
        trade_reason: str,
        cycle_index: int,
        order_price: float,
        last_trade_cycle: int | None,
        last_trade_price: float | None,
        loss_streak: int,
        rsi_value: float,
        momentum_bps: float,
        regime: str,
        market_score: float,
        volatility_state: str,
        trade_count: int,
        daily_trade_count: int = 0,
    ) -> TradeFilterResult:
        elapsed_minutes = ((cycle_index + 1) * self.cycle_seconds) / 60.0
        minutes_since_trade = (
            ((cycle_index - last_trade_cycle) * self.cycle_seconds) / 60.0
            if last_trade_cycle is not None
            else elapsed_minutes
        )
        trade_rate_per_hour = trade_count / max(elapsed_minutes / 60.0, 1e-9) if elapsed_minutes > 0 else 0.0
        activity_window_minutes = max(LOW_ACTIVITY_LOOKBACK_HOURS * 60.0, 15.0)
        low_trade_rate_active = elapsed_minutes >= activity_window_minutes and trade_rate_per_hour < MIN_TRADE_RATE_PER_HOUR
        force_trade_active = (
            TRADE_FILTER_FORCE_TRADE_MINUTES > 0
            and minutes_since_trade >= TRADE_FILTER_FORCE_TRADE_MINUTES
        )
        base_cooldown_cycles = self._cooldown_cycles()
        cooldown_cycles = 0 if low_trade_rate_active else max(int(math.ceil(base_cooldown_cycles * 0.65)), 0)
        min_trade_distance_pct = MIN_TRADE_DISTANCE_PCT * (0.45 if low_trade_rate_active else 0.70)
        momentum_limit_bps = self._momentum_limit_bps(volatility_state, low_trade_rate_active) * 1.10
        buy_rsi_max = TRADE_FILTER_BUY_RSI_MAX + (6.0 if low_trade_rate_active else 2.0)
        sell_rsi_min = TRADE_FILTER_SELL_RSI_MIN - (6.0 if low_trade_rate_active else 2.0)
        loss_streak_reduce_threshold = max(TRADE_FILTER_LOSS_STREAK_LIMIT, 1)
        loss_streak_pause_threshold = max(STATE_MACHINE_LOSS_STREAK_LIMIT, loss_streak_reduce_threshold + 1)
        size_multiplier = 1.0
        adjustment_reasons: list[str] = []
        filter_values: dict[str, object] = {
            "side": side,
            "trade_reason": trade_reason,
            "volatility_state": volatility_state,
            "rsi_value": round(rsi_value, 4),
            "momentum_bps": round(momentum_bps, 4),
            "momentum_limit_bps": round(momentum_limit_bps, 4),
            "buy_rsi_max": round(buy_rsi_max, 4),
            "sell_rsi_min": round(sell_rsi_min, 4),
            "market_score": round(market_score, 4),
            "regime": regime,
            "loss_streak": loss_streak,
            "loss_streak_reduce_threshold": loss_streak_reduce_threshold,
            "loss_streak_pause_threshold": loss_streak_pause_threshold,
            "trade_count": trade_count,
            "daily_trade_count": daily_trade_count,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "trade_rate_per_hour": round(trade_rate_per_hour, 4),
            "minutes_since_trade": round(minutes_since_trade, 4),
            "min_time_between_trades_minutes": round(MIN_TIME_BETWEEN_TRADES_MINUTES, 4),
            "force_trade_active": force_trade_active,
            "low_trade_rate_active": low_trade_rate_active,
            "cooldown_cycles": cooldown_cycles,
            "min_trade_distance_pct": round(min_trade_distance_pct, 4),
            "debug_mode": TRADE_FILTER_DEBUG_MODE,
        }

        anti_overtrading_exempt = trade_reason in ANTI_OVERTRADING_EXEMPT_REASONS

        if not anti_overtrading_exempt and MAX_TRADES_PER_DAY > 0 and daily_trade_count >= MAX_TRADES_PER_DAY:
            filter_values["adjustment_reasons"] = adjustment_reasons
            filter_values["daily_trade_limit_hit"] = True
            return TradeFilterResult(
                False,
                "max_trades_per_day",
                size_multiplier=size_multiplier,
                filter_values=filter_values,
            )

        if (
            not anti_overtrading_exempt
            and last_trade_cycle is not None
            and MIN_TIME_BETWEEN_TRADES_MINUTES > 0
            and minutes_since_trade < MIN_TIME_BETWEEN_TRADES_MINUTES
        ):
            filter_values["remaining_trade_gap_minutes"] = round(
                max(MIN_TIME_BETWEEN_TRADES_MINUTES - minutes_since_trade, 0.0),
                4,
            )
            filter_values["adjustment_reasons"] = adjustment_reasons
            return TradeFilterResult(
                False,
                "min_time_between_trades",
                size_multiplier=size_multiplier,
                filter_values=filter_values,
            )

        if loss_streak >= loss_streak_pause_threshold and trade_reason in LOSS_STREAK_SENSITIVE_REASONS:
            filter_values["adjustment_reasons"] = adjustment_reasons
            filter_values["loss_streak_pause_active"] = True
            return TradeFilterResult(
                False,
                "loss_streak_pause",
                size_multiplier=size_multiplier,
                filter_values=filter_values,
            )

        if loss_streak >= loss_streak_reduce_threshold and trade_reason in LOSS_STREAK_SENSITIVE_REASONS:
            size_multiplier *= 0.50
            adjustment_reasons.append("loss_streak_size_reduction")

        strong_trend_buy_active = (
            side == "buy"
            and trade_reason in STRONG_TREND_BUY_REASONS
            and regime == "TREND"
            and market_score >= TRADE_FILTER_STRONG_TREND_SCORE
            and momentum_bps > 0
        )
        filter_values["strong_trend_active"] = strong_trend_buy_active
        filter_values["strong_trend_score"] = round(TRADE_FILTER_STRONG_TREND_SCORE, 4)
        filter_values["strong_trend_skip_score"] = round(TRADE_FILTER_STRONG_TREND_SKIP_SCORE, 4)
        if strong_trend_buy_active:
            if (
                market_score >= TRADE_FILTER_STRONG_TREND_SKIP_SCORE
                and momentum_bps >= max(momentum_limit_bps * 0.80, 25.0)
            ):
                filter_values["adjustment_reasons"] = adjustment_reasons
                filter_values["strong_trend_skip_active"] = True
                return TradeFilterResult(
                    False,
                    "strong_trend_skip",
                    size_multiplier=size_multiplier,
                    filter_values=filter_values,
                )

            size_multiplier *= max(min(TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER, 1.0), 0.10)
            adjustment_reasons.append("strong_trend_size_reduction")

        if force_trade_active:
            filter_values["adjustment_reasons"] = adjustment_reasons + ["force_trade_override"]
            filter_values["size_multiplier"] = round(size_multiplier, 4)
            return TradeFilterResult(True, size_multiplier=size_multiplier, filter_values=filter_values)

        if trade_reason in COOLDOWN_EXEMPT_REASONS:
            filter_values["adjustment_reasons"] = ["cooldown_exempt"]
            return TradeFilterResult(True, size_multiplier=size_multiplier, filter_values=filter_values)

        if (
            last_trade_cycle is not None
            and cooldown_cycles > 0
            and (cycle_index - last_trade_cycle) < cooldown_cycles
        ):
            cooldown_progress = (cycle_index - last_trade_cycle) / max(cooldown_cycles, 1)
            filter_values["cooldown_progress"] = round(cooldown_progress, 4)
            if cooldown_progress < 0.45:
                filter_values["adjustment_reasons"] = adjustment_reasons
                return TradeFilterResult(False, "cooldown", size_multiplier=size_multiplier, filter_values=filter_values)
            size_multiplier *= 0.45
            adjustment_reasons.append("cooldown_soft_limit")

        if order_price > 0 and last_trade_price and last_trade_price > 0 and min_trade_distance_pct > 0:
            distance_pct = abs((order_price / last_trade_price) - 1.0) * 100.0
            filter_values["distance_pct"] = round(distance_pct, 4)
            if distance_pct < min_trade_distance_pct:
                distance_ratio = distance_pct / max(min_trade_distance_pct, 1e-9)
                filter_values["distance_ratio"] = round(distance_ratio, 4)
                if distance_ratio < 0.35:
                    filter_values["adjustment_reasons"] = adjustment_reasons
                    return TradeFilterResult(
                        False,
                        "min_trade_distance",
                        size_multiplier=size_multiplier,
                        filter_values=filter_values,
                    )
                size_multiplier *= max(distance_ratio, 0.35)
                adjustment_reasons.append("distance_soft_limit")

        if side == "buy":
            if trade_reason in {"trend_buy", "range_buy"} and rsi_value >= buy_rsi_max:
                rsi_excess = rsi_value - buy_rsi_max
                filter_values["rsi_excess"] = round(rsi_excess, 4)
                if rsi_excess >= 5.0 and not low_trade_rate_active:
                    filter_values["adjustment_reasons"] = adjustment_reasons
                    return TradeFilterResult(False, "rsi_limit", size_multiplier=size_multiplier, filter_values=filter_values)
                size_multiplier *= 0.55
                adjustment_reasons.append("rsi_size_reduction")
            if trade_reason in PASSIVE_BUY_REASONS and momentum_bps >= momentum_limit_bps:
                momentum_excess = momentum_bps - momentum_limit_bps
                filter_values["momentum_excess_bps"] = round(momentum_excess, 4)
                if momentum_excess >= (momentum_limit_bps * 0.45) and not low_trade_rate_active:
                    filter_values["adjustment_reasons"] = adjustment_reasons
                    return TradeFilterResult(
                        False,
                        "momentum_limit",
                        size_multiplier=size_multiplier,
                        filter_values=filter_values,
                    )
                size_multiplier *= 0.45
                adjustment_reasons.append("momentum_size_reduction")
            if regime == "RISK_OFF" and trade_reason not in {
                "reentry_pullback",
                "reentry_timeout",
                "reentry_runaway",
                "reentry_max_miss",
            }:
                size_multiplier *= 0.60
                adjustment_reasons.append("trend_against_size_reduction")

        elif side == "sell":
            if trade_reason in PASSIVE_SELL_REASONS and rsi_value <= sell_rsi_min:
                rsi_gap = sell_rsi_min - rsi_value
                filter_values["rsi_gap"] = round(rsi_gap, 4)
                if rsi_gap >= 5.0 and not low_trade_rate_active:
                    filter_values["adjustment_reasons"] = adjustment_reasons
                    return TradeFilterResult(False, "rsi_limit", size_multiplier=size_multiplier, filter_values=filter_values)
                size_multiplier *= 0.55
                adjustment_reasons.append("rsi_size_reduction")
            if trade_reason in PASSIVE_SELL_REASONS and momentum_bps <= -momentum_limit_bps:
                momentum_gap = abs(momentum_bps) - momentum_limit_bps
                filter_values["momentum_gap_bps"] = round(momentum_gap, 4)
                if momentum_gap >= (momentum_limit_bps * 0.45) and not low_trade_rate_active:
                    filter_values["adjustment_reasons"] = adjustment_reasons
                    return TradeFilterResult(
                        False,
                        "momentum_limit",
                        size_multiplier=size_multiplier,
                        filter_values=filter_values,
                    )
                size_multiplier *= 0.45
                adjustment_reasons.append("momentum_size_reduction")
            if (
                trade_reason in PASSIVE_SELL_REASONS
                and regime == "TREND"
                and market_score >= TRADE_FILTER_TREND_AGAINST_SCORE
                and momentum_bps >= (momentum_limit_bps * 0.5)
            ):
                size_multiplier *= 0.60
                adjustment_reasons.append("trend_against_size_reduction")

        size_multiplier = max(size_multiplier, 0.35 if adjustment_reasons else 1.0)
        filter_values["adjustment_reasons"] = adjustment_reasons
        filter_values["size_multiplier"] = round(size_multiplier, 4)
        return TradeFilterResult(True, size_multiplier=size_multiplier, filter_values=filter_values)
