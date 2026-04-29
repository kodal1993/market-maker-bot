from __future__ import annotations

from datetime import datetime, timezone

from config import (
    CAPITAL_PRESERVATION_DRAWDOWN_PCT,
    CAUTION_SIZE_MULTIPLIER,
    CAUTION_SPREAD_MULTIPLIER,
    CAUTION_TARGET_INVENTORY_CAP,
    DRAWDOWN_PAUSE_PCT,
    EMA_RANGE_BAND_BPS,
    FREEZE_RECOVERY_MINUTES,
    INVENTORY_DOWNTREND_MAX,
    INVENTORY_NORMAL_MAX,
    INVENTORY_UPTREND_MAX,
    LOW_ACTIVITY_GUARD_ENABLED,
    LOW_ACTIVITY_LOOKBACK_HOURS,
    LOW_ACTIVITY_MAX_DRAWDOWN_PCT,
    LOW_ACTIVITY_MIN_TRADES,
    MIN_TRADES_PER_HOUR,
    MAX_INVENTORY_MULTIPLIER,
    MAX_SPREAD_MULTIPLIER,
    MAX_TRADE_SIZE_MULTIPLIER,
    MIN_INVENTORY_MULTIPLIER,
    MIN_SPREAD_MULTIPLIER,
    MIN_TRADE_SIZE_MULTIPLIER,
    OVERWEIGHT_EXIT_BUFFER_PCT,
    RANGE_DIRECTIONAL_BIAS_FACTOR,
    RANGE_SPREAD_TIGHTENING,
    RANGE_TARGET_INVENTORY_MAX,
    RANGE_TARGET_INVENTORY_MIN,
    RISK_BLOCK_DRAWDOWN_PCT,
    RISK_OFF_TARGET_INVENTORY_MAX,
    SIGNAL_BLOCK_RISK_THRESHOLD,
    SIGNAL_BLOCK_THRESHOLD,
    SIGNAL_CAUTION_THRESHOLD,
    TREND_DIRECTIONAL_BIAS_FACTOR,
    TREND_MOMENTUM_BLOCK_BPS,
    TREND_TARGET_INVENTORY_MAX,
    TREND_TARGET_INVENTORY_MIN,
)
from intelligence_feeds import SignalFeedClient
from intelligence_market import build_adaptive_state, build_market_state
from intelligence_models import IntelligenceSnapshot
from intelligence_signals import build_macro_signal, build_news_signal, build_onchain_signal
from intelligence_utils import clamp
from sizing_engine import compute_max_position_usd, resolve_reference_equity_usd
from strategy_profile import (
    resolve_entry_threshold_multiplier,
    resolve_low_activity_edge_factor,
    resolve_low_activity_entry_factor,
    resolve_min_edge_multiplier,
)


def resolve_drawdown_stage(drawdown_pct: float) -> str:
    if drawdown_pct >= DRAWDOWN_PAUSE_PCT:
        return "pause"
    if drawdown_pct >= RISK_BLOCK_DRAWDOWN_PCT:
        return "aggression_reduce"
    if drawdown_pct >= CAPITAL_PRESERVATION_DRAWDOWN_PCT:
        return "size_reduce"
    return "normal"


def drawdown_stage_priority(stage: str) -> int:
    priorities = {
        "normal": 0,
        "size_reduce": 1,
        "aggression_reduce": 2,
        "pause": 3,
    }
    return priorities.get(stage, 0)


def _resolve_mm_mode(market_regime: str, active_regime: str, trend_direction: str) -> str:
    if market_regime == "CHOP":
        return "defensive_mm"
    if market_regime == "RISK_OFF":
        return "defensive_mm"
    if active_regime == "TREND" or trend_direction in {"up", "down"} or market_regime in {"TREND", "TREND_UP", "TREND_DOWN"}:
        return "aggressive"
    return "base_mm"


def _ema_gap_bps(short_ma: float, long_ma: float) -> float:
    if short_ma <= 0 or long_ma <= 0:
        return 0.0
    return ((short_ma - long_ma) / long_ma) * 10000.0


def _recent_momentum_bps(prices: list[float], lookback: int = 3) -> float:
    if len(prices) < max(lookback + 1, 2):
        return 0.0
    start_price = prices[-(lookback + 1)]
    end_price = prices[-1]
    if start_price <= 0:
        return 0.0
    return ((end_price / start_price) - 1.0) * 10000.0


def _resolve_feed_state(feed_score: float, risk_score: float, macro_blocked: bool) -> str:
    if macro_blocked:
        return "BLOCK"
    if feed_score <= SIGNAL_BLOCK_THRESHOLD and risk_score >= (SIGNAL_BLOCK_RISK_THRESHOLD + 0.10):
        return "BLOCK"
    if feed_score <= SIGNAL_CAUTION_THRESHOLD:
        return "CAUTION"
    if risk_score >= max(SIGNAL_BLOCK_RISK_THRESHOLD - 0.08, 0.0) and feed_score < 0.0:
        return "CAUTION"
    return "NORMAL"


def _resolve_target_inventory_pct(
    regime: str,
    market_score: float,
    adaptive_score: float,
    risk_score: float,
    feed_state: str,
) -> float:
    if regime == "RISK_OFF":
        base_target = RISK_OFF_TARGET_INVENTORY_MAX
    elif regime == "TREND":
        trend_strength = clamp(max(market_score, 0.0), 0.0, 1.0)
        base_target = TREND_TARGET_INVENTORY_MIN + (
            (TREND_TARGET_INVENTORY_MAX - TREND_TARGET_INVENTORY_MIN) * trend_strength
        )
    else:
        neutrality = 1.0 - min(abs(market_score), 1.0)
        base_target = RANGE_TARGET_INVENTORY_MIN + (
            (RANGE_TARGET_INVENTORY_MAX - RANGE_TARGET_INVENTORY_MIN) * neutrality
        )

    base_target += max(adaptive_score, 0.0) * 0.04
    base_target -= risk_score * (0.12 if regime == "RISK_OFF" else 0.08)

    if feed_state == "CAUTION":
        base_target = min(base_target, CAUTION_TARGET_INVENTORY_CAP)
    elif feed_state == "BLOCK":
        base_target = min(base_target, max(RISK_OFF_TARGET_INVENTORY_MAX, RANGE_TARGET_INVENTORY_MIN - 0.08))

    lower_bound = 0.18 if regime == "RISK_OFF" else 0.25
    return clamp(base_target, lower_bound, 0.90)


def _resolve_inventory_cap_floor_pct(regime: str) -> float:
    if regime == "TREND":
        return INVENTORY_UPTREND_MAX
    if regime == "RISK_OFF":
        return INVENTORY_DOWNTREND_MAX
    return INVENTORY_NORMAL_MAX


def _resolve_inventory_pressure(
    inventory_usd: float,
    current_equity: float,
    effective_inventory_cap: float,
    target_inventory_pct: float,
) -> tuple[float, float]:
    if effective_inventory_cap <= 0 or current_equity <= 0:
        return 0.0, 0.0

    target_inventory_usd = current_equity * clamp(target_inventory_pct, 0.0, 1.0)
    soft_pressure = max(inventory_usd - target_inventory_usd, 0.0) / effective_inventory_cap
    hard_pressure = max(inventory_usd - effective_inventory_cap, 0.0) / effective_inventory_cap
    return clamp(soft_pressure, 0.0, 2.0), clamp(hard_pressure, 0.0, 2.0)


def _recent_trade_count(
    recent_trade_cycles: list[int] | None,
    *,
    cycle_index: int,
    cycle_seconds: float,
    lookback_hours: float,
) -> int:
    if not recent_trade_cycles or cycle_seconds <= 0 or lookback_hours <= 0:
        return 0
    lookback_cycles = max(int(round((lookback_hours * 3600.0) / cycle_seconds)), 1)
    floor_cycle = max(cycle_index - lookback_cycles, 0)
    return sum(1 for trade_cycle in recent_trade_cycles if trade_cycle >= floor_cycle)


class IntelligenceEngine:
    def __init__(self):
        self.feed_client = SignalFeedClient()

    def build_snapshot(
        self,
        prices: list[float],
        current_equity: float,
        equity_peak: float,
        recent_equities: list[float],
        inventory_usd: float,
        regime_assessment=None,
        cycle_index: int = 0,
        cycle_seconds: float = 60.0,
        recent_trade_cycles: list[int] | None = None,
        paper_mode: bool = False,
        minutes_since_last_fill: float = 0.0,
        fill_quality_score: float = 1.0,
        fill_quality_tier: str = "normal",
    ) -> IntelligenceSnapshot:
        now_utc = datetime.now(timezone.utc)
        market = build_market_state(prices)
        ema_gap_bps = _ema_gap_bps(market.short_ma, market.long_ma)
        ema_range_band_bps = max(EMA_RANGE_BAND_BPS, 0.5)
        if ema_gap_bps > ema_range_band_bps:
            current_mode = "TREND_UP"
        elif ema_gap_bps < -ema_range_band_bps:
            current_mode = "TREND_DOWN"
        else:
            current_mode = "RANGE"
        active_regime = "RANGE"
        trend_direction = "neutral"
        if regime_assessment is not None:
            active_regime = getattr(regime_assessment, "execution_regime", active_regime)
            trend_direction = getattr(regime_assessment, "trend_direction", trend_direction)
        elif market.regime == "RISK_OFF":
            active_regime = "NO_TRADE" if market.volatility_state == "EXTREME" else "RANGE"
            trend_direction = "down" if current_mode == "TREND_DOWN" else "neutral"
        elif current_mode == "TREND_UP":
            active_regime = "TREND"
            trend_direction = "up"
        elif current_mode == "TREND_DOWN":
            active_regime = "TREND"
            trend_direction = "down"
        momentum_bps = _recent_momentum_bps(prices)
        news_signal = build_news_signal(self.feed_client, now_utc)
        macro_signal = build_macro_signal(self.feed_client, now_utc)
        onchain_signal = build_onchain_signal(self.feed_client, now_utc)
        adaptive = build_adaptive_state(
            recent_equities=recent_equities,
            current_equity=current_equity,
            equity_peak=equity_peak,
        )

        drawdown_pct = 0.0
        if equity_peak > 0:
            drawdown_pct = max((equity_peak - current_equity) / equity_peak, 0.0)
        drawdown_stage = resolve_drawdown_stage(drawdown_pct)

        market_confidence = clamp(0.64 + max(market.market_score, 0.0) * 0.22, 0.0, 0.92)
        feed_confidence = (
            news_signal.confidence
            + macro_signal.confidence
            + onchain_signal.confidence
        ) / 3.0
        confidence = clamp(market_confidence + (feed_confidence * 0.05), 0.0, 1.0)

        # Price action drives execution. External feeds are only risk filters.
        signal_score = clamp((market.market_score * 0.90) + (adaptive.performance_score * 0.10), -1.0, 1.0)
        feed_score = clamp(
            (news_signal.score * 0.30)
            + (macro_signal.score * 0.40)
            + (onchain_signal.score * 0.30),
            -1.0,
            1.0,
        )

        risk_score = 0.0
        if market.volatility_state == "HIGH":
            risk_score += 0.18
        elif market.volatility_state == "EXTREME":
            risk_score += 0.42

        risk_score += clamp(drawdown_pct / max(CAPITAL_PRESERVATION_DRAWDOWN_PCT, 0.0001), 0.0, 1.4) * 0.24

        if news_signal.score <= -0.35:
            risk_score += 0.05
        if macro_signal.score <= -0.30:
            risk_score += 0.06
        if onchain_signal.score <= -0.35:
            risk_score += 0.06
        if market.regime == "RISK_OFF":
            risk_score += 0.16

        feed_state = _resolve_feed_state(feed_score, risk_score, macro_signal.blocked)
        if feed_state == "CAUTION":
            risk_score += 0.02
        elif feed_state == "BLOCK":
            risk_score += 0.10
        risk_score = clamp(risk_score, 0.0, 1.25)

        entry_trigger_multiplier = resolve_entry_threshold_multiplier(active_regime, market.volatility_state)
        min_edge_multiplier = resolve_min_edge_multiplier(active_regime)
        activity_state = "normal"
        recent_trade_count = _recent_trade_count(
            recent_trade_cycles,
            cycle_index=cycle_index,
            cycle_seconds=cycle_seconds,
            lookback_hours=LOW_ACTIVITY_LOOKBACK_HOURS,
        )
        recent_trade_count_60m = _recent_trade_count(
            recent_trade_cycles,
            cycle_index=cycle_index,
            cycle_seconds=cycle_seconds,
            lookback_hours=1.0,
        )
        activity_boost = 0.0
        if MIN_TRADES_PER_HOUR > 0 and recent_trade_count_60m < MIN_TRADES_PER_HOUR:
            activity_boost = clamp(
                (MIN_TRADES_PER_HOUR - recent_trade_count_60m) / max(MIN_TRADES_PER_HOUR, 1.0),
                0.0,
                1.0,
            )
        freeze_recovery_mode = (
            FREEZE_RECOVERY_MINUTES > 0
            and minutes_since_last_fill >= max(FREEZE_RECOVERY_MINUTES, 0.0)
        )
        normalized_fill_quality_score = clamp(fill_quality_score, 0.0, 1.25)
        fill_quality_spread_multiplier = 1.0
        fill_quality_size_multiplier = 1.0
        fill_quality_cooldown_multiplier = 1.0
        if fill_quality_tier == "poor":
            fill_quality_spread_multiplier = 1.14
            fill_quality_size_multiplier = 0.78
            fill_quality_cooldown_multiplier = 1.30
        elif fill_quality_tier == "weak":
            fill_quality_spread_multiplier = 1.07
            fill_quality_size_multiplier = 0.90
            fill_quality_cooldown_multiplier = 1.14
        low_activity_active = (
            LOW_ACTIVITY_GUARD_ENABLED
            and paper_mode
            and LOW_ACTIVITY_LOOKBACK_HOURS > 0
            and cycle_seconds > 0
            and cycle_index >= max(int(round((LOW_ACTIVITY_LOOKBACK_HOURS * 3600.0) / cycle_seconds)), 1)
            and recent_trade_count < max(LOW_ACTIVITY_MIN_TRADES, 0)
            and drawdown_pct <= max(LOW_ACTIVITY_MAX_DRAWDOWN_PCT, 0.0)
        )

        mm_mode = _resolve_mm_mode(
            getattr(regime_assessment, "market_regime", market.regime),
            active_regime,
            trend_direction,
        )
        spread_multiplier = adaptive.spread_multiplier
        if market.regime == "TREND":
            spread_multiplier *= 0.96 + max(market.market_score, 0.0) * 0.04
        elif market.regime == "RANGE":
            spread_multiplier *= RANGE_SPREAD_TIGHTENING
        elif market.regime == "RISK_OFF":
            spread_multiplier *= 1.10
        if getattr(regime_assessment, "market_regime", "") == "CHOP":
            spread_multiplier *= 1.08

        if feed_state == "CAUTION":
            spread_multiplier *= CAUTION_SPREAD_MULTIPLIER
        elif feed_state == "BLOCK":
            spread_multiplier *= CAUTION_SPREAD_MULTIPLIER * 1.06

        spread_multiplier *= 1.0 + max(risk_score - 0.72, 0.0) * 0.18
        if activity_boost > 0.0:
            spread_multiplier *= 1.0 - (activity_boost * 0.06)
        if freeze_recovery_mode:
            spread_multiplier *= 0.92
        spread_multiplier *= fill_quality_spread_multiplier

        max_inventory_multiplier = adaptive.inventory_multiplier
        if market.regime == "TREND":
            max_inventory_multiplier *= 1.02 + max(market.market_score, 0.0) * 0.12
        elif market.regime == "RANGE":
            max_inventory_multiplier *= 0.96
        elif market.regime == "RISK_OFF":
            max_inventory_multiplier *= 0.68

        if feed_state == "CAUTION":
            max_inventory_multiplier *= 0.96
        elif feed_state == "BLOCK":
            max_inventory_multiplier *= 0.82

        max_inventory_multiplier = clamp(
            max_inventory_multiplier,
            MIN_INVENTORY_MULTIPLIER,
            MAX_INVENTORY_MULTIPLIER,
        )

        trade_size_multiplier = adaptive.trade_size_multiplier
        if market.regime == "TREND":
            trade_size_multiplier *= 0.96 + max(market.market_score, 0.0) * 0.12
        elif market.regime == "RANGE":
            trade_size_multiplier *= 1.02
        elif market.regime == "RISK_OFF":
            trade_size_multiplier *= 0.82

        if feed_state == "CAUTION":
            trade_size_multiplier *= CAUTION_SIZE_MULTIPLIER
        elif feed_state == "BLOCK":
            trade_size_multiplier *= min(CAUTION_SIZE_MULTIPLIER, 0.58)

        if activity_boost > 0.0:
            trade_size_multiplier *= 1.0 + (activity_boost * 0.10)
        if freeze_recovery_mode:
            trade_size_multiplier *= 1.10
        trade_size_multiplier *= fill_quality_size_multiplier

        target_inventory_pct = _resolve_target_inventory_pct(
            regime=market.regime,
            market_score=market.market_score,
            adaptive_score=adaptive.performance_score,
            risk_score=risk_score,
            feed_state=feed_state,
        )
        regime_cap_floor_usd = current_equity * clamp(_resolve_inventory_cap_floor_pct(market.regime), 0.0, 1.0)
        reference_equity = resolve_reference_equity_usd(current_equity)
        dynamic_max_position_usd = compute_max_position_usd(reference_equity)
        effective_inventory_cap = max(dynamic_max_position_usd * max_inventory_multiplier, regime_cap_floor_usd)
        soft_inventory_pressure, hard_inventory_pressure = _resolve_inventory_pressure(
            inventory_usd=inventory_usd,
            current_equity=current_equity,
            effective_inventory_cap=effective_inventory_cap,
            target_inventory_pct=target_inventory_pct,
        )
        blockers: list[str] = []
        if market.volatility_state == "LOW":
            spread_multiplier *= 0.94
            blockers.append("low_vol_tighter_quotes")
        elif market.volatility_state == "HIGH":
            spread_multiplier *= 1.03
            trade_size_multiplier *= 0.94
            blockers.append("high_vol_relaxed")
        elif market.volatility_state == "EXTREME":
            spread_multiplier *= 1.14
            trade_size_multiplier *= 0.78
            mm_mode = "defensive_mm"
            blockers.append("extreme_vol_defensive")

        if low_activity_active:
            activity_state = "low_activity_relax"
            entry_trigger_multiplier *= resolve_low_activity_entry_factor(active_regime, market.volatility_state)
            min_edge_multiplier *= resolve_low_activity_edge_factor(active_regime)
            spread_multiplier *= 0.97
            trade_size_multiplier *= 1.05
            blockers.append("low_activity_relax")
        elif activity_boost > 0.0:
            activity_state = "activity_boost"
            blockers.append("trade_frequency_floor")

        if freeze_recovery_mode:
            activity_state = "freeze_recovery"
            blockers.append("freeze_recovery")
        if fill_quality_tier in {"weak", "poor"}:
            blockers.append(f"fill_quality_{fill_quality_tier}")

        range_maker_aggression_factor = 1.0
        if strategy_mode == "RANGE_MAKER" or active_regime == "RANGE":
            range_maker_aggression_factor = 0.88
            entry_trigger_multiplier *= range_maker_aggression_factor
            min_edge_multiplier *= 0.90

        trend_threshold_multiplier = clamp(
            adaptive.threshold_multiplier
            * entry_trigger_multiplier
            * (1.0 + max(risk_score - 0.72, 0.0) * 0.12),
            0.52,
            1.30,
        )
        max_chase_bps_multiplier = clamp(
            0.98 - (risk_score * 0.20) + max(market.market_score, 0.0) * 0.10,
            0.55,
            1.02,
        )
        if feed_state == "CAUTION":
            max_chase_bps_multiplier *= 0.94
        elif feed_state == "BLOCK":
            max_chase_bps_multiplier *= 0.82

        inventory_skew_multiplier = clamp(
            1.0
            + (risk_score * 0.18)
            + (activity_boost * 0.10)
            + (0.08 if mm_mode == "aggressive" else -0.03 if mm_mode == "defensive_mm" else 0.0)
            + (0.05 if feed_state == "CAUTION" else 0.12 if feed_state == "BLOCK" else 0.0),
            0.80,
            1.55,
        )

        bias_strength = TREND_DIRECTIONAL_BIAS_FACTOR if market.regime == "TREND" else RANGE_DIRECTIONAL_BIAS_FACTOR
        if market.regime == "RISK_OFF":
            bias_strength = 0.25
        if feed_state == "CAUTION":
            bias_strength *= 0.92
        elif feed_state == "BLOCK":
            bias_strength *= 0.70
        directional_bias = clamp(signal_score * max(confidence, 0.35) * bias_strength, -1.0, 1.0)

        inventory_exit_threshold = effective_inventory_cap * (1.0 + max(OVERWEIGHT_EXIT_BUFFER_PCT, 0.0))
        severe_inventory_exit_threshold = effective_inventory_cap * (
            1.0 + max(OVERWEIGHT_EXIT_BUFFER_PCT, 0.0) + 0.08
        )

        strategy_mode = "RANGE_MAKER"
        quote_enabled = True
        if market.regime == "WARMUP":
            strategy_mode = "NO_TRADE"
            quote_enabled = False
            blockers.append("warmup")
        elif drawdown_stage == "pause":
            strategy_mode = "OVERWEIGHT_EXIT" if inventory_usd > 0 else "RANGE_MAKER"
            quote_enabled = strategy_mode != "NO_TRADE"
            mm_mode = "defensive_mm"
            blockers.append("drawdown_pause_softened")
        elif market.volatility_state == "EXTREME":
            strategy_mode = "OVERWEIGHT_EXIT" if inventory_usd > 0 else "RANGE_MAKER"
            quote_enabled = True
            blockers.append("capital_preservation_softened")
        elif inventory_usd > max(severe_inventory_exit_threshold, 0.0):
            strategy_mode = "OVERWEIGHT_EXIT"
            mm_mode = "defensive_mm"
            blockers.append("inventory_excess_severe")
        elif active_regime == "TREND" and trend_direction == "up":
            strategy_mode = "TREND_UP"
        else:
            strategy_mode = "RANGE_MAKER"

        if market.regime == "RISK_OFF":
            blockers.append("risk_off_defensive")
            mm_mode = "defensive_mm"
            if inventory_usd > max(severe_inventory_exit_threshold, 0.0):
                strategy_mode = "OVERWEIGHT_EXIT"

        if active_regime == "TREND" and trend_direction == "down":
            target_inventory_pct = min(target_inventory_pct, 0.45)
            directional_bias = min(directional_bias, -0.18)
            trade_size_multiplier *= 0.92
            blockers.append("trend_down_defensive_skew")
        elif active_regime == "RANGE":
            directional_bias = clamp(directional_bias * 0.72, -0.45, 0.45)

        if drawdown_stage in {"size_reduce", "aggression_reduce", "pause"}:
            blockers.append("drawdown_size_reduce")
            max_inventory_multiplier = min(max_inventory_multiplier, 0.88)
            trade_size_multiplier = min(trade_size_multiplier, 0.72)
            spread_multiplier = max(spread_multiplier, 1.08)
            target_inventory_pct = min(target_inventory_pct, 0.52)
            if strategy_mode == "TREND_UP":
                strategy_mode = "RANGE_MAKER"
            if mm_mode == "aggressive":
                mm_mode = "base_mm"

        if drawdown_stage in {"aggression_reduce", "pause"}:
            blockers.append("drawdown_aggression_reduce")
            confidence = clamp(confidence * 0.78, 0.0, 1.0)
            spread_multiplier = max(spread_multiplier, 1.15)
            max_chase_bps_multiplier = clamp(max_chase_bps_multiplier * 0.68, 0.35, 1.02)
            directional_bias *= 0.58
            target_inventory_pct = min(target_inventory_pct, 0.44)
            mm_mode = "defensive_mm"
            if strategy_mode == "TREND_UP":
                strategy_mode = "RANGE_MAKER"

        buy_enabled = strategy_mode in {"TREND_UP", "RANGE_MAKER"} and risk_score < 1.30
        sell_enabled = strategy_mode in {"TREND_UP", "RANGE_MAKER", "OVERWEIGHT_EXIT"}

        if not quote_enabled or strategy_mode == "NO_TRADE":
            buy_enabled = False
            sell_enabled = False
        elif strategy_mode == "OVERWEIGHT_EXIT":
            buy_enabled = False

        if strategy_mode != "NO_TRADE":
            if active_regime == "TREND" and trend_direction == "down":
                sell_enabled = sell_enabled or inventory_usd > 0
                trade_size_multiplier = min(trade_size_multiplier, 0.88)
                target_inventory_pct = min(target_inventory_pct, 0.45)
                directional_bias = min(directional_bias, -0.18)
                blockers.append("ema_downtrend_sell_bias")
                if strategy_mode == "TREND_UP":
                    strategy_mode = "RANGE_MAKER"
            elif active_regime == "TREND" and trend_direction == "up" and strategy_mode != "OVERWEIGHT_EXIT":
                directional_bias = max(directional_bias, 0.12)
                blockers.append("trend_up_buy_bias")
            elif active_regime == "RANGE":
                blockers.append("range_mean_reversion")
                blockers.append("ema_range_dual_side")
            else:
                blockers.append("ema_range_dual_side")

        if strategy_mode != "NO_TRADE" and buy_enabled and momentum_bps <= -max(TREND_MOMENTUM_BLOCK_BPS, 0.0):
            trade_size_multiplier *= 0.92
            spread_multiplier *= 1.04
            blockers.append("strong_drop_buy_soft_guard")
        if (
            strategy_mode not in {"NO_TRADE", "OVERWEIGHT_EXIT"}
            and sell_enabled
            and momentum_bps >= max(TREND_MOMENTUM_BLOCK_BPS, 0.0)
        ):
            trade_size_multiplier *= 0.92
            spread_multiplier *= 1.04
            blockers.append("strong_rally_sell_soft_guard")

        if buy_enabled:
            buy_inventory_cap_pct = target_inventory_pct
            if strategy_mode == "RANGE_MAKER":
                buy_inventory_cap_pct += 0.08
            else:
                buy_inventory_cap_pct += 0.04

            buy_inventory_cap_usd = min(
                effective_inventory_cap,
                current_equity * clamp(buy_inventory_cap_pct, 0.0, 1.0),
            )
            if inventory_usd >= buy_inventory_cap_usd:
                buy_enabled = False
                blockers.append("inventory_buy_cap")

        if feed_state == "BLOCK" and buy_enabled:
            buy_enabled = False
            blockers.append("feed_block")
        elif feed_state == "CAUTION":
            blockers.append("feed_caution")

        if soft_inventory_pressure > 0.0:
            trade_size_multiplier *= clamp(1.0 - (soft_inventory_pressure * 0.12), 0.80, 1.0)
            if strategy_mode == "RANGE_MAKER":
                spread_multiplier *= clamp(1.0 - min(soft_inventory_pressure, 0.5) * 0.07, 0.92, 1.0)
            directional_bias = min(directional_bias, 0.18)
        if hard_inventory_pressure > 0.0:
            sell_enabled = True
            buy_enabled = False
            spread_multiplier *= 0.98
            trade_size_multiplier *= clamp(1.0 - (min(hard_inventory_pressure, 1.0) * 0.10), 0.75, 1.0)
            active_regime = "RANGE"
            strategy_mode = "OVERWEIGHT_EXIT" if inventory_usd > 0 else strategy_mode
            mm_mode = "defensive_mm"
            blockers.append("hard_inventory_reduction_only")

        spread_multiplier = clamp(spread_multiplier, MIN_SPREAD_MULTIPLIER, MAX_SPREAD_MULTIPLIER)
        trade_size_multiplier = clamp(
            trade_size_multiplier,
            MIN_TRADE_SIZE_MULTIPLIER,
            MAX_TRADE_SIZE_MULTIPLIER,
        )
        cooldown_multiplier = fill_quality_cooldown_multiplier
        if activity_boost > 0.0:
            cooldown_multiplier *= 1.0 - (activity_boost * 0.18)
        if freeze_recovery_mode:
            cooldown_multiplier *= 0.82
        cooldown_multiplier = clamp(cooldown_multiplier, 0.55, 1.40)

        reason_parts = [market.regime.lower(), market.volatility_state.lower(), feed_state.lower()]
        reason_parts.append(mm_mode)
        if news_signal.score >= 0.25:
            reason_parts.append("news_tailwind")
        elif news_signal.score <= -0.25:
            reason_parts.append("news_headwind")
        if macro_signal.blocked:
            reason_parts.append("macro_event")
        elif macro_signal.score >= 0.20:
            reason_parts.append("macro_support")
        elif macro_signal.score <= -0.20:
            reason_parts.append("macro_headwind")
        if onchain_signal.score >= 0.25:
            reason_parts.append("onchain_bid")
        elif onchain_signal.score <= -0.25:
            reason_parts.append("onchain_stress")
        if blockers:
            reason_parts.extend(blockers)

        return IntelligenceSnapshot(
            mode=strategy_mode,
            current_mode=current_mode,
            reason=" | ".join(reason_parts[:6]),
            feed_state=feed_state,
            regime=market.regime,
            volatility_state=market.volatility_state,
            short_ma=market.short_ma,
            long_ma=market.long_ma,
            volatility=market.volatility,
            trend_strength=market.trend_strength,
            market_score=market.market_score,
            feed_score=feed_score,
            news_score=news_signal.score,
            macro_score=macro_signal.score,
            onchain_score=onchain_signal.score,
            adaptive_score=adaptive.performance_score,
            signal_score=signal_score,
            risk_score=risk_score,
            confidence=confidence,
            buy_enabled=buy_enabled,
            sell_enabled=sell_enabled,
            spread_multiplier=spread_multiplier,
            max_inventory_multiplier=max_inventory_multiplier,
            trade_size_multiplier=trade_size_multiplier,
            target_inventory_pct=target_inventory_pct,
            trend_threshold_multiplier=trend_threshold_multiplier,
            max_chase_bps_multiplier=max_chase_bps_multiplier,
            inventory_skew_multiplier=inventory_skew_multiplier,
            directional_bias=directional_bias,
            active_regime=active_regime,
            trend_direction=trend_direction,
            activity_state=activity_state,
            min_edge_multiplier=min_edge_multiplier,
            entry_trigger_multiplier=entry_trigger_multiplier,
            mm_mode=mm_mode,
            strategy_mode=strategy_mode,
            activity_boost=activity_boost,
            quote_enabled=quote_enabled,
            aggressive_enabled=mm_mode == "aggressive" and strategy_mode != "NO_TRADE",
            freeze_recovery_mode=freeze_recovery_mode,
            minutes_since_last_fill=minutes_since_last_fill,
            trades_last_60m=recent_trade_count_60m,
            fill_quality_tier=fill_quality_tier,
            fill_quality_score=normalized_fill_quality_score,
            cooldown_multiplier=cooldown_multiplier,
            blockers=blockers,
        )
