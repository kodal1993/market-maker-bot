from __future__ import annotations

from types_bot import ExecutionContext, ExecutionPolicy, ExecutionSignal, SlippageEstimate


class SlippageGuard:
    def evaluate(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
    ) -> SlippageEstimate:
        liquidity_usd = context.liquidity_usd if context.liquidity_usd > 0 else policy.liquidity_hint_usd
        liquidity_usd = max(liquidity_usd, signal.size_usd * 5.0, 1.0)
        size_ratio = signal.size_usd / liquidity_usd
        volatility_bps = max(context.volatility, 0.0) * 10000.0
        base_slippage_bps = max(1.0, max(context.spread_bps, 0.0) * 0.45)
        size_multiplier_bps = min(size_ratio * 8500.0, policy.max_price_impact_bps * 2.0)
        volatility_component_bps = min(volatility_bps * 0.30, policy.max_slippage_bps)

        expected_slippage_bps = base_slippage_bps + size_multiplier_bps + volatility_component_bps
        price_impact_bps = max(0.5, size_multiplier_bps + (max(context.spread_bps, 0.0) * 0.20))
        allowed_slippage_bps = max(2.0, policy.max_slippage_bps)

        is_valid = True
        block_reason = ""
        if price_impact_bps > policy.max_price_impact_bps:
            is_valid = False
            block_reason = "price_impact_too_high"
        elif expected_slippage_bps > allowed_slippage_bps:
            is_valid = False
            block_reason = "slippage_above_dynamic_guard"

        return SlippageEstimate(
            is_valid=is_valid,
            expected_slippage_bps=expected_slippage_bps,
            allowed_slippage_bps=allowed_slippage_bps,
            price_impact_bps=price_impact_bps,
            size_ratio=size_ratio,
            block_reason=block_reason,
        )
