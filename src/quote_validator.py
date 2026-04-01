from __future__ import annotations

from statistics import median

from types_bot import ExecutionContext, ExecutionPolicy, ExecutionSignal, QuoteValidationResult


def _deviation_bps(price_a: float, price_b: float) -> float:
    if price_a <= 0 or price_b <= 0:
        return 0.0
    return abs(price_a - price_b) / price_b * 10000.0


class QuoteValidator:
    def validate(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
    ) -> QuoteValidationResult:
        router_price = context.router_price
        if router_price <= 0:
            router_price = context.quote_ask if signal.side == "buy" else context.quote_bid
        if router_price <= 0:
            router_price = context.mid_price

        external_refs = [
            price
            for price in (context.backup_price, context.onchain_ref_price, context.mid_price)
            if price > 0
        ]
        reference_price = median(external_refs) if external_refs else router_price
        twap_price = context.twap_price if context.twap_price > 0 else reference_price

        quote_deviation_bps = _deviation_bps(router_price, reference_price)
        twap_deviation_bps = _deviation_bps(router_price, twap_price)

        block_reason = ""
        is_valid = True
        if quote_deviation_bps > policy.max_quote_deviation_bps:
            is_valid = False
            block_reason = "quote_deviation_too_high"
        elif twap_deviation_bps > policy.max_twap_deviation_bps:
            is_valid = False
            block_reason = "twap_deviation_too_high"

        return QuoteValidationResult(
            is_valid=is_valid,
            router_price=router_price,
            reference_price=reference_price,
            quote_deviation_bps=quote_deviation_bps,
            twap_deviation_bps=twap_deviation_bps,
            block_reason=block_reason,
            quotes={
                "router": router_price,
                "backup": context.backup_price,
                "onchain_ref": context.onchain_ref_price,
                "twap": twap_price,
            },
        )
