from __future__ import annotations

from config import (
    ACTIVITY_ALIGNMENT_BOOST_ENABLED,
    ACTIVITY_ALIGNMENT_BOOST_SIZE_MULTIPLIER,
    ACTIVITY_ALIGNMENT_BOOST_SPREAD_MULTIPLIER,
    ACTIVITY_ALIGNMENT_MAX_RISK_SCORE,
    ACTIVITY_ALIGNMENT_MIN_EDGE_SCORE,
    ACTIVITY_ALIGNMENT_MIN_FEED_SCORE,
    ACTIVITY_ALIGNMENT_MIN_ONCHAIN_SCORE,
    CHOP_DISABLE_NEW_TRADES,
    CONFIRMATION_MOMENTUM_SHOCK_BPS,
    EMA_RANGE_BAND_BPS,
    ENABLE_EXECUTION_CONFIRMATION,
    HIGH_EDGE_OVERRIDE_SCORE,
    LOSS_PAUSE_MINUTES,
    TREND_MOMENTUM_BLOCK_BPS,
    TREND_DOWN_DISABLE_RANGE_BUYS,
    TREND_UP_DISABLE_COUNTERTREND_SELLS,
)
from metrics_window import clamp
from types_bot import EdgeAssessment, MarketRegimeAssessment, SignalGateDecision

PROTECTIVE_SELL_REASONS = {
    "failsafe_sell",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_exit_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
    "force_trade_sell",
}


def inventory_state_label(inventory_ratio: float, target_base_pct: float) -> str:
    delta = inventory_ratio - target_base_pct
    if delta >= 0.08:
        return "base_heavy"
    if delta <= -0.08:
        return "quote_heavy"
    return "balanced"


def is_protective_exit(action: str, reason: str) -> bool:
    return action.upper() == "SELL" and reason in PROTECTIVE_SELL_REASONS


def ema_gap_bps(short_ma: float, long_ma: float) -> float:
    if short_ma <= 0 or long_ma <= 0:
        return 0.0
    return ((short_ma - long_ma) / long_ma) * 10000.0


class SignalGate:
    def evaluate(
        self,
        *,
        signal,
        strategy_mode: str,
        regime_assessment: MarketRegimeAssessment,
        edge_assessment: EdgeAssessment,
        inventory_ratio: float,
        target_base_pct: float,
        consecutive_losses: int,
        loss_pause_remaining_minutes: float,
        short_ma: float,
        long_ma: float,
        momentum_bps: float,
        confirmation_enabled: bool = ENABLE_EXECUTION_CONFIRMATION,
        confirmation_momentum_bps: float = 0.0,
        confirmation_slowing: bool = False,
    ) -> SignalGateDecision:
        action = signal.action.upper()
        reason = signal.reason or ""
        protective_exit = is_protective_exit(action, reason)
        inventory_emergency_override = bool(getattr(signal, "filter_values", {}).get("inventory_emergency_override")) or (
            edge_assessment.edge_override_reason == "inventory_emergency_override"
        )
        signal_filter_values = getattr(signal, "filter_values", {}) or {}
        feed_score = float(signal_filter_values.get("feed_score", 0.0) or 0.0)
        risk_score = float(signal_filter_values.get("risk_score", 0.0) or 0.0)
        onchain_score = float(signal_filter_values.get("onchain_score", 0.0) or 0.0)
        inventory_state = inventory_state_label(inventory_ratio, target_base_pct)
        ema_trend_gap_bps = ema_gap_bps(short_ma, long_ma)
        ema_range_band_bps = max(EMA_RANGE_BAND_BPS, 0.5)
        gate_size_multiplier = 1.0
        gate_spread_multiplier = 1.0
        soft_guard_reasons: list[str] = []

        gate_details = {
            "raw_signal": f"{action}:{signal.source or '-'}:{reason or '-'}",
            "market_regime": regime_assessment.market_regime,
            "regime_confidence": round(regime_assessment.regime_confidence, 6),
            "edge_score": round(edge_assessment.edge_score, 6),
            "expected_edge_usd": round(edge_assessment.expected_edge_usd, 6),
            "expected_edge_bps": round(edge_assessment.expected_edge_bps, 6),
            "edge_bucket": edge_assessment.edge_bucket,
            "edge_size_multiplier": round(edge_assessment.size_multiplier, 6),
            "edge_spread_multiplier": round(edge_assessment.spread_multiplier, 6),
            "edge_penalty_reason": edge_assessment.edge_penalty_reason,
            "edge_override_reason": edge_assessment.edge_override_reason,
            "aggressive_enabled": edge_assessment.aggressive_enabled,
            "mev_risk_score": round(edge_assessment.mev_risk_score, 6),
            "slippage_estimate_bps": round(edge_assessment.slippage_estimate_bps, 6),
            "inventory_state": inventory_state,
            "consecutive_losses": consecutive_losses,
            "loss_pause_remaining": round(loss_pause_remaining_minutes, 6),
            "ema_gap_bps": round(ema_trend_gap_bps, 6),
            "momentum_bps": round(momentum_bps, 6),
            "confirmation_enabled": confirmation_enabled,
            "confirmation_momentum_bps": round(confirmation_momentum_bps, 6),
            "confirmation_slowing": confirmation_slowing,
            "inventory_emergency_override": inventory_emergency_override,
            "feed_score": round(feed_score, 6),
            "risk_score": round(risk_score, 6),
            "onchain_score": round(onchain_score, 6),
        }
        if ema_trend_gap_bps > ema_range_band_bps:
            gate_details["upper_tf_bias"] = "buy_only"
        elif ema_trend_gap_bps < -ema_range_band_bps:
            gate_details["upper_tf_bias"] = "sell_only"
        else:
            gate_details["upper_tf_bias"] = "range"

        if action not in {"BUY", "SELL"}:
            return SignalGateDecision(False, "skip", "no_signal", gate_details)

        if (
            loss_pause_remaining_minutes > 0
            and not protective_exit
            and not inventory_emergency_override
            and edge_assessment.edge_score < HIGH_EDGE_OVERRIDE_SCORE
        ):
            gate_details["loss_pause_soft_degrade"] = True
            gate_size_multiplier *= 0.76
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("loss_pause_soft_degrade")

        if (
            CHOP_DISABLE_NEW_TRADES
            and regime_assessment.market_regime == "CHOP"
            and not protective_exit
            and not inventory_emergency_override
        ):
            gate_size_multiplier *= 0.82
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("chop_market_soft")

        if (
            not protective_exit
            and not inventory_emergency_override
            and action == "BUY"
            and ema_trend_gap_bps < -ema_range_band_bps
        ):
            gate_size_multiplier *= 0.72
            gate_spread_multiplier *= 1.10
            soft_guard_reasons.append("ema_downtrend_buy_soft")

        if (
            not protective_exit
            and not inventory_emergency_override
            and action == "SELL"
            and ema_trend_gap_bps > ema_range_band_bps
        ):
            gate_size_multiplier *= 0.74
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("ema_uptrend_sell_soft")

        if (
            not protective_exit
            and not inventory_emergency_override
            and action == "BUY"
            and momentum_bps <= -max(TREND_MOMENTUM_BLOCK_BPS, 0.0)
        ):
            gate_size_multiplier *= 0.72
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("momentum_drop_buy_soft")

        if (
            not protective_exit
            and not inventory_emergency_override
            and action == "SELL"
            and momentum_bps >= max(TREND_MOMENTUM_BLOCK_BPS, 0.0)
        ):
            gate_size_multiplier *= 0.72
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("momentum_rally_sell_soft")

        confirmation_threshold_bps = max(CONFIRMATION_MOMENTUM_SHOCK_BPS, 1.0)
        if (
            confirmation_enabled
            and not protective_exit
            and not inventory_emergency_override
            and action == "BUY"
            and confirmation_momentum_bps <= -confirmation_threshold_bps
            and not confirmation_slowing
        ):
            gate_size_multiplier *= 0.78
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("confirmation_blocks_buy_soft")

        if (
            confirmation_enabled
            and not protective_exit
            and not inventory_emergency_override
            and action == "SELL"
            and confirmation_momentum_bps >= confirmation_threshold_bps
            and not confirmation_slowing
        ):
            gate_size_multiplier *= 0.78
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("confirmation_blocks_sell_soft")

        if (
            TREND_DOWN_DISABLE_RANGE_BUYS
            and action == "BUY"
            and regime_assessment.market_regime == "TREND_DOWN"
            and not protective_exit
            and not inventory_emergency_override
        ):
            gate_size_multiplier *= 0.76
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("regime_blocks_countertrend_buy_soft")

        if (
            TREND_UP_DISABLE_COUNTERTREND_SELLS
            and action == "SELL"
            and regime_assessment.market_regime == "TREND_UP"
            and not protective_exit
            and not inventory_emergency_override
            and reason == "quoted_sell"
        ):
            gate_size_multiplier *= 0.78
            gate_spread_multiplier *= 1.08
            soft_guard_reasons.append("regime_blocks_countertrend_sell_soft")

        if not edge_assessment.edge_pass:
            return SignalGateDecision(False, "skip", edge_assessment.edge_reject_reason or "edge_filter_reject", gate_details)

        trend_aligned = (action == "BUY" and ema_trend_gap_bps > ema_range_band_bps and momentum_bps > 0) or (
            action == "SELL" and ema_trend_gap_bps < -ema_range_band_bps and momentum_bps < 0
        )
        high_quality_alignment = (
            ACTIVITY_ALIGNMENT_BOOST_ENABLED
            and not protective_exit
            and not inventory_emergency_override
            and trend_aligned
            and edge_assessment.edge_score >= ACTIVITY_ALIGNMENT_MIN_EDGE_SCORE
            and feed_score >= ACTIVITY_ALIGNMENT_MIN_FEED_SCORE
            and risk_score <= ACTIVITY_ALIGNMENT_MAX_RISK_SCORE
            and onchain_score >= ACTIVITY_ALIGNMENT_MIN_ONCHAIN_SCORE
        )
        if high_quality_alignment:
            gate_size_multiplier *= max(ACTIVITY_ALIGNMENT_BOOST_SIZE_MULTIPLIER, 1.0)
            gate_spread_multiplier *= max(min(ACTIVITY_ALIGNMENT_BOOST_SPREAD_MULTIPLIER, 1.0), 0.5)
            soft_guard_reasons.append("quality_alignment_boost")

        approved_mode = strategy_mode or "execute"
        if regime_assessment.market_regime == "RANGE" and action == "BUY":
            approved_mode = "range_entry"
        elif regime_assessment.market_regime == "TREND_UP" and action == "BUY":
            approved_mode = "trend_follow_buy"
        elif action == "SELL" and protective_exit:
            approved_mode = "risk_exit"

        gate_details["gate_size_multiplier"] = round(gate_size_multiplier, 6)
        gate_details["gate_spread_multiplier"] = round(gate_spread_multiplier, 6)
        gate_details["soft_guard_reasons"] = soft_guard_reasons
        gate_details["soft_guard_reason"] = "|".join(soft_guard_reasons)
        gate_details["approved_mode"] = approved_mode
        gate_details["gate_decision"] = "allow"
        return SignalGateDecision(True, approved_mode, "", gate_details)


def loss_pause_remaining_minutes(cycle_index: int, pause_until_cycle: int | None, cycle_seconds: float) -> float:
    if pause_until_cycle is None:
        return 0.0
    remaining_cycles = max(pause_until_cycle - cycle_index, 0)
    return max((remaining_cycles * max(cycle_seconds, 1.0)) / 60.0, 0.0)


def loss_pause_cycles(minutes: float, cycle_seconds: float) -> int:
    if minutes <= 0:
        return 0
    return max(int(round((minutes * 60.0) / max(cycle_seconds, 1.0))), 1)


def capped_loss_pause_minutes(minutes: float = LOSS_PAUSE_MINUTES) -> float:
    return clamp(minutes, 0.0, 24.0 * 60.0)
