from __future__ import annotations

from config import (
    AGGRESSIVE_SIZE_MULT,
    DEFENSIVE_SIZE_MULT,
    EDGE_SCORE_MIN,
    EDGE_SCORE_MIN_REENTRY,
    EDGE_SOFT_NEG,
    EDGE_STRONG_POS,
    EDGE_WEAK_POS,
    ENABLE_EDGE_FILTER,
    ESTIMATED_SWAP_GAS_UNITS,
    EXECUTION_MAKER_SLIPPAGE_BPS,
    EXPECTED_EDGE_MIN_BPS,
    EXPECTED_EDGE_MIN_USD,
    HIGH_EDGE_OVERRIDE_SCORE,
    INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS,
    INVENTORY_SAME_SIDE_ENTRY_PENALTY_BPS,
    MAKER_FEE_BPS,
    MAX_CONSECUTIVE_LOSSES_BEFORE_PAUSE,
    REENTRY_BLOCK_AFTER_LOSS_MINUTES,
    REENTRY_EDGE_SCORE_MIN,
    REENTRY_MIN_PULLBACK_PCT,
    TAKER_FEE_BPS,
)
from metrics_window import clamp
from mev_risk_engine import MevRiskEngine
from policy_engine import PolicyEngine
from quote_validator import QuoteValidator
from slippage_guard import SlippageGuard
from types_bot import EdgeAssessment, ExecutionContext, ExecutionSignal, InventoryProfile, MarketRegimeAssessment

PROTECTIVE_SELL_REASONS = {
    "failsafe_sell",
    "inventory_force_reduce",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_exit_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
}

TAKER_LIKE_REASONS = {
    "failsafe_sell",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_exit_sell",
    "force_trade_buy",
    "force_trade_sell",
    "partial_reset",
    "profit_lock_level_1",
    "profit_lock_level_2",
    "reentry_pullback",
    "reentry_max_miss",
    "reentry_runaway",
    "reentry_timeout",
}


def is_reentry_reason(reason: str) -> bool:
    return reason.startswith("reentry_")


def is_protective_exit(side: str, reason: str) -> bool:
    return side.lower() == "sell" and reason in PROTECTIVE_SELL_REASONS


def _inventory_adjustment_usd(side: str, inventory_usd: float, target_base_usd: float, size_usd: float, regime: str) -> float:
    imbalance_ratio = (inventory_usd - target_base_usd) / max(size_usd, 1.0)
    if side == "buy":
        if imbalance_ratio > 0:
            return -min(imbalance_ratio * 0.04 * size_usd, size_usd * 0.05)
        if imbalance_ratio < 0 and regime == "RANGE":
            return min(abs(imbalance_ratio) * 0.02 * size_usd, size_usd * 0.03)
    else:
        if imbalance_ratio > 0:
            return min(abs(imbalance_ratio) * 0.02 * size_usd, size_usd * 0.03)
        if imbalance_ratio < 0:
            return -min(abs(imbalance_ratio) * 0.04 * size_usd, size_usd * 0.05)
    return 0.0


def _inventory_limit_bias_usd(
    *,
    side: str,
    inventory_usd: float,
    target_base_usd: float,
    size_usd: float,
    inventory_profile: InventoryProfile | None,
) -> float:
    if inventory_profile is None or size_usd <= 0 or not inventory_profile.soft_limit_hit:
        return 0.0

    delta_usd = inventory_usd - target_base_usd
    if abs(delta_usd) <= 1e-9:
        return 0.0

    same_side = (side == "buy" and delta_usd > 0.0) or (side == "sell" and delta_usd < 0.0)
    bias_bps = -INVENTORY_SAME_SIDE_ENTRY_PENALTY_BPS if same_side else INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS
    if inventory_profile.hard_limit_hit and not same_side:
        bias_bps += INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS * 0.50
    if inventory_profile.force_limit_hit and not same_side:
        bias_bps += INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS
    return size_usd * bias_bps / 10_000.0


class EdgeFilter:
    def __init__(
        self,
        *,
        enabled: bool = ENABLE_EDGE_FILTER,
        policy_engine: PolicyEngine | None = None,
        quote_validator: QuoteValidator | None = None,
        slippage_guard: SlippageGuard | None = None,
        mev_risk_engine: MevRiskEngine | None = None,
    ) -> None:
        self.enabled = enabled
        self.policy_engine = policy_engine or PolicyEngine()
        self.quote_validator = quote_validator or QuoteValidator()
        self.slippage_guard = slippage_guard or SlippageGuard()
        self.mev_risk_engine = mev_risk_engine or MevRiskEngine()

    @staticmethod
    def _fee_bps(reason: str) -> float:
        if reason.startswith("force_trade_") or is_reentry_reason(reason):
            return TAKER_FEE_BPS
        return max(MAKER_FEE_BPS, 0.5)

    @staticmethod
    def _slippage_cost_bps(reason: str, expected_slippage_bps: float) -> float:
        effective_slippage_bps = max(expected_slippage_bps, 0.0)
        if effective_slippage_bps <= 0:
            return 0.0
        if reason in TAKER_LIKE_REASONS or reason.startswith("force_trade_") or is_reentry_reason(reason):
            return effective_slippage_bps
        return max(EXECUTION_MAKER_SLIPPAGE_BPS, effective_slippage_bps * 0.35)

    @staticmethod
    def _gas_estimate_usd(context: ExecutionContext) -> float:
        return max(
            (context.gas_price_gwei * ESTIMATED_SWAP_GAS_UNITS * max(context.mid_price, 0.0)) / 1_000_000_000.0,
            0.0,
        )

    @staticmethod
    def _pullback_depth_pct(last_sell_price: float | None, current_price: float, side: str) -> float:
        if side.lower() != "buy" or last_sell_price is None or last_sell_price <= 0 or current_price <= 0:
            return 0.0
        return max(((last_sell_price / current_price) - 1.0) * 100.0, 0.0)

    @staticmethod
    def _expected_capture_pct(
        *,
        side: str,
        reason: str,
        current_price: float,
        regime_assessment: MarketRegimeAssessment,
        profit_pct: float | None,
        last_sell_price: float | None,
    ) -> float:
        if current_price <= 0:
            return 0.0

        if is_protective_exit(side, reason):
            return max(profit_pct or 0.10, 0.10)

        if side == "buy":
            if is_reentry_reason(reason) and last_sell_price and last_sell_price > 0:
                recovery_target = min(
                    max(regime_assessment.window_mean, current_price),
                    last_sell_price,
                )
                return max(((recovery_target / current_price) - 1.0) * 100.0, 0.0)
            if regime_assessment.execution_regime == "RANGE":
                target = max(regime_assessment.window_mean, current_price)
                return max(((target / current_price) - 1.0) * 100.0, 0.0)
            if regime_assessment.execution_regime == "TREND" and regime_assessment.trend_direction == "up":
                continuation_pct = max(regime_assessment.net_move_pct, 0.0) * 0.55
                return max(continuation_pct, 0.10)
            return 0.0

        if regime_assessment.execution_regime == "RANGE":
            target = regime_assessment.window_mean if regime_assessment.window_mean > 0 else current_price
            return max(((current_price / target) - 1.0) * 100.0, 0.0)
        if regime_assessment.execution_regime == "TREND" and regime_assessment.trend_direction == "down":
            return max(abs(regime_assessment.net_move_pct) * 0.35, 0.10)
        return max(profit_pct or 0.05, 0.05)

    def assess(
        self,
        *,
        signal,
        context: ExecutionContext,
        regime_assessment: MarketRegimeAssessment,
        inventory_usd: float,
        target_base_usd: float,
        consecutive_losses: int,
        last_loss_cycle: int | None,
        last_loss_reason: str,
        cycle_index: int,
        cycle_seconds: float,
        last_sell_price: float | None,
        current_profit_pct: float | None,
        inventory_profile: InventoryProfile | None = None,
        min_edge_multiplier: float = 1.0,
        min_edge_bps: float | None = None,
        force_trade_active: bool = False,
        inventory_emergency_override: bool = False,
    ) -> EdgeAssessment:
        if signal.action not in {"BUY", "SELL"} or signal.size_usd <= 0:
            return EdgeAssessment(0.0, 0.0, 0.0, 0.0, False, "no_signal")

        side = signal.action.lower()
        signal_filter_values = getattr(signal, "filter_values", {}) or {}
        recovery_mode_active = bool(signal_filter_values.get("recovery_mode_active", False))
        active_regime = str(signal_filter_values.get("active_regime", "") or "")
        strategy_mode = str(signal_filter_values.get("strategy_mode", "") or "")
        if not self.enabled:
            return EdgeAssessment(
                expected_edge_usd=max(EXPECTED_EDGE_MIN_USD, signal.size_usd * 0.0005),
                expected_edge_bps=max(EXPECTED_EDGE_MIN_BPS, 5.0),
                cost_estimate_usd=0.0,
                edge_score=100.0,
                edge_pass=True,
            )

        policy = self.policy_engine.resolve(context.pair, context.router)
        exec_signal = ExecutionSignal(
            side=side,
            size_usd=signal.size_usd,
            limit_price=signal.order_price if signal.order_price > 0 else context.router_price,
            trade_reason=signal.reason,
            mode=context.market_mode,
            source=signal.source,
            pair=context.pair,
            router=context.router,
            inventory_cap_usd=signal.inventory_cap_usd,
        )
        quote_validation = self.quote_validator.validate(exec_signal, context, policy)
        slippage = self.slippage_guard.evaluate(exec_signal, context, policy)
        mev_risk = self.mev_risk_engine.assess(exec_signal, context, policy, slippage, quote_validation)

        pullback_depth_pct = self._pullback_depth_pct(last_sell_price, exec_signal.limit_price, side)
        expected_capture_pct = self._expected_capture_pct(
            side=side,
            reason=signal.reason,
            current_price=exec_signal.limit_price,
            regime_assessment=regime_assessment,
            profit_pct=current_profit_pct,
            last_sell_price=last_sell_price,
        )
        expected_capture_bps = max(expected_capture_pct, 0.0) * 100.0
        expected_capture_usd = signal.size_usd * max(expected_capture_pct, 0.0) / 100.0

        fee_estimate_usd = signal.size_usd * (self._fee_bps(signal.reason) / 10_000.0)
        slippage_cost_bps = self._slippage_cost_bps(signal.reason, slippage.expected_slippage_bps)
        slippage_estimate_usd = signal.size_usd * slippage_cost_bps / 10_000.0
        base_qty = signal.size_usd / max(exec_signal.limit_price, 1e-9)
        if side == "buy":
            adverse_price_move_per_eth = max(context.mid_price - exec_signal.limit_price, 0.0)
        else:
            adverse_price_move_per_eth = max(exec_signal.limit_price - context.mid_price, 0.0)
        adverse_selection_usd = adverse_price_move_per_eth * base_qty
        gas_estimate_usd = self._gas_estimate_usd(context)
        mev_penalty_usd = signal.size_usd * max(mev_risk.mev_risk_score, 0.0) / 100_000.0

        regime_penalty_usd = 0.0
        if regime_assessment.market_regime == "CHOP":
            regime_penalty_usd += signal.size_usd * 0.0012
        elif regime_assessment.market_regime == "TREND_DOWN" and side == "buy":
            regime_penalty_usd += signal.size_usd * 0.0025
        elif regime_assessment.market_regime == "TREND_UP" and side == "sell" and not is_protective_exit(side, signal.reason):
            regime_penalty_usd += signal.size_usd * 0.0010

        loss_penalty_usd = signal.size_usd * max(consecutive_losses, 0) * 0.0005
        reentry_penalty_usd = 0.0
        if is_reentry_reason(signal.reason):
            if regime_assessment.market_regime == "TREND_UP" and regime_assessment.regime_confidence > 70.0:
                reentry_penalty_usd += signal.size_usd * 0.0020
            if last_loss_cycle is not None and cycle_index >= last_loss_cycle:
                minutes_since_loss = ((cycle_index - last_loss_cycle) * max(cycle_seconds, 1.0)) / 60.0
                if last_loss_reason.startswith("reentry_") and minutes_since_loss < REENTRY_BLOCK_AFTER_LOSS_MINUTES:
                    reentry_penalty_usd += signal.size_usd * 0.0035

        inventory_adjustment_usd = _inventory_adjustment_usd(
            side=side,
            inventory_usd=inventory_usd,
            target_base_usd=target_base_usd,
            size_usd=signal.size_usd,
            regime=regime_assessment.market_regime,
        )
        inventory_adjustment_usd += _inventory_limit_bias_usd(
            side=side,
            inventory_usd=inventory_usd,
            target_base_usd=target_base_usd,
            size_usd=signal.size_usd,
            inventory_profile=inventory_profile,
        )

        total_cost_usd = (
            fee_estimate_usd
            + slippage_estimate_usd
            + adverse_selection_usd
            + gas_estimate_usd
            + mev_penalty_usd
            + regime_penalty_usd
            + loss_penalty_usd
            + reentry_penalty_usd
        )
        expected_edge_usd = expected_capture_usd - total_cost_usd + inventory_adjustment_usd
        expected_edge_bps = (expected_edge_usd / max(signal.size_usd, 1.0)) * 10_000.0
        expected_edge_ratio = expected_edge_usd / max(signal.size_usd, 1.0)
        threshold_multiplier = clamp(min_edge_multiplier, 0.70, 2.00)
        required_edge_usd = EXPECTED_EDGE_MIN_USD * threshold_multiplier
        required_edge_bps = EXPECTED_EDGE_MIN_BPS * threshold_multiplier
        if min_edge_bps is not None:
            required_edge_bps = min_edge_bps
            required_edge_usd = signal.size_usd * (required_edge_bps / 10_000.0)
            if required_edge_bps >= 0:
                required_edge_usd = max(required_edge_usd, EXPECTED_EDGE_MIN_USD * threshold_multiplier)
        required_edge_score = EDGE_SCORE_MIN * threshold_multiplier
        required_reentry_score = max(EDGE_SCORE_MIN_REENTRY, REENTRY_EDGE_SCORE_MIN) * threshold_multiplier
        inventory_balance_ratio = abs(inventory_usd - target_base_usd) / max(target_base_usd, 1.0)
        balanced_inventory = inventory_balance_ratio <= 0.08
        effective_reentry_pullback_min = REENTRY_MIN_PULLBACK_PCT * (0.60 if balanced_inventory else 1.0)
        effective_reentry_score = required_reentry_score * (0.92 if balanced_inventory else 1.0)

        spread_quality = clamp(1.0 - (context.spread_bps / max(expected_capture_bps, 12.0)), 0.0, 1.0)
        edge_score = clamp(
            45.0
            + (expected_edge_bps * 0.65)
            + (regime_assessment.regime_confidence * 0.10)
            + (spread_quality * 12.0)
            - (regime_assessment.volatility_score * 0.12)
            - (consecutive_losses * 8.0)
            - max(mev_risk.mev_risk_score - 40.0, 0.0) * 0.25,
            0.0,
            100.0,
        )

        edge_bucket = "bad"
        if expected_edge_ratio > EDGE_STRONG_POS:
            edge_bucket = "strong_positive"
        elif expected_edge_ratio >= EDGE_WEAK_POS:
            edge_bucket = "weak_positive"
        elif expected_edge_ratio >= EDGE_SOFT_NEG:
            edge_bucket = "slightly_negative"

        size_multiplier = 1.0
        spread_multiplier = 1.0
        inventory_skew_multiplier = 1.0
        cooldown_multiplier = 1.0
        aggressive_enabled = False
        edge_override_reason = "inventory_emergency_override" if inventory_emergency_override else (
            "force_trade_active" if force_trade_active else ""
        )
        if edge_bucket == "strong_positive":
            size_multiplier = max(AGGRESSIVE_SIZE_MULT, 1.0)
            spread_multiplier = 0.82
            inventory_skew_multiplier = 1.35
            cooldown_multiplier = 0.60
            aggressive_enabled = True
        elif edge_bucket == "weak_positive":
            size_multiplier = 1.0 + (max(AGGRESSIVE_SIZE_MULT, 1.0) - 1.0) * 0.55
            spread_multiplier = 0.92
            inventory_skew_multiplier = 1.18
            cooldown_multiplier = 0.82
            aggressive_enabled = True
        elif edge_bucket == "slightly_negative" and not edge_override_reason:
            size_multiplier = max((1.0 + max(min(DEFENSIVE_SIZE_MULT, 1.0), 0.10)) / 2.0, 0.65)
            spread_multiplier = 1.12
            inventory_skew_multiplier = 0.92
            cooldown_multiplier = 1.15
        elif edge_bucket == "bad" and not edge_override_reason:
            size_multiplier = 0.55
            spread_multiplier = 1.16
            inventory_skew_multiplier = 0.88
            cooldown_multiplier = 1.18

        edge_reject_reason = ""
        edge_penalty_reason = ""
        if is_protective_exit(side, signal.reason):
            edge_score = max(edge_score, HIGH_EDGE_OVERRIDE_SCORE)
        elif not quote_validation.is_valid:
            edge_reject_reason = quote_validation.block_reason
        elif not slippage.is_valid:
            edge_reject_reason = slippage.block_reason
        elif not edge_override_reason and edge_bucket == "bad" and expected_edge_usd < 0:
            deficit_bps = abs(min(expected_edge_bps - required_edge_bps, 0.0))
            size_multiplier = min(size_multiplier, max(0.28, 0.48 - min(deficit_bps / 200.0, 0.16)))
            spread_multiplier = max(spread_multiplier, 1.18 + min(deficit_bps / 120.0, 0.18))
            inventory_skew_multiplier = min(inventory_skew_multiplier, 0.88)
            cooldown_multiplier = max(cooldown_multiplier, 1.18 + min(deficit_bps / 160.0, 0.20))
            edge_penalty_reason = "expected_edge_bad"
        elif not edge_override_reason and edge_bucket == "bad" and (
            expected_edge_bps < required_edge_bps or expected_edge_usd < required_edge_usd
        ):
            size_multiplier = min(size_multiplier, 0.60)
            spread_multiplier = max(spread_multiplier, 1.12)
            inventory_skew_multiplier = min(inventory_skew_multiplier, 0.90)
            cooldown_multiplier = max(cooldown_multiplier, 1.12)
            edge_penalty_reason = "expected_edge_below_min"
        elif is_reentry_reason(signal.reason) and pullback_depth_pct < effective_reentry_pullback_min:
            edge_reject_reason = "reentry_low_pullback"
        elif (
            is_reentry_reason(signal.reason)
            and regime_assessment.market_regime not in {"RANGE", "TREND_UP"}
            and not (
                recovery_mode_active
                and regime_assessment.market_regime == "CHOP"
                and active_regime == "RANGE"
                and strategy_mode == "RANGE_MAKER"
            )
        ):
            edge_reject_reason = "reentry_rejected_bad_regime"
        elif (
            is_reentry_reason(signal.reason)
            and regime_assessment.market_regime == "TREND_UP"
            and regime_assessment.regime_confidence > 70.0
            and not recovery_mode_active
        ):
            edge_reject_reason = "reentry_rejected_bad_regime"
        elif is_reentry_reason(signal.reason) and regime_assessment.volatility_score >= 75.0:
            edge_reject_reason = "reentry_high_volatility_shock"
        elif is_reentry_reason(signal.reason) and edge_score < effective_reentry_score and not recovery_mode_active:
            edge_reject_reason = "reentry_rejected_low_edge"
        elif not edge_override_reason and edge_bucket == "bad" and edge_score < required_edge_score:
            size_multiplier = min(size_multiplier, 0.72)
            spread_multiplier = max(spread_multiplier, 1.08)
            cooldown_multiplier = max(cooldown_multiplier, 1.08)
            edge_penalty_reason = edge_penalty_reason or "edge_score_too_low"

        if (
            is_reentry_reason(signal.reason)
            and last_loss_cycle is not None
            and cycle_index >= last_loss_cycle
            and last_loss_reason.startswith("reentry_")
        ):
            minutes_since_loss = ((cycle_index - last_loss_cycle) * max(cycle_seconds, 1.0)) / 60.0
            if minutes_since_loss < REENTRY_BLOCK_AFTER_LOSS_MINUTES and not edge_reject_reason:
                edge_reject_reason = "reentry_block_after_loss"

        return EdgeAssessment(
            expected_edge_usd=round(expected_edge_usd, 6),
            expected_edge_bps=round(expected_edge_bps, 6),
            cost_estimate_usd=round(total_cost_usd, 6),
            edge_score=round(edge_score, 6),
            edge_pass=edge_reject_reason == "",
            edge_reject_reason=edge_reject_reason,
            expected_capture_usd=round(expected_capture_usd, 6),
            expected_capture_bps=round(expected_capture_bps, 6),
            fee_estimate_usd=round(fee_estimate_usd, 6),
            slippage_estimate_bps=round(slippage_cost_bps, 6),
            slippage_estimate_usd=round(slippage_estimate_usd, 6),
            gas_estimate_usd=round(gas_estimate_usd, 6),
            mev_risk_score=round(mev_risk.mev_risk_score, 6),
            sandwich_risk=round(mev_risk.sandwich_risk, 6),
            mev_penalty_usd=round(mev_penalty_usd, 6),
            regime_penalty_usd=round(regime_penalty_usd, 6),
            loss_penalty_usd=round(loss_penalty_usd, 6),
            reentry_penalty_usd=round(reentry_penalty_usd, 6),
            inventory_adjustment_usd=round(inventory_adjustment_usd, 6),
            adverse_selection_usd=round(adverse_selection_usd, 6),
            pullback_depth_pct=round(pullback_depth_pct, 6),
            edge_bucket=edge_bucket,
            size_multiplier=round(size_multiplier, 6),
            spread_multiplier=round(spread_multiplier, 6),
            inventory_skew_multiplier=round(inventory_skew_multiplier, 6),
            cooldown_multiplier=round(cooldown_multiplier, 6),
            aggressive_enabled=aggressive_enabled,
            edge_penalty_reason=edge_penalty_reason,
            edge_override_reason=edge_override_reason,
        )
