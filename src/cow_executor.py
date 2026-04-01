from __future__ import annotations

from config import COW_MIN_NOTIONAL_USD, COW_SUPPORTED_PAIRS, ENABLE_COW, MAKER_FEE_BPS
from types_bot import ExecutionContext, ExecutionPolicy, ExecutionResult, ExecutionSignal


class CowExecutor:
    def __init__(
        self,
        enabled: bool = ENABLE_COW,
        min_notional_usd: float = COW_MIN_NOTIONAL_USD,
        supported_pairs: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.min_notional_usd = min_notional_usd
        self.supported_pairs = {
            pair.strip().upper()
            for pair in (supported_pairs if supported_pairs is not None else COW_SUPPORTED_PAIRS)
        }

    def supports(self, pair: str, size_usd: float, policy: ExecutionPolicy) -> bool:
        return bool(
            self.enabled
            and policy.allow_cow
            and policy.cow_supported
            and pair.strip().upper() in self.supported_pairs
            and size_usd >= max(self.min_notional_usd, policy.cow_min_notional_usd)
        )

    def execute(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
    ) -> ExecutionResult:
        if not self.supports(signal.pair, signal.size_usd, policy):
            return ExecutionResult(
                allow_trade=False,
                execution_mode="skip",
                private_tx_used=False,
                cow_used=False,
                quoted_price=context.router_price,
                order_price=0.0,
                size_usd=signal.size_usd,
                fee_bps=0.0,
                execution_type="maker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
                trade_blocked_reason="cow_not_supported",
            )

        return ExecutionResult(
            allow_trade=True,
            execution_mode="cow_intent",
            private_tx_used=False,
            cow_used=True,
            quoted_price=context.router_price,
            order_price=context.router_price,
            size_usd=signal.size_usd,
            fee_bps=MAKER_FEE_BPS,
            execution_type="taker",
            mev_risk_score=0.0,
            sandwich_risk=0.0,
            execution_window_score=0.0,
            expected_slippage_bps=0.0,
            realized_slippage_bps=0.0,
            price_impact_bps=0.0,
            quote_deviation_bps=0.0,
            metadata={
                "intent_pair": signal.pair,
                "min_notional_usd": max(self.min_notional_usd, policy.cow_min_notional_usd),
            },
        )
