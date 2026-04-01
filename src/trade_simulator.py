from __future__ import annotations

from types_bot import (
    ExecutionContext,
    ExecutionPolicy,
    ExecutionSignal,
    ExecutionSlice,
    MevRiskAssessment,
    QuoteValidationResult,
    SimulationResult,
    SlippageEstimate,
)


def _apply_slippage(side: str, price: float, slippage_bps: float) -> float:
    if price <= 0:
        return price

    slippage_multiplier = slippage_bps / 10000.0
    if side == "buy":
        return price * (1.0 + slippage_multiplier)
    return price * (1.0 - slippage_multiplier)


class TradeSimulator:
    def simulate(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
        execution_mode: str,
        quote_validation: QuoteValidationResult,
        slippage: SlippageEstimate,
        mev_risk: MevRiskAssessment,
        slices: list[ExecutionSlice],
        fee_bps: float,
    ) -> SimulationResult:
        if not quote_validation.is_valid:
            return SimulationResult(
                success=False,
                estimated_price=0.0,
                realized_slippage_bps=0.0,
                estimated_cost_usd=0.0,
                block_reason=quote_validation.block_reason,
            )

        if not slippage.is_valid:
            return SimulationResult(
                success=False,
                estimated_price=0.0,
                realized_slippage_bps=0.0,
                estimated_cost_usd=0.0,
                block_reason=slippage.block_reason,
            )

        if execution_mode == "guarded_public" and not mev_risk.public_swap_allowed:
            return SimulationResult(
                success=False,
                estimated_price=0.0,
                realized_slippage_bps=0.0,
                estimated_cost_usd=0.0,
                block_reason="public_swap_risk_blocked",
            )

        if execution_mode == "guarded_public" and context.gas_price_gwei > policy.max_gas_spike_gwei:
            return SimulationResult(
                success=False,
                estimated_price=0.0,
                realized_slippage_bps=0.0,
                estimated_cost_usd=0.0,
                block_reason="gas_spike_guard",
            )

        mode_multiplier = {
            "private_tx": 0.75,
            "cow_intent": 0.60,
            "guarded_public": 1.05,
        }.get(execution_mode, 1.0)
        slice_discount = max(1.0 - (max(len(slices) - 1, 0) * 0.08), 0.72)
        realized_slippage_bps = slippage.expected_slippage_bps * mode_multiplier * slice_discount

        if execution_mode == "guarded_public":
            realized_slippage_bps *= 1.0 + (mev_risk.sandwich_risk * 0.35)

        estimated_price = _apply_slippage(signal.side, quote_validation.router_price, realized_slippage_bps)
        estimated_cost_usd = signal.size_usd * ((fee_bps + realized_slippage_bps) / 10000.0)

        if realized_slippage_bps > policy.max_slippage_bps:
            return SimulationResult(
                success=False,
                estimated_price=estimated_price,
                realized_slippage_bps=realized_slippage_bps,
                estimated_cost_usd=estimated_cost_usd,
                block_reason="simulated_slippage_too_high",
            )

        return SimulationResult(
            success=True,
            estimated_price=estimated_price,
            realized_slippage_bps=realized_slippage_bps,
            estimated_cost_usd=estimated_cost_usd,
            notes={
                "slice_count": len(slices),
                "mode_multiplier": mode_multiplier,
                "slice_discount": slice_discount,
            },
        )
