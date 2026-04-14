from __future__ import annotations

from config import (
    EXECUTION_MAKER_SLIPPAGE_BPS,
    EXECUTION_MIN_EXPECTED_PROFIT_PCT,
    EXECUTION_SLIPPAGE_SIZE_FACTOR,
    EXECUTION_TAKER_SLIPPAGE_BPS,
    MAKER_FEE_BPS,
    PROFIT_LOCK_LEVEL_1_BPS,
    TRADE_SIZE_USD,
)
from types_bot import ExecutionDecision, Quote, ReentryState

TAKER_REASONS = {
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

TAKER_BUFFER_BPS = 10.0
FORCED_EXECUTION_REASONS = {
    "failsafe_sell",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_exit_sell",
    "force_trade_buy",
    "force_trade_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
    "reentry_pullback",
    "reentry_max_miss",
    "reentry_runaway",
    "reentry_timeout",
}


class ExecutionEngine:
    def __init__(self, maker_fee_bps: float, taker_fee_bps: float, base_trade_size_usd: float = TRADE_SIZE_USD):
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.base_trade_size_usd = max(base_trade_size_usd, 1e-9)

    def _choose_execution_type(self, trade_reason: str, mode: str) -> str:
        if trade_reason in TAKER_REASONS or mode == "OVERWEIGHT_EXIT":
            return "taker"
        return "maker"

    def _estimate_slippage_bps(self, size_usd: float, execution_type: str) -> float:
        if execution_type == "maker":
            return max(EXECUTION_MAKER_SLIPPAGE_BPS, 0.0)

        size_multiplier = max((size_usd / self.base_trade_size_usd) - 1.0, 0.0)
        estimated_bps = max(EXECUTION_TAKER_SLIPPAGE_BPS, 0.0) * (
            1.0 + (size_multiplier * EXECUTION_SLIPPAGE_SIZE_FACTOR)
        )
        return max(TAKER_BUFFER_BPS, estimated_bps)

    def _quoted_price(self, side: str, quote: Quote, execution_type: str) -> float:
        if side == "buy":
            return quote.ask if execution_type == "taker" else quote.bid
        return quote.bid if execution_type == "taker" else quote.ask

    def _required_profit_pct(self, size_usd: float) -> float:
        base_threshold = max(EXECUTION_MIN_EXPECTED_PROFIT_PCT, 0.0)
        if size_usd <= (self.base_trade_size_usd * 0.50):
            return base_threshold * 0.25
        if size_usd <= self.base_trade_size_usd:
            return base_threshold * 0.40
        if size_usd <= (self.base_trade_size_usd * 1.50):
            return base_threshold * 0.65
        return base_threshold

    def _order_price(self, side: str, quoted_price: float, slippage_bps: float, execution_type: str) -> float:
        if quoted_price <= 0:
            return quoted_price

        if execution_type != "taker":
            return quoted_price

        slippage_multiplier = slippage_bps / 10000.0
        if side == "buy":
            return quoted_price * (1.0 + slippage_multiplier)
        return quoted_price * (1.0 - slippage_multiplier)

    def _expected_profit_pct(
        self,
        side: str,
        order_price: float,
        fee_bps: float,
        slippage_bps: float,
        portfolio,
        reentry_state: ReentryState,
    ) -> float:
        if order_price <= 0:
            return 0.0

        transaction_cost_pct = (fee_bps + slippage_bps) / 100.0
        if side == "buy":
            if reentry_state.last_sell_price and reentry_state.last_sell_price > order_price:
                gross_profit_pct = ((reentry_state.last_sell_price / order_price) - 1.0) * 100.0
            else:
                gross_profit_pct = PROFIT_LOCK_LEVEL_1_BPS / 100.0
            # Include a conservative estimate for the exit maker fee.
            return max(gross_profit_pct - transaction_cost_pct - (MAKER_FEE_BPS / 100.0), 0.0)

        if portfolio.eth_cost_basis is None or portfolio.eth_cost_basis <= 0:
            return 0.0

        net_price = order_price * (1.0 - (fee_bps / 10000.0))
        return ((net_price / portfolio.eth_cost_basis) - 1.0) * 100.0

    def build_decision(
        self,
        side: str,
        quote: Quote,
        size_usd: float,
        mode: str,
        trade_reason: str,
        portfolio,
        reentry_state: ReentryState,
    ) -> ExecutionDecision:
        if size_usd <= 0:
            return ExecutionDecision(
                allow_trade=False,
                block_reason="zero_size",
                order_price=0.0,
                quoted_price=0.0,
                fee_bps=0.0,
                execution_type="maker",
                slippage_bps=0.0,
                expected_profit_pct=0.0,
                trade_reason=trade_reason,
            )

        execution_type = self._choose_execution_type(trade_reason, mode)
        quoted_price = self._quoted_price(side, quote, execution_type)
        slippage_bps = self._estimate_slippage_bps(size_usd, execution_type)
        order_price = self._order_price(side, quoted_price, slippage_bps, execution_type)
        fee_bps = self.taker_fee_bps if execution_type == "taker" else self.maker_fee_bps
        expected_profit_pct = self._expected_profit_pct(
            side=side,
            order_price=order_price,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            portfolio=portfolio,
            reentry_state=reentry_state,
        )
        required_profit_pct = self._required_profit_pct(size_usd)
        allow_small_probe_trade = size_usd <= (self.base_trade_size_usd * 0.50) and expected_profit_pct >= 0.0
        allow_trade = (
            trade_reason in FORCED_EXECUTION_REASONS
            or expected_profit_pct >= required_profit_pct
            or allow_small_probe_trade
        )
        return ExecutionDecision(
            allow_trade=allow_trade,
            block_reason="" if allow_trade else "expected_profit_below_threshold",
            order_price=order_price,
            quoted_price=quoted_price,
            fee_bps=fee_bps,
            execution_type=execution_type,
            slippage_bps=slippage_bps,
            expected_profit_pct=expected_profit_pct,
            trade_reason=trade_reason,
        )
