from __future__ import annotations

from config import ESTIMATED_SWAP_GAS_UNITS, MAX_GAS_TO_PROFIT_RATIO
from config import TAKER_FEE_BPS
from cow_executor import CowExecutor
from mev_risk_engine import MevRiskEngine
from order_slicer import OrderSlicer
from policy_engine import PolicyEngine
from private_tx_executor import PrivateTxExecutor
from quote_validator import QuoteValidator
from slippage_guard import SlippageGuard
from trade_simulator import TradeSimulator
from types_bot import ExecutionContext, ExecutionResult, ExecutionSignal


PAPER_ACTIVITY_GAS_EXEMPT_KEYS = (
    "paper_activity_override",
    "activity_floor_force",
    "force_trade_active",
    "inventory_emergency_override",
)


class ExecutionRouter:
    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        private_tx_executor: PrivateTxExecutor | None = None,
        cow_executor: CowExecutor | None = None,
        slippage_guard: SlippageGuard | None = None,
        mev_risk_engine: MevRiskEngine | None = None,
        quote_validator: QuoteValidator | None = None,
        order_slicer: OrderSlicer | None = None,
        trade_simulator: TradeSimulator | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.private_tx_executor = private_tx_executor or PrivateTxExecutor()
        self.cow_executor = cow_executor or CowExecutor()
        self.slippage_guard = slippage_guard or SlippageGuard()
        self.mev_risk_engine = mev_risk_engine or MevRiskEngine()
        self.quote_validator = quote_validator or QuoteValidator()
        self.order_slicer = order_slicer or OrderSlicer()
        self.trade_simulator = trade_simulator or TradeSimulator()

    @staticmethod
    def _metadata_float(metadata: dict[str, object], key: str) -> float | None:
        raw_value = metadata.get(key)
        if raw_value is None or raw_value == "":
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _metadata_bool(metadata: dict[str, object], key: str) -> bool:
        raw_value = metadata.get(key)
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return raw_value != 0
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _paper_activity_gas_exempt(self, metadata: dict[str, object]) -> bool:
        if not self._metadata_bool(metadata, "paper_mode"):
            return False
        return any(self._metadata_bool(metadata, key) for key in PAPER_ACTIVITY_GAS_EXEMPT_KEYS)

    def _gas_guard_metrics(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
    ) -> dict[str, object]:
        metadata = dict(signal.metadata or {})
        expected_profit_pct = self._metadata_float(metadata, "expected_profit_pct")
        expected_profit_usd = self._metadata_float(metadata, "expected_profit_usd")
        if expected_profit_usd is None and expected_profit_pct is not None:
            expected_profit_usd = max(signal.size_usd * (expected_profit_pct / 100.0), 0.0)

        gas_cost_usd = max(
            (context.gas_price_gwei * ESTIMATED_SWAP_GAS_UNITS * max(context.mid_price, 0.0)) / 1_000_000_000.0,
            0.0,
        )
        gas_to_profit_ratio = None
        if expected_profit_usd is not None and expected_profit_usd > 0:
            gas_to_profit_ratio = gas_cost_usd / expected_profit_usd

        metrics: dict[str, object] = {
            "gas_price_gwei": round(context.gas_price_gwei, 6),
            "estimated_gas_cost_usd": round(gas_cost_usd, 6),
        }
        if expected_profit_pct is not None:
            metrics["expected_profit_pct"] = round(expected_profit_pct, 6)
        if expected_profit_usd is not None:
            metrics["expected_profit_usd"] = round(expected_profit_usd, 6)
        if gas_to_profit_ratio is not None:
            metrics["gas_to_profit_ratio"] = round(gas_to_profit_ratio, 6)
        if self._paper_activity_gas_exempt(metadata):
            metrics["paper_activity_gas_exempt"] = True
        return metrics

    def _gas_guard_block_reason(
        self,
        context: ExecutionContext,
        policy,
        metrics: dict[str, object],
    ) -> str:
        if context.gas_price_gwei > policy.max_gas_spike_gwei:
            return "gas_spike_skip"

        if bool(metrics.get("paper_activity_gas_exempt")):
            metrics["gas_profit_guard_bypassed"] = True
            return ""

        expected_profit_usd = self._metadata_float(metrics, "expected_profit_usd")
        gas_to_profit_ratio = self._metadata_float(metrics, "gas_to_profit_ratio")
        if expected_profit_usd is None or expected_profit_usd <= 0 or gas_to_profit_ratio is None:
            return ""

        if gas_to_profit_ratio >= 1.0:
            return "gas_cost_exceeds_expected_profit"
        if gas_to_profit_ratio > MAX_GAS_TO_PROFIT_RATIO:
            return "gas_profit_ratio_too_high"
        return ""

    def _skip_result(
        self,
        signal: ExecutionSignal,
        quoted_price: float,
        block_reason: str,
        *,
        mev_risk_score: float = 0.0,
        sandwich_risk: float = 0.0,
        execution_window_score: float = 0.0,
        expected_slippage_bps: float = 0.0,
        realized_slippage_bps: float = 0.0,
        price_impact_bps: float = 0.0,
        quote_deviation_bps: float = 0.0,
        metadata: dict[str, object] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            allow_trade=False,
            execution_mode="skip",
            private_tx_used=False,
            cow_used=False,
            quoted_price=quoted_price,
            order_price=0.0,
            size_usd=signal.size_usd,
            fee_bps=0.0,
            execution_type="maker",
            mev_risk_score=mev_risk_score,
            sandwich_risk=sandwich_risk,
            execution_window_score=execution_window_score,
            expected_slippage_bps=expected_slippage_bps,
            realized_slippage_bps=realized_slippage_bps,
            price_impact_bps=price_impact_bps,
            quote_deviation_bps=quote_deviation_bps,
            trade_blocked_reason=block_reason,
            metadata=metadata or {},
        )

    def _resolve_mode(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        quoted_price: float,
        mev_risk,
        policy,
        base_metadata: dict[str, object],
    ) -> tuple[str, ExecutionResult | None]:
        preferred_mode = policy.preferred_mode or mev_risk.recommended_execution_mode
        cow_eligible = self.cow_executor.supports(signal.pair, signal.size_usd, policy)

        if preferred_mode == "skip":
            return "skip", self._skip_result(
                signal,
                quoted_price,
                mev_risk.block_reason or "execution_mode_skip",
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                metadata=base_metadata,
            )

        if preferred_mode == "private_tx" and policy.allow_private_tx and self.private_tx_executor.is_available():
            return "private_tx", None

        if preferred_mode == "guarded_public" and policy.allow_guarded_public and mev_risk.public_swap_allowed:
            return "guarded_public", None

        if preferred_mode == "cow_intent" and cow_eligible:
            return "cow_intent", None

        if self.private_tx_executor.is_available() and policy.allow_private_tx:
            return "private_tx", None

        if policy.allow_guarded_public and mev_risk.public_swap_allowed:
            return "guarded_public", None

        if cow_eligible:
            return "cow_intent", None

        return "skip", self._skip_result(
            signal,
            quoted_price,
            "no_safe_execution_mode",
            mev_risk_score=mev_risk.mev_risk_score,
            sandwich_risk=mev_risk.sandwich_risk,
            execution_window_score=mev_risk.execution_window_score,
            metadata=base_metadata,
        )

    @staticmethod
    def _public_guard_block_reason(mev_risk, slippage, policy) -> str:
        if mev_risk.mev_risk_score >= policy.public_swap_max_risk:
            return "public_mev_risk_too_high"
        if slippage.expected_slippage_bps > policy.max_slippage_bps:
            return "public_slippage_too_high"
        if slippage.price_impact_bps > policy.max_price_impact_bps:
            return "public_price_impact_too_high"
        return ""

    def execute_trade(self, signal: ExecutionSignal, context: ExecutionContext) -> ExecutionResult:
        if signal.size_usd <= 0:
            return self._skip_result(signal, context.router_price, "zero_size")

        policy = self.policy_engine.resolve(signal.pair, signal.router)
        liquidity_usd = context.liquidity_usd if context.liquidity_usd > 0 else policy.liquidity_hint_usd
        if liquidity_usd <= 0:
            liquidity_usd = max(signal.size_usd * 20.0, 50_000.0)
        enriched_context = ExecutionContext(
            pair=context.pair,
            router=context.router,
            mid_price=context.mid_price,
            quote_bid=context.quote_bid,
            quote_ask=context.quote_ask,
            router_price=context.router_price,
            backup_price=context.backup_price,
            onchain_ref_price=context.onchain_ref_price,
            twap_price=context.twap_price,
            spread_bps=context.spread_bps,
            volatility=context.volatility,
            liquidity_usd=liquidity_usd,
            gas_price_gwei=context.gas_price_gwei,
            block_number=context.block_number,
            recent_blocks_since_trade=context.recent_blocks_since_trade,
            portfolio_usdc=context.portfolio_usdc,
            portfolio_eth=context.portfolio_eth,
            market_mode=context.market_mode,
            metadata=context.metadata,
        )

        quote_validation = self.quote_validator.validate(signal, enriched_context, policy)
        slippage = self.slippage_guard.evaluate(signal, enriched_context, policy)
        mev_risk = self.mev_risk_engine.assess(signal, enriched_context, policy, slippage, quote_validation)
        gas_guard_metrics = self._gas_guard_metrics(signal, enriched_context)
        base_metadata = {"policy_profile": policy.profile, **gas_guard_metrics}

        if not quote_validation.is_valid:
            return self._skip_result(
                signal,
                quote_validation.router_price,
                quote_validation.block_reason,
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata=base_metadata,
            )

        if not slippage.is_valid:
            return self._skip_result(
                signal,
                quote_validation.router_price,
                slippage.block_reason,
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata=base_metadata,
            )

        gas_guard_block_reason = self._gas_guard_block_reason(enriched_context, policy, gas_guard_metrics)
        base_metadata = {"policy_profile": policy.profile, **gas_guard_metrics}
        if gas_guard_block_reason:
            return self._skip_result(
                signal,
                quote_validation.router_price,
                gas_guard_block_reason,
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata=base_metadata,
            )

        if self._metadata_bool(signal.metadata or {}, "recovery_passive_only"):
            maker_price = min(context.quote_bid, quote_validation.router_price) if signal.side == "buy" else max(
                context.quote_ask,
                quote_validation.router_price,
            )
            return ExecutionResult(
                allow_trade=True,
                execution_mode="passive_limit",
                private_tx_used=False,
                cow_used=False,
                quoted_price=quote_validation.router_price,
                order_price=maker_price,
                size_usd=signal.size_usd,
                fee_bps=0.0,
                execution_type="maker",
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                realized_slippage_bps=0.0,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata={**base_metadata, "recovery_passive_only": True},
            )

        execution_mode, early_result = self._resolve_mode(
            signal,
            enriched_context,
            quote_validation.router_price,
            mev_risk,
            policy,
            base_metadata,
        )
        if early_result is not None:
            return early_result

        slices = self.order_slicer.slice_order(signal, policy, slippage)
        fee_bps = TAKER_FEE_BPS
        if execution_mode == "private_tx":
            executor_result = self.private_tx_executor.execute(signal, enriched_context, policy)
            fee_bps = executor_result.fee_bps
        elif execution_mode == "cow_intent":
            executor_result = self.cow_executor.execute(signal, enriched_context, policy)
            fee_bps = executor_result.fee_bps
        else:
            executor_result = ExecutionResult(
                allow_trade=True,
                execution_mode="guarded_public",
                private_tx_used=False,
                cow_used=False,
                quoted_price=quote_validation.router_price,
                order_price=quote_validation.router_price,
                size_usd=signal.size_usd,
                fee_bps=TAKER_FEE_BPS,
                execution_type="taker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
            )

        if not executor_result.allow_trade:
            return self._skip_result(
                signal,
                quote_validation.router_price,
                executor_result.trade_blocked_reason or "executor_blocked",
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata=base_metadata,
            )

        if execution_mode == "guarded_public":
            public_block_reason = self._public_guard_block_reason(mev_risk, slippage, policy)
            if public_block_reason:
                return self._skip_result(
                    signal,
                    quote_validation.router_price,
                    public_block_reason,
                    mev_risk_score=mev_risk.mev_risk_score,
                    sandwich_risk=mev_risk.sandwich_risk,
                    execution_window_score=mev_risk.execution_window_score,
                    expected_slippage_bps=slippage.expected_slippage_bps,
                    price_impact_bps=slippage.price_impact_bps,
                    quote_deviation_bps=quote_validation.quote_deviation_bps,
                    metadata=base_metadata,
                )

        simulation = self.trade_simulator.simulate(
            signal=signal,
            context=enriched_context,
            policy=policy,
            execution_mode=execution_mode,
            quote_validation=quote_validation,
            slippage=slippage,
            mev_risk=mev_risk,
            slices=slices,
            fee_bps=fee_bps,
        )
        if not simulation.success:
            return self._skip_result(
                signal,
                quote_validation.router_price,
                simulation.block_reason,
                mev_risk_score=mev_risk.mev_risk_score,
                sandwich_risk=mev_risk.sandwich_risk,
                execution_window_score=mev_risk.execution_window_score,
                expected_slippage_bps=slippage.expected_slippage_bps,
                realized_slippage_bps=simulation.realized_slippage_bps,
                price_impact_bps=slippage.price_impact_bps,
                quote_deviation_bps=quote_validation.quote_deviation_bps,
                metadata=base_metadata,
            )

        metadata = {
            **base_metadata,
            **executor_result.metadata,
            "slice_count": len(slices),
            "reference_price": quote_validation.reference_price,
            "twap_deviation_bps": quote_validation.twap_deviation_bps,
        }
        return ExecutionResult(
            allow_trade=True,
            execution_mode=execution_mode,
            private_tx_used=execution_mode == "private_tx",
            cow_used=execution_mode == "cow_intent",
            quoted_price=quote_validation.router_price,
            order_price=simulation.estimated_price,
            size_usd=signal.size_usd,
            fee_bps=fee_bps,
            execution_type=executor_result.execution_type,
            mev_risk_score=mev_risk.mev_risk_score,
            sandwich_risk=mev_risk.sandwich_risk,
            execution_window_score=mev_risk.execution_window_score,
            expected_slippage_bps=slippage.expected_slippage_bps,
            realized_slippage_bps=simulation.realized_slippage_bps,
            price_impact_bps=slippage.price_impact_bps,
            quote_deviation_bps=quote_validation.quote_deviation_bps,
            slices=slices,
            metadata=metadata,
        )
