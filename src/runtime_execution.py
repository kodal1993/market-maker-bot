from __future__ import annotations

from execution_analytics import analytics_from_result, as_filter_values
from types_bot import ExecutionContext, ExecutionSignal, Quote


def execution_pair() -> str:
    return "WETH/USDC"


def estimate_execution_gas_gwei(volatility: float, spread_bps: float) -> float:
    return max(8.0, 10.0 + (volatility * 8000.0) + (spread_bps * 0.25))


def estimate_execution_liquidity_usd(
    effective_max_inventory_usd: float,
    size_usd: float,
    mid: float,
) -> float:
    liquidity_anchor = max(size_usd, max(effective_max_inventory_usd * 0.05, 1.0))
    return max(
        effective_max_inventory_usd * 250.0,
        liquidity_anchor * 80.0,
        max(mid, 1.0) * 400.0,
        75_000.0,
    )


def build_execution_context(
    runtime,
    cycle_index: int,
    mode: str,
    mid: float,
    spread_bps: float,
    intelligence,
    quote: Quote,
    router_price: float,
    source: str,
    effective_max_inventory_usd: float,
    size_usd: float,
) -> ExecutionContext:
    recent_blocks_since_trade = 0
    if runtime.last_trade_cycle_any is not None:
        recent_blocks_since_trade = max(cycle_index - runtime.last_trade_cycle_any, 0)

    return ExecutionContext(
        pair=execution_pair(),
        router="uniswap_v3",
        mid_price=mid,
        quote_bid=quote.bid,
        quote_ask=quote.ask,
        router_price=router_price,
        backup_price=mid,
        onchain_ref_price=mid,
        twap_price=((intelligence.short_ma + intelligence.long_ma) / 2.0)
        if intelligence.short_ma > 0 and intelligence.long_ma > 0
        else mid,
        spread_bps=spread_bps,
        volatility=intelligence.volatility,
        liquidity_usd=estimate_execution_liquidity_usd(effective_max_inventory_usd, size_usd, mid),
        gas_price_gwei=estimate_execution_gas_gwei(intelligence.volatility, spread_bps),
        block_number=cycle_index,
        recent_blocks_since_trade=recent_blocks_since_trade,
        portfolio_usdc=runtime.portfolio.usdc,
        portfolio_eth=runtime.portfolio.eth,
        market_mode=mode,
        metadata={
            "source": source,
            "adverse_selection_bps": getattr(runtime, "current_adverse_selection_bps", 0.0),
        },
    )


def route_execution_signal(runtime, signal: ExecutionSignal, context: ExecutionContext) -> tuple[object | None, dict[str, object]]:
    execution_result = runtime.execution_router.execute_trade(signal, context)
    capture_execution_result(runtime, context, execution_result)
    runtime.last_execution_analytics = analytics_from_result(execution_result)
    filter_values = as_filter_values(runtime.last_execution_analytics)
    metadata = dict(getattr(execution_result, "metadata", {}) or {})
    for key in (
        "gas_price_gwei",
        "estimated_gas_cost_usd",
        "expected_profit_pct",
        "expected_profit_usd",
        "gas_to_profit_ratio",
    ):
        value = metadata.get(key)
        if value is None or value == "":
            continue
        filter_values[key] = round(float(value), 6) if isinstance(value, (int, float)) else value
    return execution_result, filter_values


def apply_execution_result_to_order(order, execution_result) -> None:
    order.price = execution_result.order_price
    order.size_usd = execution_result.size_usd
    order.size_base = order.size_usd / order.price if order.price > 0 else 0.0
    order.fee_bps = execution_result.fee_bps
    order.execution_type = execution_result.execution_type
    order.slippage_bps = execution_result.realized_slippage_bps


def reset_execution_observation(runtime) -> None:
    runtime.last_execution_pair = ""
    runtime.last_execution_gas_gwei = 0.0
    runtime.last_execution_tx_hash = ""
    runtime.last_execution_metadata = {}


def capture_execution_result(runtime, context: ExecutionContext, execution_result) -> None:
    runtime.last_execution_pair = context.pair
    runtime.last_execution_gas_gwei = context.gas_price_gwei
    metadata = dict(getattr(execution_result, "metadata", {}) or {})
    runtime.last_execution_metadata = metadata
    runtime.last_execution_tx_hash = str(metadata.get("tx_hash", "") or "")
