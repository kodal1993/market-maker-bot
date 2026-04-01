from __future__ import annotations

from types_bot import (
    ExecutionContext,
    ExecutionPolicy,
    ExecutionSignal,
    MevRiskAssessment,
    QuoteValidationResult,
    SlippageEstimate,
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


class MevRiskEngine:
    def assess(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
        slippage: SlippageEstimate,
        quote_validation: QuoteValidationResult,
    ) -> MevRiskAssessment:
        liquidity_usd = context.liquidity_usd if context.liquidity_usd > 0 else policy.liquidity_hint_usd
        liquidity_usd = max(liquidity_usd, signal.size_usd * 5.0, 1.0)
        size_to_liquidity = signal.size_usd / liquidity_usd
        volatility_bps = max(context.volatility, 0.0) * 10000.0
        size_component = _clamp(size_to_liquidity * 20_000.0, 0.0, 35.0)
        gas_component = _clamp(
            (
                context.gas_price_gwei / max(policy.max_gas_spike_gwei, 1.0)
            )
            * 20.0,
            0.0,
            20.0,
        )
        volatility_component = _clamp(volatility_bps * 0.40, 0.0, 20.0)
        spread_component = _clamp(context.spread_bps * 0.60, 0.0, 15.0)
        quote_component = _clamp(quote_validation.quote_deviation_bps * 0.40, 0.0, 10.0)
        impact_component = _clamp(slippage.price_impact_bps * 0.80, 0.0, 15.0)
        slippage_component = _clamp(slippage.expected_slippage_bps * 0.25, 0.0, 10.0)

        execution_window_score = _clamp(
            1.0
            - (
                ((gas_component / 20.0) * 0.50)
                + ((volatility_component / 20.0) * 0.30)
                + ((spread_component / 15.0) * 0.20)
            ),
            0.0,
            1.0,
        )
        sandwich_risk = _clamp(
            ((size_component / 35.0) * 0.45)
            + ((spread_component / 15.0) * 0.20)
            + ((impact_component / 15.0) * 0.20)
            + ((gas_component / 20.0) * 0.15),
            0.0,
            1.0,
        )
        mev_risk_score = _clamp(
            size_component
            + gas_component
            + volatility_component
            + spread_component
            + quote_component
            + impact_component
            + slippage_component,
            0.0,
            100.0,
        )
        public_risk_limit = min(max(policy.public_swap_max_risk, 0.0), 40.0)
        skip_risk_limit = min(max(policy.mev_risk_threshold_block, 0.0), 70.0)
        if skip_risk_limit < public_risk_limit:
            skip_risk_limit = 70.0

        public_swap_allowed = False
        recommended_execution_mode = "guarded_public"
        block_reason = ""

        if mev_risk_score > skip_risk_limit:
            recommended_execution_mode = "skip"
            block_reason = "mev_risk_too_high"
        elif mev_risk_score >= public_risk_limit:
            recommended_execution_mode = "private_tx"
        else:
            public_swap_allowed = sandwich_risk <= 0.55
            recommended_execution_mode = "guarded_public"

        if context.gas_price_gwei > policy.max_gas_spike_gwei and recommended_execution_mode == "guarded_public":
            public_swap_allowed = False
            recommended_execution_mode = "private_tx"
            block_reason = "gas_spike_public_block"

        return MevRiskAssessment(
            mev_risk_score=mev_risk_score,
            sandwich_risk=sandwich_risk,
            execution_window_score=execution_window_score,
            recommended_execution_mode=recommended_execution_mode,
            public_swap_allowed=public_swap_allowed,
            block_reason=block_reason,
        )
