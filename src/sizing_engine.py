from __future__ import annotations

from dataclasses import dataclass

from config import (
    ACCOUNT_REFERENCE_MODE,
    ACCOUNT_SIZE_OVERRIDE,
    FORCE_TRADE_SIZE_PCT,
    MAX_INVENTORY_USD,
    MAX_POSITION_PCT,
    MAX_TRADE_SIZE_PCT,
    MAX_TRADE_SIZE_USD,
    MIN_BASE_RESERVE_PCT,
    MIN_NOTIONAL_USD,
    MIN_QUOTE_RESERVE_PCT,
    TARGET_BASE_PCT,
    TARGET_QUOTE_PCT,
    TRADE_SIZE_PCT,
    TRADE_SIZE_USD,
)


@dataclass(frozen=True)
class SizingSnapshot:
    equity_snapshot_usd: float
    reference_equity_usd: float
    computed_trade_size_usd: float
    computed_max_trade_size_usd: float
    computed_max_position_usd: float
    computed_force_trade_size_usd: float
    trade_size_usd: float
    max_trade_size_usd: float
    max_position_usd: float
    force_trade_size_usd: float
    target_base_pct: float
    target_quote_pct: float
    target_base_usd: float
    target_quote_usd: float
    min_notional_usd: float
    min_base_reserve_pct: float
    min_quote_reserve_pct: float
    base_reserve_usd: float
    quote_reserve_usd: float
    base_reserve_eth: float
    available_quote_to_trade_usd: float
    size_clamped: bool
    clamp_reason: str
    force_size_clamped: bool
    force_clamp_reason: str
    insufficient_equity_for_min_trade: bool


@dataclass(frozen=True)
class SizeClampResult:
    effective_size_usd: float
    size_clamped: bool
    clamp_reason: str
    insufficient_equity_for_min_trade: bool


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _normalized_target_split(target_base_pct: float, target_quote_pct: float) -> tuple[float, float]:
    base_pct = max(target_base_pct, 0.0)
    quote_pct = max(target_quote_pct, 0.0)
    total = base_pct + quote_pct
    if total <= 0:
        return 0.50, 0.50
    return base_pct / total, quote_pct / total


def resolve_reference_equity_usd(current_equity_usd: float) -> float:
    equity_snapshot = max(current_equity_usd, 0.0)
    mode = ACCOUNT_REFERENCE_MODE.strip().lower()
    if ACCOUNT_SIZE_OVERRIDE > 0 and mode in {"dynamic", "override", "fixed"}:
        return ACCOUNT_SIZE_OVERRIDE
    return equity_snapshot


def compute_max_position_usd(reference_equity_usd: float) -> float:
    if MAX_POSITION_PCT > 0:
        return max(reference_equity_usd * MAX_POSITION_PCT, 0.0)
    return max(MAX_INVENTORY_USD, 0.0)


def _compute_max_trade_size_usd(reference_equity_usd: float, max_position_usd: float) -> float:
    if MAX_TRADE_SIZE_PCT > 0:
        max_trade_size_usd = max(reference_equity_usd * MAX_TRADE_SIZE_PCT, 0.0)
    else:
        max_trade_size_usd = max(MAX_TRADE_SIZE_USD, 0.0)
    if max_position_usd > 0:
        max_trade_size_usd = min(max_trade_size_usd, max_position_usd)
    return max(max_trade_size_usd, 0.0)


def _compute_trade_size_usd(reference_equity_usd: float) -> float:
    if TRADE_SIZE_PCT > 0:
        return max(reference_equity_usd * TRADE_SIZE_PCT, 0.0)
    return max(TRADE_SIZE_USD, 0.0)


def _compute_force_trade_size_usd(reference_equity_usd: float, base_trade_size_usd: float) -> float:
    if FORCE_TRADE_SIZE_PCT > 0:
        return max(reference_equity_usd * FORCE_TRADE_SIZE_PCT, 0.0)
    return max(base_trade_size_usd, 0.0)


def clamp_size_to_limits(
    computed_size_usd: float,
    *,
    max_trade_size_usd: float,
    min_notional_usd: float,
    clamp_reason_high: str = "max_trade_size_pct",
) -> SizeClampResult:
    requested_size_usd = max(computed_size_usd, 0.0)
    if requested_size_usd <= 0:
        return SizeClampResult(
            effective_size_usd=0.0,
            size_clamped=False,
            clamp_reason="zero_computed_size",
            insufficient_equity_for_min_trade=True,
        )

    if max_trade_size_usd > 0 and max_trade_size_usd < min_notional_usd:
        return SizeClampResult(
            effective_size_usd=0.0,
            size_clamped=False,
            clamp_reason="insufficient_equity_for_min_trade",
            insufficient_equity_for_min_trade=True,
        )

    if requested_size_usd < min_notional_usd:
        return SizeClampResult(
            effective_size_usd=0.0,
            size_clamped=False,
            clamp_reason="below_min_notional",
            insufficient_equity_for_min_trade=True,
        )

    if max_trade_size_usd > 0 and requested_size_usd > max_trade_size_usd:
        return SizeClampResult(
            effective_size_usd=max_trade_size_usd,
            size_clamped=True,
            clamp_reason=clamp_reason_high,
            insufficient_equity_for_min_trade=False,
        )

    return SizeClampResult(
        effective_size_usd=requested_size_usd,
        size_clamped=False,
        clamp_reason="",
        insufficient_equity_for_min_trade=False,
    )


def build_sizing_snapshot(
    *,
    current_equity_usd: float,
    mid_price: float,
    portfolio_usdc: float = 0.0,
    portfolio_eth: float = 0.0,
) -> SizingSnapshot:
    equity_snapshot_usd = max(current_equity_usd, 0.0)
    reference_equity_usd = resolve_reference_equity_usd(equity_snapshot_usd)
    target_base_pct, target_quote_pct = _normalized_target_split(TARGET_BASE_PCT, TARGET_QUOTE_PCT)
    min_notional_usd = max(MIN_NOTIONAL_USD, 0.0)

    max_position_usd = compute_max_position_usd(reference_equity_usd)
    max_trade_size_usd = _compute_max_trade_size_usd(reference_equity_usd, max_position_usd)
    computed_trade_size_usd = _compute_trade_size_usd(reference_equity_usd)
    computed_force_trade_size_usd = _compute_force_trade_size_usd(reference_equity_usd, computed_trade_size_usd)

    trade_clamp = clamp_size_to_limits(
        computed_trade_size_usd,
        max_trade_size_usd=max_trade_size_usd,
        min_notional_usd=min_notional_usd,
    )
    force_trade_clamp = clamp_size_to_limits(
        computed_force_trade_size_usd,
        max_trade_size_usd=max_trade_size_usd,
        min_notional_usd=min_notional_usd,
    )

    base_reserve_pct = clamp(MIN_BASE_RESERVE_PCT, 0.0, 0.95)
    quote_reserve_pct = clamp(MIN_QUOTE_RESERVE_PCT, 0.0, 0.95)
    base_reserve_usd = reference_equity_usd * base_reserve_pct
    quote_reserve_usd = reference_equity_usd * quote_reserve_pct
    base_reserve_eth = (base_reserve_usd / mid_price) if mid_price > 0 else 0.0

    current_base_usd = max(portfolio_eth, 0.0) * max(mid_price, 0.0)
    available_quote_to_trade_usd = max(portfolio_usdc - quote_reserve_usd, 0.0)
    if current_base_usd <= base_reserve_usd:
        base_reserve_eth = max(portfolio_eth, 0.0)

    return SizingSnapshot(
        equity_snapshot_usd=equity_snapshot_usd,
        reference_equity_usd=reference_equity_usd,
        computed_trade_size_usd=computed_trade_size_usd,
        computed_max_trade_size_usd=max_trade_size_usd,
        computed_max_position_usd=max_position_usd,
        computed_force_trade_size_usd=computed_force_trade_size_usd,
        trade_size_usd=trade_clamp.effective_size_usd,
        max_trade_size_usd=max_trade_size_usd,
        max_position_usd=max_position_usd,
        force_trade_size_usd=force_trade_clamp.effective_size_usd,
        target_base_pct=target_base_pct,
        target_quote_pct=target_quote_pct,
        target_base_usd=reference_equity_usd * target_base_pct,
        target_quote_usd=reference_equity_usd * target_quote_pct,
        min_notional_usd=min_notional_usd,
        min_base_reserve_pct=base_reserve_pct,
        min_quote_reserve_pct=quote_reserve_pct,
        base_reserve_usd=base_reserve_usd,
        quote_reserve_usd=quote_reserve_usd,
        base_reserve_eth=base_reserve_eth,
        available_quote_to_trade_usd=available_quote_to_trade_usd,
        size_clamped=trade_clamp.size_clamped,
        clamp_reason=trade_clamp.clamp_reason,
        force_size_clamped=force_trade_clamp.size_clamped,
        force_clamp_reason=force_trade_clamp.clamp_reason,
        insufficient_equity_for_min_trade=(
            trade_clamp.insufficient_equity_for_min_trade
            and force_trade_clamp.insufficient_equity_for_min_trade
        ),
    )


def trade_direction_to_target(snapshot: SizingSnapshot, inventory_usd: float) -> str:
    tolerance_usd = max(snapshot.min_notional_usd, snapshot.reference_equity_usd * 0.01, 1.0)
    if inventory_usd > (snapshot.target_base_usd + tolerance_usd):
        return "sell"
    if inventory_usd < max(snapshot.target_base_usd - tolerance_usd, 0.0):
        return "buy"
    return "neutral"


def as_log_fields(snapshot: SizingSnapshot, *, use_force: bool = False) -> dict[str, object]:
    return {
        "equity_snapshot": round(snapshot.equity_snapshot_usd, 6),
        "reference_equity_usd": round(snapshot.reference_equity_usd, 6),
        "computed_trade_size_usd": round(snapshot.computed_trade_size_usd, 6),
        "computed_max_trade_size_usd": round(snapshot.computed_max_trade_size_usd, 6),
        "computed_max_position_usd": round(snapshot.computed_max_position_usd, 6),
        "computed_force_trade_size_usd": round(snapshot.computed_force_trade_size_usd, 6),
        "computed_target_base_usd": round(snapshot.target_base_usd, 6),
        "computed_target_quote_usd": round(snapshot.target_quote_usd, 6),
        "target_base_pct": round(snapshot.target_base_pct, 6),
        "target_quote_pct": round(snapshot.target_quote_pct, 6),
        "min_notional_usd": round(snapshot.min_notional_usd, 6),
        "size_clamped": snapshot.force_size_clamped if use_force else snapshot.size_clamped,
        "clamp_reason": snapshot.force_clamp_reason if use_force else snapshot.clamp_reason,
    }
