from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PASS_PROFIT_FACTOR = 1.3
FAIL_PROFIT_FACTOR = 1.1
PASS_MAX_DRAWDOWN_PCT = 20.0
MINIMUM_VERDICT_TRADE_COUNT = 200


def _json_compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@dataclass
class TradeRecord:
    cycle_index: int
    side: str
    price: float
    size_usd: float
    fee_usd: float
    realized_pnl: float
    execution_type: str = ""
    slippage_bps: float = 0.0
    trade_reason: str = ""
    usdc_after: float = 0.0
    eth_after: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    max_profit_during_trade: float = 0.0
    active_regime: str = ""
    trend_direction: str = ""
    volatility_bucket: str = ""
    inventory_state: str = ""
    trigger_reason: str = ""
    exit_reason: str = ""
    entry_edge_bps: float = 0.0
    entry_edge_usd: float = 0.0
    hold_minutes: float = 0.0


@dataclass
class EquityRecord:
    cycle_index: int
    mid_price: float
    equity_usd: float
    inventory_usd: float
    pnl_usd: float


@dataclass
class PerformanceTracker:
    start_usdc: float
    start_eth: float
    start_price: float
    start_value: float = field(init=False)
    gross_profit_usd: float = 0.0
    gross_loss_usd: float = 0.0
    max_loss_streak: int = 0
    current_loss_streak: int = 0
    max_pnl: float | None = None
    min_pnl: float | None = None
    equity_peak: float | None = None
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    trade_history: list[TradeRecord] = field(default_factory=list)
    equity_history: list[EquityRecord] = field(default_factory=list)
    closed_trade_pnls: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        reference_price = self.start_price if self.start_price > 0 else 0.0
        self.start_value = self.start_usdc + (self.start_eth * reference_price)

    @property
    def trade_count(self) -> int:
        return len(self.trade_history)

    @property
    def closed_trade_count(self) -> int:
        return sum(1 for trade in self.trade_history if trade.side == "sell")

    def record_equity(
        self,
        *,
        cycle_index: int,
        mid_price: float,
        equity_usd: float,
        inventory_usd: float,
    ) -> None:
        pnl_usd = equity_usd - self.start_value
        self.max_pnl = pnl_usd if self.max_pnl is None else max(self.max_pnl, pnl_usd)
        self.min_pnl = pnl_usd if self.min_pnl is None else min(self.min_pnl, pnl_usd)
        self.equity_peak = equity_usd if self.equity_peak is None else max(self.equity_peak, equity_usd)
        if self.equity_peak and self.equity_peak > 0:
            drawdown_usd = max(self.equity_peak - equity_usd, 0.0)
            self.max_drawdown_usd = max(self.max_drawdown_usd, drawdown_usd)
            drawdown_pct = drawdown_usd / self.equity_peak
            self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown_pct)

        self.equity_history.append(
            EquityRecord(
                cycle_index=cycle_index,
                mid_price=mid_price,
                equity_usd=equity_usd,
                inventory_usd=inventory_usd,
                pnl_usd=pnl_usd,
            )
        )

    def record_trade(
        self,
        *,
        cycle_index: int,
        side: str,
        price: float,
        size_usd: float,
        fee_usd: float,
        realized_pnl: float,
        usdc_after: float,
        eth_after: float,
        execution_type: str = "",
        slippage_bps: float = 0.0,
        trade_reason: str = "",
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        max_profit_during_trade: float = 0.0,
        active_regime: str = "",
        trend_direction: str = "",
        volatility_bucket: str = "",
        inventory_state: str = "",
        trigger_reason: str = "",
        exit_reason: str = "",
        entry_edge_bps: float = 0.0,
        entry_edge_usd: float = 0.0,
        hold_minutes: float = 0.0,
    ) -> None:
        self.trade_history.append(
            TradeRecord(
                cycle_index=cycle_index,
                side=side,
                price=price,
                size_usd=size_usd,
                fee_usd=fee_usd,
                realized_pnl=realized_pnl,
                execution_type=execution_type,
                slippage_bps=slippage_bps,
                trade_reason=trade_reason,
                usdc_after=usdc_after,
                eth_after=eth_after,
                entry_price=entry_price,
                exit_price=exit_price,
                max_profit_during_trade=max_profit_during_trade,
                active_regime=active_regime,
                trend_direction=trend_direction,
                volatility_bucket=volatility_bucket,
                inventory_state=inventory_state,
                trigger_reason=trigger_reason,
                exit_reason=exit_reason,
                entry_edge_bps=entry_edge_bps,
                entry_edge_usd=entry_edge_usd,
                hold_minutes=hold_minutes,
            )
        )

        if side != "sell":
            return

        if realized_pnl > 0:
            self.gross_profit_usd += realized_pnl
        elif realized_pnl < 0:
            self.gross_loss_usd += abs(realized_pnl)

        self.closed_trade_pnls.append(realized_pnl)
        if realized_pnl < 0:
            self.current_loss_streak += 1
            self.max_loss_streak = max(self.max_loss_streak, self.current_loss_streak)
        else:
            self.current_loss_streak = 0

    def build_summary(
        self,
        *,
        final_mid: float,
        final_usdc: float,
        final_eth: float,
        realized_pnl: float,
    ) -> dict:
        end_value = final_usdc + (final_eth * final_mid) if final_mid > 0 else final_usdc
        total_pnl = end_value - self.start_value
        unrealized_pnl = total_pnl - realized_pnl
        hodl_value = self.start_usdc + (self.start_eth * final_mid) if final_mid > 0 else self.start_value
        alpha_vs_hodl = end_value - hodl_value

        winning_trades = [value for value in self.closed_trade_pnls if value > 0]
        losing_trades = [value for value in self.closed_trade_pnls if value < 0]
        breakeven_trade_count = max(self.closed_trade_count - len(winning_trades) - len(losing_trades), 0)

        profit_factor = None
        if self.gross_loss_usd > 0:
            profit_factor = self.gross_profit_usd / self.gross_loss_usd
        elif self.gross_profit_usd > 0:
            profit_factor = None
        else:
            profit_factor = 0.0

        summary = {
            "start_value": self.start_value,
            "start_equity": self.start_value,
            "end_value": end_value,
            "final_equity": end_value,
            "total_pnl": total_pnl,
            "final_pnl": total_pnl,
            "realized_pnl": realized_pnl,
            "realized_pnl_usd": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "gross_profit_usd": self.gross_profit_usd,
            "gross_loss_usd": self.gross_loss_usd,
            "profit_factor": profit_factor,
            "win_rate": ((len(winning_trades) / self.closed_trade_count) * 100.0) if self.closed_trade_count else 0.0,
            "avg_win": (sum(winning_trades) / len(winning_trades)) if winning_trades else 0.0,
            "avg_loss": (sum(losing_trades) / len(losing_trades)) if losing_trades else 0.0,
            "avg_profit": (sum(winning_trades) / len(winning_trades)) if winning_trades else 0.0,
            "avg_profit_usd": (sum(winning_trades) / len(winning_trades)) if winning_trades else 0.0,
            "avg_loss_usd": (sum(losing_trades) / len(losing_trades)) if losing_trades else 0.0,
            "avg_loss_abs_usd": (sum(abs(value) for value in losing_trades) / len(losing_trades)) if losing_trades else 0.0,
            "winning_trade_count": len(winning_trades),
            "losing_trade_count": len(losing_trades),
            "breakeven_trade_count": breakeven_trade_count,
            "max_pnl": 0.0 if self.max_pnl is None else self.max_pnl,
            "min_pnl": 0.0 if self.min_pnl is None else self.min_pnl,
            "max_drawdown_usd": self.max_drawdown_usd,
            "max_drawdown_pct": self.max_drawdown_pct * 100.0,
            "max_drawdown": self.max_drawdown_pct * 100.0,
            "max_loss_streak": self.max_loss_streak,
            "trade_count": self.trade_count,
            "closed_trade_count": self.closed_trade_count,
            "pnl_per_trade": (total_pnl / self.trade_count) if self.trade_count else 0.0,
            "hodl_value": hodl_value,
            "alpha": alpha_vs_hodl,
            "alpha_vs_hodl": alpha_vs_hodl,
        }
        summary["daily_stats"] = {
            "realized_pnl": summary["realized_pnl"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "win_rate": summary["win_rate"],
            "trade_count": summary["trade_count"],
            "closed_trade_count": summary["closed_trade_count"],
            "avg_profit": summary["avg_profit"],
            "avg_loss": summary["avg_loss"],
            "avg_loss_abs_usd": summary["avg_loss_abs_usd"],
        }
        summary.update(build_verdict_snapshot(summary))
        return summary

    def trade_history_rows(self) -> list[dict]:
        return [asdict(trade) for trade in self.trade_history]

    def equity_history_rows(self) -> list[dict]:
        return [asdict(point) for point in self.equity_history]


def resolve_profit_factor_value(summary: dict) -> float:
    raw_value = summary.get("profit_factor")
    if raw_value is None:
        if summary.get("gross_profit_usd", 0.0) > 0 and summary.get("gross_loss_usd", 0.0) <= 0:
            return math.inf
        return 0.0
    return float(raw_value)


def profit_factor_display(summary: dict) -> str:
    value = resolve_profit_factor_value(summary)
    if math.isinf(value):
        return "inf"
    return f"{value:.4f}"


def build_verdict_snapshot(summary: dict) -> dict:
    trade_count = int(summary.get("trade_count", 0))
    alpha = float(summary.get("alpha_vs_hodl", summary.get("alpha", 0.0)))
    drawdown_pct = float(summary.get("max_drawdown_pct", 0.0))
    profit_factor_value = resolve_profit_factor_value(summary)

    minimum_trade_count_met = trade_count >= MINIMUM_VERDICT_TRADE_COUNT
    pass_profit_factor = profit_factor_value > PASS_PROFIT_FACTOR
    pass_drawdown = drawdown_pct < PASS_MAX_DRAWDOWN_PCT
    pass_alpha = alpha > 0
    fail_profit_factor = profit_factor_value < FAIL_PROFIT_FACTOR
    fail_alpha = alpha < 0

    reasons: list[str] = []
    if fail_profit_factor:
        reasons.append("profit_factor_below_fail_threshold")
    if fail_alpha:
        reasons.append("alpha_vs_hodl_negative")
    if not minimum_trade_count_met:
        reasons.append("insufficient_trade_count")

    if not minimum_trade_count_met:
        verdict = "INSUFFICIENT DATA"
    elif fail_profit_factor or fail_alpha:
        verdict = "FAIL"
    elif pass_profit_factor and pass_drawdown and pass_alpha:
        verdict = "PASS"
    else:
        verdict = "REVIEW"

    if verdict == "PASS":
        reasons.append("all_pass_conditions_met")
    elif verdict == "REVIEW":
        reasons.append("mixed_signals")

    return {
        "verdict": verdict,
        "verdict_reasons": reasons,
        "minimum_trade_count_met": minimum_trade_count_met,
        "pass_condition_profit_factor": pass_profit_factor,
        "pass_condition_drawdown": pass_drawdown,
        "pass_condition_alpha": pass_alpha,
        "fail_condition_profit_factor": fail_profit_factor,
        "fail_condition_alpha": fail_alpha,
        "verdict_profit_factor_value": profit_factor_value,
        "verdict_profit_factor_display": profit_factor_display(summary),
        # Compatibility aliases for the existing validation pipeline.
        "validation_status": verdict,
        "validation_reasons": reasons,
        "trade_count_requirement_met": minimum_trade_count_met,
        "validation_profit_factor_value": profit_factor_value,
        "validation_profit_factor_display": profit_factor_display(summary),
    }


def build_report(
    summary: dict,
    *,
    run_label: str = "",
    input_path: str = "",
    seed: int | None = None,
    variant: str = "",
    trade_history_path: str = "",
    equity_curve_path: str = "",
    summary_path: str = "",
) -> dict:
    verdict = build_verdict_snapshot(summary)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run": {
            "label": run_label,
            "input_path": input_path,
            "seed": seed,
            "variant": variant,
        },
        "artifacts": {
            "trade_history_csv": trade_history_path,
            "equity_curve_csv": equity_curve_path,
            "summary_json": summary_path,
        },
        "metrics": {
            "start_value": summary.get("start_value", 0.0),
            "end_value": summary.get("end_value", summary.get("final_equity", 0.0)),
            "hodl_value": summary.get("hodl_value", 0.0),
            "alpha": summary.get("alpha", 0.0),
            "alpha_vs_hodl": summary.get("alpha_vs_hodl", summary.get("alpha", 0.0)),
            "total_pnl": summary.get("total_pnl", summary.get("final_pnl", 0.0)),
            "realized_pnl": summary.get("realized_pnl", summary.get("realized_pnl_usd", 0.0)),
            "unrealized_pnl": summary.get("unrealized_pnl", 0.0),
            "profit_factor": verdict["verdict_profit_factor_display"],
            "win_rate": summary.get("win_rate", 0.0),
            "avg_win": summary.get("avg_win", 0.0),
            "avg_loss": summary.get("avg_loss", 0.0),
            "avg_profit": summary.get("avg_profit", summary.get("avg_win", 0.0)),
            "avg_loss_abs_usd": summary.get("avg_loss_abs_usd", abs(summary.get("avg_loss", 0.0))),
            "max_drawdown_pct": summary.get("max_drawdown_pct", 0.0),
            "max_loss_streak": summary.get("max_loss_streak", 0),
            "trade_count": summary.get("trade_count", 0),
            "closed_trade_count": summary.get("closed_trade_count", 0),
            "pnl_per_trade": summary.get("pnl_per_trade", 0.0),
            "daily_trade_count": summary.get("daily_trade_count", 0),
            "regime_trade_counts": summary.get("regime_trade_counts", {}),
            "regime_realized_pnl_usd": summary.get("regime_realized_pnl_usd", {}),
            "avg_hold_minutes": summary.get("avg_hold_minutes", 0.0),
            "inventory_drift_avg_pct": summary.get("inventory_drift_avg_pct", 0.0),
            "inventory_drift_max_pct": summary.get("inventory_drift_max_pct", 0.0),
            "rejection_reason_stats": summary.get("rejection_reason_stats", {}),
        },
        "verdict": {
            "status": verdict["verdict"],
            "reasons": verdict["verdict_reasons"],
            "minimum_trade_count_met": verdict["minimum_trade_count_met"],
            "pass_condition_profit_factor": verdict["pass_condition_profit_factor"],
            "pass_condition_drawdown": verdict["pass_condition_drawdown"],
            "pass_condition_alpha": verdict["pass_condition_alpha"],
            "fail_condition_profit_factor": verdict["fail_condition_profit_factor"],
            "fail_condition_alpha": verdict["fail_condition_alpha"],
        },
        # Compatibility alias for existing exporters.
        "validation": {
            "status": verdict["validation_status"],
            "reasons": verdict["validation_reasons"],
            "trade_count_requirement_met": verdict["trade_count_requirement_met"],
            "pass_condition_profit_factor": verdict["pass_condition_profit_factor"],
            "pass_condition_drawdown": verdict["pass_condition_drawdown"],
            "pass_condition_alpha": verdict["pass_condition_alpha"],
            "fail_condition_profit_factor": verdict["fail_condition_profit_factor"],
            "fail_condition_alpha": verdict["fail_condition_alpha"],
        },
    }
    return report


def flatten_report(report: dict) -> dict:
    run = report.get("run", {})
    artifacts = report.get("artifacts", {})
    metrics = report.get("metrics", {})
    verdict = report.get("verdict", {})
    return {
        "generated_at_utc": report.get("generated_at_utc", ""),
        "label": run.get("label", ""),
        "input_path": run.get("input_path", ""),
        "seed": run.get("seed", ""),
        "variant": run.get("variant", ""),
        "trade_history_csv": artifacts.get("trade_history_csv", ""),
        "equity_curve_csv": artifacts.get("equity_curve_csv", ""),
        "summary_json": artifacts.get("summary_json", ""),
        "start_value": metrics.get("start_value", 0.0),
        "end_value": metrics.get("end_value", 0.0),
        "hodl_value": metrics.get("hodl_value", 0.0),
        "alpha": metrics.get("alpha", 0.0),
        "alpha_vs_hodl": metrics.get("alpha_vs_hodl", metrics.get("alpha", 0.0)),
        "total_pnl": metrics.get("total_pnl", 0.0),
        "realized_pnl": metrics.get("realized_pnl", 0.0),
        "unrealized_pnl": metrics.get("unrealized_pnl", 0.0),
        "profit_factor": metrics.get("profit_factor", ""),
        "win_rate": metrics.get("win_rate", 0.0),
        "avg_win": metrics.get("avg_win", 0.0),
        "avg_loss": metrics.get("avg_loss", 0.0),
        "avg_profit": metrics.get("avg_profit", metrics.get("avg_win", 0.0)),
        "avg_loss_abs_usd": metrics.get("avg_loss_abs_usd", abs(metrics.get("avg_loss", 0.0))),
        "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
        "max_loss_streak": metrics.get("max_loss_streak", 0),
        "trade_count": metrics.get("trade_count", 0),
        "closed_trade_count": metrics.get("closed_trade_count", 0),
        "pnl_per_trade": metrics.get("pnl_per_trade", 0.0),
        "daily_trade_count": metrics.get("daily_trade_count", 0),
        "avg_hold_minutes": metrics.get("avg_hold_minutes", 0.0),
        "inventory_drift_avg_pct": metrics.get("inventory_drift_avg_pct", 0.0),
        "inventory_drift_max_pct": metrics.get("inventory_drift_max_pct", 0.0),
        "regime_trade_counts": _json_compact(metrics.get("regime_trade_counts", {})),
        "regime_realized_pnl_usd": _json_compact(metrics.get("regime_realized_pnl_usd", {})),
        "rejection_reason_stats": _json_compact(metrics.get("rejection_reason_stats", {})),
        "verdict": verdict.get("status", ""),
        "verdict_reasons": "|".join(verdict.get("reasons", [])),
        "minimum_trade_count_met": verdict.get("minimum_trade_count_met", False),
        "pass_condition_profit_factor": verdict.get("pass_condition_profit_factor", False),
        "pass_condition_drawdown": verdict.get("pass_condition_drawdown", False),
        "pass_condition_alpha": verdict.get("pass_condition_alpha", False),
        "fail_condition_profit_factor": verdict.get("fail_condition_profit_factor", False),
        "fail_condition_alpha": verdict.get("fail_condition_alpha", False),
    }


def write_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)


def write_report_csv(report: dict, output_path: Path) -> None:
    row = flatten_report(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def log_performance_summary(summary: dict, log_fn) -> None:
    log_fn(f"Final equity: {summary['final_equity']:.2f}")
    log_fn(f"Total pnl: {summary['total_pnl']:.2f}")
    log_fn(f"Realized pnl: {summary['realized_pnl']:.4f}")
    log_fn(f"Unrealized pnl: {summary['unrealized_pnl']:.4f}")
    log_fn(f"Trades: {summary['trade_count']}")
    log_fn(f"Closed trades: {summary['closed_trade_count']}")
    log_fn(f"PnL / trade: {summary['pnl_per_trade']:.4f}")
    log_fn(f"Win rate: {summary['win_rate']:.2f}%")
    log_fn(f"Avg win: {summary['avg_win']:.4f}")
    log_fn(f"Avg loss: {summary['avg_loss']:.4f}")
    log_fn(f"Avg profit: {summary['avg_profit']:.4f}")
    log_fn(f"Avg loss abs: {summary['avg_loss_abs_usd']:.4f}")
    log_fn(f"Max loss streak: {summary['max_loss_streak']}")
    if summary["profit_factor"] is None:
        log_fn("Profit factor: inf")
    else:
        log_fn(f"Profit factor: {summary['profit_factor']:.4f}")
    log_fn(f"Max drawdown: {summary['max_drawdown_usd']:.2f} ({summary['max_drawdown_pct']:.2f}%)")
    log_fn(f"HODL value: {summary['hodl_value']:.2f}")
    log_fn(f"Alpha vs HODL: {summary['alpha_vs_hodl']:.2f}")
    log_fn(
        f"Verdict: {summary['verdict']} | PF {summary['verdict_profit_factor_display']} | "
        f"alpha {summary['alpha_vs_hodl']:.2f} | trade_count_ok {summary['minimum_trade_count_met']}"
    )
