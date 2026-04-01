from __future__ import annotations

from types_bot import ExecutionAnalyticsRecord, ExecutionResult


def blank_execution_analytics() -> ExecutionAnalyticsRecord:
    return ExecutionAnalyticsRecord()


def analytics_from_result(result: ExecutionResult | None) -> ExecutionAnalyticsRecord:
    if result is None:
        return blank_execution_analytics()

    return ExecutionAnalyticsRecord(
        execution_mode=result.execution_mode,
        private_tx_used=result.private_tx_used,
        cow_used=result.cow_used,
        trade_size_usd=result.size_usd,
        mev_risk_score=result.mev_risk_score,
        sandwich_risk=result.sandwich_risk,
        execution_window_score=result.execution_window_score,
        expected_slippage_bps=result.expected_slippage_bps,
        realized_slippage_bps=result.realized_slippage_bps,
        price_impact_bps=result.price_impact_bps,
        quote_deviation_bps=result.quote_deviation_bps,
        trade_blocked_reason=result.trade_blocked_reason,
        slice_count=len(result.slices),
        policy_profile=str(result.metadata.get("policy_profile", "")),
    )


def update_block_reason(record: ExecutionAnalyticsRecord, block_reason: str) -> ExecutionAnalyticsRecord:
    if block_reason:
        record.trade_blocked_reason = block_reason
    return record


def as_filter_values(record: ExecutionAnalyticsRecord) -> dict[str, object]:
    return {
        "execution_mode": record.execution_mode,
        "private_tx_used": record.private_tx_used,
        "cow_used": record.cow_used,
        "trade_size_usd": round(record.trade_size_usd, 6),
        "mev_risk_score": round(record.mev_risk_score, 6),
        "sandwich_risk": round(record.sandwich_risk, 6),
        "execution_window_score": round(record.execution_window_score, 6),
        "expected_slippage_bps": round(record.expected_slippage_bps, 6),
        "realized_slippage_bps": round(record.realized_slippage_bps, 6),
        "price_impact_bps": round(record.price_impact_bps, 6),
        "quote_deviation_bps": round(record.quote_deviation_bps, 6),
        "trade_blocked_reason": record.trade_blocked_reason,
        "slice_count": record.slice_count,
        "policy_profile": record.policy_profile,
    }
