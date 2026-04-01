from __future__ import annotations

import json

from config import KILL_SWITCH_USD, TRADE_FILTER_DEBUG_MODE
from logger import log
from types_bot import ExecutionAnalyticsRecord, ProfitLockState, ReentryState


def _deserialize_filter_values(filter_values: str) -> dict[str, object]:
    if not filter_values:
        return {}
    try:
        payload = json.loads(filter_values)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rounded_or_blank(value: object) -> object:
    if value in {None, ""}:
        return ""
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return ""


def extract_signal_log_fields(filter_values: str) -> dict[str, object]:
    payload = _deserialize_filter_values(filter_values)
    return {
        "detected_regime": str(payload.get("detected_regime") or payload.get("market_regime") or ""),
        "zone": str(payload.get("zone") or ""),
        "entry_threshold_bps": _rounded_or_blank(payload.get("entry_threshold_bps")),
        "min_edge_bps": _rounded_or_blank(payload.get("min_edge_bps")),
        "inventory_drift_pct": _rounded_or_blank(payload.get("inventory_drift_pct")),
        "trade_blocked_reason": str(
            payload.get("trade_blocked_reason")
            or payload.get("gate_blocked_reason")
            or payload.get("block_reason")
            or ""
        ),
    }


def trade_log_headers() -> list[str]:
    return [
        "cycle",
        "state",
        "mode",
        "decision_source",
        "final_action",
        "overridden_signals",
        "allow_trade",
        "block_reason",
        "filter_values",
        "detected_regime",
        "zone",
        "entry_threshold_bps",
        "min_edge_bps",
        "inventory_drift_pct",
        "side",
        "execution_type",
        "execution_mode",
        "private_tx_used",
        "cow_used",
        "mev_risk_score",
        "sandwich_risk",
        "expected_slippage_bps",
        "realized_slippage_bps",
        "price_impact_bps",
        "quote_deviation_bps",
        "trade_blocked_reason",
        "execution_price",
        "price",
        "entry_price",
        "exit_price",
        "max_profit_during_trade",
        "size_usd",
        "fee_usd",
        "slippage_bps",
        "trade_reason",
        "active_regime",
        "trend_direction",
        "volatility_bucket",
        "inventory_state",
        "trigger_reason",
        "exit_reason",
        "entry_edge_bps",
        "entry_edge_usd",
        "hold_minutes",
        "usdc_after",
        "eth_after",
    ]


def equity_log_headers() -> list[str]:
    return [
        "cycle",
        "state",
        "mode",
        "decision_source",
        "final_action",
        "overridden_signals",
        "allow_trade",
        "block_reason",
        "filter_values",
        "detected_regime",
        "zone",
        "entry_threshold_bps",
        "min_edge_bps",
        "inventory_drift_pct",
        "feed_state",
        "regime",
        "volatility_state",
        "price",
        "source",
        "short_ma",
        "long_ma",
        "volatility",
        "spread",
        "signal_score",
        "feed_score",
        "risk_score",
        "news_score",
        "macro_score",
        "onchain_score",
        "adaptive_score",
        "confidence",
        "buy_enabled",
        "sell_enabled",
        "max_inventory_usd",
        "target_inventory_pct",
        "trade_size_multiplier",
        "spread_multiplier",
        "trade_size_usd",
        "inventory_usd",
        "inventory_ratio",
        "equity",
        "pnl",
        "trades",
        "execution_price",
        "reentry_state",
        "state_context",
        "time_in_state_sec",
        "last_transition",
        "last_sell_price",
        "reentry_levels",
        "buy_zones",
        "executed_buy_levels",
        "reentry_active",
        "reentry_timeout",
        "cooldown_remaining",
        "profit_lock_state",
        "current_profit_pct",
        "buy_debug_reason",
        "sell_debug_reason",
        "last_execution_type",
        "execution_mode",
        "private_tx_used",
        "cow_used",
        "mev_risk_score",
        "sandwich_risk",
        "execution_window_score",
        "expected_slippage_bps",
        "realized_slippage_bps",
        "price_impact_bps",
        "quote_deviation_bps",
        "trade_blocked_reason",
        "last_slippage_bps",
        "last_trade_reason",
    ]


def serialize_filter_values(filter_values: dict[str, object] | None) -> str:
    if not filter_values:
        return ""
    return json.dumps(filter_values, separators=(",", ":"), sort_keys=True)


def merge_filter_values(base: dict[str, object] | None, **updates: object) -> dict[str, object]:
    merged = dict(base or {})
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def serialize_buy_zones(state: ReentryState) -> str:
    if state.last_sell_price is None or not any(state.buy_zones):
        return ""
    return json.dumps(
        {
            "zone_1": round(state.buy_zones[0], 6),
            "zone_2": round(state.buy_zones[1], 6),
            "zone_3": round(state.buy_zones[2], 6),
        },
        separators=(",", ":"),
    )


def serialize_profit_lock_state(state: ProfitLockState) -> str:
    if state.anchor_price is None:
        return ""
    return json.dumps(
        {
            "anchor": round(state.anchor_price, 6),
            "high": round(state.highest_price or 0.0, 6),
            "level_one_armed": state.level_one_armed,
            "level_two_armed": state.level_two_armed,
            "level_one_executed": state.level_one_executed,
            "level_two_executed": state.level_two_executed,
        },
        separators=(",", ":"),
    )


def log_cycle(
    runtime,
    cycle_index: int,
    mode: str,
    intelligence,
    mid: float,
    source: str,
    equity_usd: float,
    pnl_usd: float,
    spread: float,
    inventory_usd: float,
    *,
    time_in_state_sec: float,
    last_transition: str,
) -> None:
    profit_text = "n/a" if runtime.last_profit_pct is None else f"{runtime.last_profit_pct:.3f}%"
    regime = getattr(runtime, "current_regime_assessment", None)
    edge = getattr(runtime, "current_edge_assessment", None)
    gate = getattr(runtime, "current_signal_gate_decision", None)
    regime_text = getattr(regime, "market_regime", "-")
    execution_regime = getattr(regime, "execution_regime", getattr(runtime, "current_active_regime", "-"))
    trend_direction = getattr(regime, "trend_direction", getattr(runtime, "current_trend_direction", "neutral"))
    range_location = getattr(regime, "range_location", getattr(runtime, "current_range_location", "middle"))
    regime_confidence = getattr(regime, "regime_confidence", 0.0)
    edge_score = getattr(edge, "edge_score", 0.0)
    expected_edge_usd = getattr(edge, "expected_edge_usd", 0.0)
    gate_text = "allow" if getattr(gate, "allow_trade", False) else "reject"
    gate_reason = getattr(gate, "blocked_reason", "") or runtime.last_decision_block_reason or "-"
    detected_regime = getattr(runtime, "current_detected_regime", regime_text)
    zone = getattr(runtime, "current_zone", "mid")
    entry_threshold_bps = getattr(runtime, "current_entry_threshold_bps", 0.0)
    min_edge_bps = getattr(runtime, "current_min_edge_bps", 0.0)
    inventory_drift_pct = getattr(runtime, "current_inventory_drift_pct", 0.0)
    trade_blocked_reason = (
        getattr(runtime.last_execution_analytics, "trade_blocked_reason", "")
        or getattr(gate, "blocked_reason", "")
        or runtime.last_decision_block_reason
        or "-"
    )
    upper_tf_bias = getattr(runtime, "current_trend_bias", "range")
    current_mode = getattr(intelligence, "current_mode", mode)
    confirmation_text = (
        f"{getattr(runtime, 'current_confirmation_momentum_bps', 0.0):.1f}bps/"
        f"{'slow' if getattr(runtime, 'current_confirmation_slowing', False) else 'fast'}"
    )
    log(
        f"{cycle_index} | state {runtime.state_context.current_state.value} | time_in_state {time_in_state_sec:.0f}s | "
        f"last_transition {last_transition or '-'} | mode {mode} | current_mode {current_mode} | "
        f"trend_strength {getattr(intelligence, 'trend_strength', 0.0):.5f} | regime {intelligence.regime} | "
        f"vol_state {intelligence.volatility_state} | "
        f"price {mid:.2f} | src {source} | eq {equity_usd:.2f} | pnl {pnl_usd:.2f} | "
        f"spread {spread:.1f} | vol {intelligence.volatility:.5f} | inv {inventory_usd:.2f} | "
        f"sig {intelligence.signal_score:.2f} | feed {intelligence.feed_state} | risk {intelligence.risk_score:.2f} | "
        f"raw_signal {getattr(runtime, 'last_raw_signal', '-') or '-'} | detected_regime {detected_regime} | "
        f"active_regime {execution_regime} | trend_dir {trend_direction} | zone {zone} | range_loc {range_location} | "
        f"activity {getattr(runtime, 'current_activity_state', 'normal')} | regime_conf {regime_confidence:.1f} | "
        f"entry_threshold_bps {entry_threshold_bps:.2f} | min_edge_bps {min_edge_bps:.2f} | "
        f"inventory_drift_pct {inventory_drift_pct:+.2f} | "
        f"upper_tf {upper_tf_bias} | confirm {confirmation_text} | "
        f"edge {edge_score:.1f} | exp_edge {expected_edge_usd:.4f} | "
        f"gate {gate_text} | gate_reason {gate_reason} | trade_blocked_reason {trade_blocked_reason} | "
        f"loss_streak {getattr(runtime, 'loss_streak', 0)} | "
        f"drawdown {getattr(runtime, 'current_drawdown_pct', 0.0):.2%} | dd_stage {getattr(runtime, 'drawdown_guard_stage', 'normal')} | "
        f"inv_limit {getattr(runtime, 'current_inventory_limit_state', 'normal')} | "
        f"buy {intelligence.buy_enabled} | sell {intelligence.sell_enabled} | "
        f"profit_pct {profit_text} | buy_debug {runtime.last_buy_debug_reason or '-'} | "
        f"sell_debug {runtime.last_sell_debug_reason or '-'}"
    )
    if TRADE_FILTER_DEBUG_MODE:
        filter_values = runtime.last_filter_values or "{}"
        log(
            f"{cycle_index} | allow_trade {runtime.last_allow_trade} | "
            f"block_reason {runtime.last_decision_block_reason or '-'} | filters {filter_values}"
        )


def log_execution_decision(runtime, cycle_index: int) -> None:
    analytics = runtime.last_execution_analytics
    if not analytics.execution_mode:
        return
    metadata = getattr(runtime, "last_execution_metadata", {}) or {}
    gas_price_gwei = metadata.get("gas_price_gwei", getattr(runtime, "last_execution_gas_gwei", 0.0))
    estimated_gas_cost_usd = metadata.get("estimated_gas_cost_usd", 0.0)
    expected_profit_usd = metadata.get("expected_profit_usd", 0.0)
    gas_to_profit_ratio = metadata.get("gas_to_profit_ratio")
    gas_ratio_text = "-" if gas_to_profit_ratio in {None, ""} else f"{float(gas_to_profit_ratio):.2f}"

    log(
        f"{cycle_index} | execution_mode {analytics.execution_mode} | "
        f"trade_size {analytics.trade_size_usd:.2f} | "
        f"gas {float(gas_price_gwei):.1f}gwei | "
        f"gas_cost {float(estimated_gas_cost_usd):.4f} | "
        f"exp_profit {float(expected_profit_usd):.4f} | "
        f"gas_profit_ratio {gas_ratio_text} | "
        f"mev_risk {analytics.mev_risk_score:.1f} | "
        f"slippage {analytics.expected_slippage_bps:.1f}bps | "
        f"blocked_reason {analytics.trade_blocked_reason or '-'}"
    )


def log_trade_intent(runtime, cycle_index: int) -> None:
    if runtime.last_final_action in {"BUY", "SELL"} and runtime.last_allow_trade:
        log(
            f"{cycle_index} | traded_why {runtime.last_final_action.lower()} | "
            f"reason {runtime.last_decision_reason or '-'} | "
            f"source {runtime.last_decision_source or '-'} | "
            f"size {runtime.last_decision_size_usd:.2f}"
        )
        return

    log(
        f"{cycle_index} | no_trade_why action {runtime.last_final_action or 'NONE'} | "
        f"reason {runtime.last_decision_block_reason or runtime.last_decision_reason or 'no_signal'} | "
        f"source {runtime.last_decision_source or '-'}"
    )


def kill_switch_allows_continue(pnl_usd: float, log_progress: bool) -> bool:
    if pnl_usd < KILL_SWITCH_USD:
        if log_progress:
            log(f"KILL SWITCH ACTIVE | pnl={pnl_usd:.2f}")
        return False
    return True


def append_equity_row(
    equity_logger,
    cycle_index: int,
    state: str,
    mode: str,
    decision_source: str,
    final_action: str,
    overridden_signals: str,
    allow_trade: bool,
    block_reason: str,
    filter_values: str,
    feed_state: str,
    regime: str,
    volatility_state: str,
    mid: float,
    source: str,
    short_ma: float,
    long_ma: float,
    volatility: float,
    spread: float,
    signal_score: float,
    feed_score: float,
    risk_score: float,
    news_score: float,
    macro_score: float,
    onchain_score: float,
    adaptive_score: float,
    confidence: float,
    buy_enabled: bool,
    sell_enabled: bool,
    max_inventory_usd: float,
    target_inventory_pct: float,
    trade_size_multiplier: float,
    spread_multiplier: float,
    trade_size_usd: float,
    inventory_usd: float,
    inventory_ratio: float,
    equity_usd: float,
    pnl_usd: float,
    trade_count: int,
    execution_price: float,
    reentry_state: str,
    state_context: str,
    time_in_state_sec: float,
    last_transition: str,
    last_sell_price: float | None,
    reentry_levels: str,
    buy_zones: str,
    executed_buy_levels: str,
    reentry_active: bool,
    reentry_timeout: int,
    cooldown_remaining: int,
    profit_lock_state: str,
    current_profit_pct: float | None,
    buy_debug_reason: str,
    sell_debug_reason: str,
    last_execution_type: str,
    execution_analytics: ExecutionAnalyticsRecord,
    last_slippage_bps: float,
    last_trade_reason: str,
) -> None:
    if equity_logger is None:
        return

    signal_fields = extract_signal_log_fields(filter_values)
    trade_blocked_reason = signal_fields["trade_blocked_reason"] or execution_analytics.trade_blocked_reason
    equity_logger.append(
        [
            cycle_index,
            state,
            mode,
            decision_source,
            final_action,
            overridden_signals,
            int(allow_trade),
            block_reason,
            filter_values,
            signal_fields["detected_regime"],
            signal_fields["zone"],
            signal_fields["entry_threshold_bps"],
            signal_fields["min_edge_bps"],
            signal_fields["inventory_drift_pct"],
            feed_state,
            regime,
            volatility_state,
            round(mid, 6),
            source,
            round(short_ma, 6),
            round(long_ma, 6),
            round(volatility, 8),
            round(spread, 4),
            round(signal_score, 4),
            round(feed_score, 4),
            round(risk_score, 4),
            round(news_score, 4),
            round(macro_score, 4),
            round(onchain_score, 4),
            round(adaptive_score, 4),
            round(confidence, 4),
            int(buy_enabled),
            int(sell_enabled),
            round(max_inventory_usd, 6),
            round(target_inventory_pct, 6),
            round(trade_size_multiplier, 6),
            round(spread_multiplier, 6),
            round(trade_size_usd, 6),
            round(inventory_usd, 6),
            round(inventory_ratio, 6),
            round(equity_usd, 6),
            round(pnl_usd, 6),
            trade_count,
            round(execution_price, 6),
            reentry_state,
            state_context,
            round(time_in_state_sec, 6),
            last_transition,
            "" if last_sell_price is None else round(last_sell_price, 6),
            reentry_levels,
            buy_zones,
            executed_buy_levels,
            int(reentry_active),
            reentry_timeout,
            cooldown_remaining,
            profit_lock_state,
            "" if current_profit_pct is None else round(current_profit_pct, 6),
            buy_debug_reason,
            sell_debug_reason,
            last_execution_type,
            execution_analytics.execution_mode,
            int(execution_analytics.private_tx_used),
            int(execution_analytics.cow_used),
            round(execution_analytics.mev_risk_score, 6),
            round(execution_analytics.sandwich_risk, 6),
            round(execution_analytics.execution_window_score, 6),
            round(execution_analytics.expected_slippage_bps, 6),
            round(execution_analytics.realized_slippage_bps, 6),
            round(execution_analytics.price_impact_bps, 6),
            round(execution_analytics.quote_deviation_bps, 6),
            trade_blocked_reason,
            round(last_slippage_bps, 6),
            last_trade_reason,
        ]
    )


def append_trade_row(
    trade_logger,
    cycle_index: int,
    state: str,
    mode: str,
    decision_source: str,
    final_action: str,
    overridden_signals: str,
    allow_trade: bool,
    block_reason: str,
    filter_values: str,
    fill,
    portfolio,
    execution_analytics: ExecutionAnalyticsRecord,
    entry_price: float | None,
    exit_price: float | None,
    max_profit_during_trade: float | None,
    active_regime: str,
    trend_direction: str,
    volatility_bucket: str,
    inventory_state: str,
    trigger_reason: str,
    exit_reason: str,
    entry_edge_bps: float,
    entry_edge_usd: float,
    hold_minutes: float,
    *,
    trade_reason_category,
) -> None:
    if trade_logger is None or not fill.filled:
        return

    signal_fields = extract_signal_log_fields(filter_values)
    trade_blocked_reason = signal_fields["trade_blocked_reason"] or execution_analytics.trade_blocked_reason
    trade_logger.append(
        [
            cycle_index,
            state,
            mode,
            decision_source,
            final_action,
            overridden_signals,
            int(allow_trade),
            block_reason,
            filter_values,
            signal_fields["detected_regime"],
            signal_fields["zone"],
            signal_fields["entry_threshold_bps"],
            signal_fields["min_edge_bps"],
            signal_fields["inventory_drift_pct"],
            fill.side,
            fill.execution_type,
            execution_analytics.execution_mode,
            int(execution_analytics.private_tx_used),
            int(execution_analytics.cow_used),
            round(execution_analytics.mev_risk_score, 6),
            round(execution_analytics.sandwich_risk, 6),
            round(execution_analytics.expected_slippage_bps, 6),
            round(execution_analytics.realized_slippage_bps, 6),
            round(execution_analytics.price_impact_bps, 6),
            round(execution_analytics.quote_deviation_bps, 6),
            trade_blocked_reason,
            round(fill.price, 6),
            round(fill.price, 6),
            "" if entry_price is None else round(entry_price, 6),
            "" if exit_price is None else round(exit_price, 6),
            "" if max_profit_during_trade is None else round(max_profit_during_trade, 6),
            round(fill.size_usd, 6),
            round(fill.fee_usd, 6),
            round(fill.slippage_bps, 6),
            trade_reason_category(mode, fill.trade_reason),
            active_regime,
            trend_direction,
            volatility_bucket,
            inventory_state,
            trigger_reason,
            exit_reason,
            round(entry_edge_bps, 6),
            round(entry_edge_usd, 6),
            round(hold_minutes, 6),
            round(portfolio.usdc, 6),
            round(portfolio.eth, 8),
        ]
    )
