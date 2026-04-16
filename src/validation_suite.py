import argparse
import csv
import json
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

from backtest import load_price_rows, resolve_cycle_seconds
from bot_runner import build_summary, create_runtime, process_price_tick, resolve_start_balances
from logger import log
from log_cleanup import cleanup_logs_for_run, format_cleanup_result
from performance import build_report, flatten_report

VARIANTS = (
    {
        "name": "current_system",
        "enable_trade_filter": True,
        "enable_inventory_manager": True,
        "enable_reentry_engine": True,
        "enable_decision_engine": False,
        "enable_execution_engine": True,
        "enable_state_machine": True,
    },
    {
        "name": "decision_engine_system",
        "enable_trade_filter": True,
        "enable_inventory_manager": True,
        "enable_reentry_engine": True,
        "enable_decision_engine": True,
        "enable_execution_engine": True,
        "enable_state_machine": True,
        "adaptive_flags": None,
    },
    {
        "name": "adaptive_regime_aware",
        "enable_trade_filter": True,
        "enable_inventory_manager": True,
        "enable_reentry_engine": True,
        "enable_decision_engine": True,
        "enable_execution_engine": True,
        "enable_state_machine": True,
        "adaptive_flags": {
            "enabled": True,
            "regime_enabled": True,
            "edge_enabled": True,
            "mode_selector_enabled": True,
            "dynamic_quoting_enabled": True,
            "risk_governor_enabled": True,
            "performance_adaptation_enabled": True,
            "inventory_bands_enabled": True,
            "fill_quality_enabled": True,
            "soft_filters_enabled": True,
            "logging_enabled": True,
        },
    },
)

PERIOD_PRESETS = {
    "full": (0.0, 1.0),
    "first_half": (0.0, 0.5),
    "second_half": (0.5, 1.0),
    "last_quarter": (0.75, 1.0),
}


def parse_args():
    parser = argparse.ArgumentParser(description="Run automatic multi-period, multi-seed validation for the trading bot.")
    parser.add_argument("--input", required=True, help="Path to the historical CSV file.")
    parser.add_argument("--price-column", default="price", help="CSV column that contains the price.")
    parser.add_argument("--source-column", default="", help="Optional CSV column for the source label.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of rows to replay before slicing.")
    parser.add_argument("--seeds", default="41,42,43", help="Comma-separated random seeds for deterministic fills.")
    parser.add_argument(
        "--periods",
        default="full,first_half,second_half,last_quarter",
        help="Comma-separated validation periods: full, first_half, second_half, last_quarter.",
    )
    parser.add_argument(
        "--cycle-seconds",
        type=float,
        default=0.0,
        help="Optional cycle duration in seconds. When omitted, tries to infer from the input filename.",
    )
    parser.add_argument(
        "--output-dir",
        default=r"logs\validations",
        help="Directory for the generated validation outputs.",
    )
    parser.add_argument("--label", default="validation_suite", help="Optional label used in the output filenames.")
    return parser.parse_args()


def parse_int_list(value: str) -> list[int]:
    items = []
    for part in value.split(","):
        stripped = part.strip()
        if stripped:
            items.append(int(stripped))
    if not items:
        raise ValueError("Expected at least one seed.")
    return items


def parse_period_list(value: str) -> list[str]:
    periods = [part.strip() for part in value.split(",") if part.strip()]
    if not periods:
        raise ValueError("Expected at least one period.")
    for period in periods:
        if period not in PERIOD_PRESETS:
            raise ValueError(f"Unsupported period '{period}'. Supported: {', '.join(sorted(PERIOD_PRESETS))}")
    return periods


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sanitize_label(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "validation_suite"


def slice_period_rows(rows: list[tuple[float, str]], period_name: str) -> list[tuple[float, str]]:
    start_ratio, end_ratio = PERIOD_PRESETS[period_name]
    total = len(rows)
    start_index = min(int(total * start_ratio), max(total - 1, 0))
    end_index = max(int(total * end_ratio), start_index + 1)
    sliced = rows[start_index:end_index]
    if len(sliced) < 2:
        raise ValueError(f"Period '{period_name}' produced too few rows ({len(sliced)}).")
    return sliced


def run_variant(rows: list[tuple[float, str]], seed: int, cycle_seconds: float, variant: dict) -> dict:
    random.seed(seed)
    runtime = create_runtime(
        reference_price=rows[0][0],
        cycle_seconds=cycle_seconds,
        enable_reentry_engine=variant["enable_reentry_engine"],
        enable_decision_engine=variant["enable_decision_engine"],
        enable_execution_engine=variant["enable_execution_engine"],
        enable_trade_filter=variant["enable_trade_filter"],
        enable_inventory_manager=variant["enable_inventory_manager"],
        enable_state_machine=variant["enable_state_machine"],
        adaptive_flags=variant.get("adaptive_flags"),
    )

    for cycle_index, (mid, source) in enumerate(rows):
        should_continue = process_price_tick(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=mid,
            source=source,
            trade_logger=None,
            equity_logger=None,
            log_progress=False,
        )
        if not should_continue:
            break

    return build_summary(runtime)


def summarize_group(variant_name: str, period_name: str, summaries: list[dict]) -> dict:
    profit_factors = [summary["profit_factor"] for summary in summaries if summary["profit_factor"] is not None]
    status_counts = Counter(summary["verdict"] for summary in summaries)
    return {
        "variant": variant_name,
        "period": period_name,
        "run_count": len(summaries),
        "avg_total_pnl": round(average([summary["total_pnl"] for summary in summaries]), 6),
        "avg_realized_pnl": round(average([summary["realized_pnl"] for summary in summaries]), 6),
        "avg_unrealized_pnl": round(average([summary["unrealized_pnl"] for summary in summaries]), 6),
        "avg_profit_factor": round(average(profit_factors), 6) if profit_factors else None,
        "avg_win_rate": round(average([summary["win_rate"] for summary in summaries]), 6),
        "avg_avg_win": round(average([summary["avg_win"] for summary in summaries]), 6),
        "avg_avg_loss": round(average([summary["avg_loss"] for summary in summaries]), 6),
        "avg_max_drawdown_pct": round(average([summary["max_drawdown_pct"] for summary in summaries]), 6),
        "avg_max_loss_streak": round(average([summary["max_loss_streak"] for summary in summaries]), 6),
        "avg_trade_count": round(average([summary["trade_count"] for summary in summaries]), 6),
        "avg_closed_trade_count": round(average([summary["closed_trade_count"] for summary in summaries]), 6),
        "avg_idle_time_ratio": round(average([summary.get("idle_time_ratio", summary.get("no_trade_ratio", 0.0)) for summary in summaries]), 6),
        "avg_toxic_fill_ratio": round(average([summary.get("toxic_fill_ratio", 0.0) for summary in summaries]), 6),
        "avg_pnl_per_trade": round(average([summary["pnl_per_trade"] for summary in summaries]), 6),
        "avg_alpha": round(average([summary["alpha"] for summary in summaries]), 6),
        "min_alpha": round(min(summary["alpha"] for summary in summaries), 6),
        "max_alpha": round(max(summary["alpha"] for summary in summaries), 6),
        "trade_count_requirement_pass_rate": round(
            average([1.0 if summary["minimum_trade_count_met"] else 0.0 for summary in summaries]) * 100.0,
            6,
        ),
        "pass_rate": round((status_counts.get("PASS", 0) / len(summaries)) * 100.0, 6) if summaries else 0.0,
        "fail_rate": round((status_counts.get("FAIL", 0) / len(summaries)) * 100.0, 6) if summaries else 0.0,
        "insufficient_rate": round(
            (status_counts.get("INSUFFICIENT DATA", 0) / len(summaries)) * 100.0,
            6,
        )
        if summaries
        else 0.0,
        "review_rate": round((status_counts.get("REVIEW", 0) / len(summaries)) * 100.0, 6) if summaries else 0.0,
    }


def write_csv(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    csv_path = Path(args.input)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    seeds = parse_int_list(args.seeds)
    periods = parse_period_list(args.periods)
    rows = load_price_rows(
        csv_path=csv_path,
        price_column=args.price_column,
        source_column=args.source_column,
        limit=args.limit,
    )
    if not rows:
        raise ValueError("No valid price rows found in the input CSV.")

    cycle_seconds = resolve_cycle_seconds(csv_path, args.cycle_seconds)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_label(args.label)
    runs_csv = output_dir / f"{stem}_{timestamp}_runs.csv"
    aggregate_csv = output_dir / f"{stem}_{timestamp}_aggregate.csv"
    report_json = output_dir / f"{stem}_{timestamp}.json"
    cleanup_result = cleanup_logs_for_run([runs_csv, aggregate_csv, report_json])
    log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

    start_usdc, start_eth = resolve_start_balances(reference_price=rows[0][0])
    log(f"Validation suite input: {csv_path}")
    log(f"Loaded rows: {len(rows)}")
    log(f"Cycle seconds: {cycle_seconds:.0f}")
    log(f"Seeds: {seeds}")
    log(f"Periods: {periods}")
    log(f"Variants: {[variant['name'] for variant in VARIANTS]}")
    log(f"Start balances | usdc {start_usdc:.2f} | eth {start_eth:.8f}")

    run_reports: list[dict] = []
    summary_groups: dict[tuple[str, str], list[dict]] = {}
    for period_name in periods:
        period_rows = slice_period_rows(rows, period_name)
        for variant in VARIANTS:
            variant_name = variant["name"]
            summaries: list[dict] = []
            for seed in seeds:
                summary = run_variant(period_rows, seed, cycle_seconds, variant)
                summaries.append(summary)
                report = build_report(
                    summary,
                    run_label=f"{variant_name}_{period_name}_seed_{seed}",
                    input_path=str(csv_path),
                    seed=seed,
                    variant=variant_name,
                )
                flattened = flatten_report(report)
                flattened["period"] = period_name
                flattened["rows"] = len(period_rows)
                run_reports.append(flattened)
            summary_groups[(variant_name, period_name)] = summaries
            aggregate = summarize_group(variant_name, period_name, summaries)
            log(
                f"{variant_name} | {period_name} | avg pnl {aggregate['avg_total_pnl']:.2f} | "
                f"avg alpha {aggregate['avg_alpha']:.2f} | avg dd {aggregate['avg_max_drawdown_pct']:.2f}% | "
                f"pass {aggregate['pass_rate']:.1f}% | fail {aggregate['fail_rate']:.1f}% | "
                f"idle {aggregate['avg_idle_time_ratio']:.2%} | toxic {aggregate['avg_toxic_fill_ratio']:.2f}"
            )

    aggregate_rows = [
        summarize_group(variant_name, period_name, summaries)
        for (variant_name, period_name), summaries in sorted(summary_groups.items())
    ]

    write_csv(run_reports, runs_csv)
    write_csv(aggregate_rows, aggregate_csv)
    with report_json.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "input": str(csv_path),
                "cycle_seconds": cycle_seconds,
                "seeds": seeds,
                "periods": periods,
                "variants": [variant["name"] for variant in VARIANTS],
                "runs": run_reports,
                "aggregate": aggregate_rows,
            },
            handle,
            indent=2,
        )

    log(f"Validation suite runs CSV: {runs_csv}")
    log(f"Validation suite aggregate CSV: {aggregate_csv}")
    log(f"Validation suite JSON: {report_json}")


if __name__ == "__main__":
    main()
