import argparse
import csv
import json
import random
from datetime import datetime
from pathlib import Path

from backtest import load_price_rows, resolve_cycle_seconds
from bot_runner import build_summary, create_runtime, process_price_tick, resolve_start_balances
from logger import log
from log_cleanup import cleanup_logs_for_run, format_cleanup_result

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
    },
)


def parse_args():
    parser = argparse.ArgumentParser(description="Compare the current adaptive bot against the new re-entry engine.")
    parser.add_argument("--input", required=True, help="Path to the historical CSV file.")
    parser.add_argument("--price-column", default="price", help="CSV column that contains the price.")
    parser.add_argument(
        "--source-column",
        default="",
        help="Optional CSV column for the source label. Defaults to 'historical'.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of rows to replay.")
    parser.add_argument("--seeds", default="41,42,43", help="Comma-separated random seeds for the fill model.")
    parser.add_argument(
        "--cycle-seconds",
        type=float,
        default=0.0,
        help="Optional cycle duration in seconds. When omitted, tries to infer from the input filename.",
    )
    parser.add_argument(
        "--output-dir",
        default=r"logs\backtests",
        help="Directory for generated benchmark outputs.",
    )
    parser.add_argument("--label", default="variant_benchmark", help="Optional label used in the output filenames.")
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


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sanitize_label(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "variant_benchmark"


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


def summarize_variant(variant: str, cycle_seconds: float, summaries: list[dict]) -> dict:
    profit_factors = [summary["profit_factor"] for summary in summaries if summary["profit_factor"] is not None]
    run_count = len(summaries)
    pass_rate = (
        average([1.0 if summary["verdict"] == "PASS" else 0.0 for summary in summaries]) * 100.0
        if summaries
        else 0.0
    )
    fail_rate = (
        average([1.0 if summary["verdict"] == "FAIL" else 0.0 for summary in summaries]) * 100.0
        if summaries
        else 0.0
    )
    insufficient_rate = (
        average([1.0 if summary["verdict"] == "INSUFFICIENT DATA" else 0.0 for summary in summaries])
        * 100.0
        if summaries
        else 0.0
    )
    return {
        "variant": variant,
        "cycle_seconds": cycle_seconds,
        "run_count": run_count,
        "avg_total_pnl": round(average([summary["total_pnl"] for summary in summaries]), 6),
        "avg_final_pnl": round(average([summary["final_pnl"] for summary in summaries]), 6),
        "avg_return_pct": round(average([summary["return_pct"] for summary in summaries]), 6),
        "avg_realized_pnl": round(average([summary["realized_pnl"] for summary in summaries]), 6),
        "avg_unrealized_pnl": round(average([summary["unrealized_pnl"] for summary in summaries]), 6),
        "avg_final_eth": round(average([summary["final_eth"] for summary in summaries]), 8),
        "avg_eth_delta": round(average([summary["eth_delta"] for summary in summaries]), 8),
        "avg_final_usdc": round(average([summary["final_usdc"] for summary in summaries]), 6),
        "avg_usdc_delta": round(average([summary["usdc_delta"] for summary in summaries]), 6),
        "avg_profit_factor": round(average(profit_factors), 6) if profit_factors else None,
        "avg_win_rate": round(average([summary["win_rate"] for summary in summaries]), 6),
        "avg_avg_win": round(average([summary["avg_win"] for summary in summaries]), 6),
        "avg_avg_loss": round(average([summary["avg_loss"] for summary in summaries]), 6),
        "avg_max_drawdown_usd": round(average([summary["max_drawdown_usd"] for summary in summaries]), 6),
        "avg_max_drawdown_pct": round(average([summary["max_drawdown_pct"] for summary in summaries]), 6),
        "avg_max_loss_streak": round(average([summary["max_loss_streak"] for summary in summaries]), 6),
        "avg_trade_count": round(average([summary["trade_count"] for summary in summaries]), 6),
        "avg_closed_trade_count": round(average([summary["closed_trade_count"] for summary in summaries]), 6),
        "avg_buy_count": round(average([summary["buy_count"] for summary in summaries]), 6),
        "avg_sell_count": round(average([summary["sell_count"] for summary in summaries]), 6),
        "avg_pnl_per_trade": round(average([summary["pnl_per_trade"] for summary in summaries]), 6),
        "avg_alpha": round(average([summary["alpha"] for summary in summaries]), 6),
        "trade_count_requirement_pass_rate": round(
            average([1.0 if summary["minimum_trade_count_met"] else 0.0 for summary in summaries]) * 100.0,
            6,
        ),
        "pass_rate": round(pass_rate, 6),
        "fail_rate": round(fail_rate, 6),
        "insufficient_rate": round(insufficient_rate, 6),
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

    rows = load_price_rows(
        csv_path=csv_path,
        price_column=args.price_column,
        source_column=args.source_column,
        limit=args.limit,
    )
    if not rows:
        raise ValueError("No valid price rows found in the input CSV.")

    cycle_seconds = resolve_cycle_seconds(csv_path, args.cycle_seconds)
    seeds = parse_int_list(args.seeds)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    stem = sanitize_label(args.label)
    csv_output = output_root / f"{stem}_{timestamp}.csv"
    json_output = output_root / f"{stem}_{timestamp}.json"
    cleanup_result = cleanup_logs_for_run([csv_output, json_output])
    log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

    start_usdc, start_eth = resolve_start_balances(reference_price=rows[0][0])

    log(f"Variant benchmark input: {csv_path}")
    log(f"Loaded rows: {len(rows)}")
    log(f"Cycle seconds: {cycle_seconds:.0f}")
    log(f"Seeds: {seeds}")
    log(f"Start balances | usdc {start_usdc:.2f} | eth {start_eth:.8f}")

    per_variant_results: dict[str, list[dict]] = {}
    summary_rows: list[dict] = []
    for variant in VARIANTS:
        variant_name = variant["name"]
        summaries = [run_variant(rows, seed, cycle_seconds, variant) for seed in seeds]
        per_variant_results[variant_name] = summaries
        summary = summarize_variant(variant_name, cycle_seconds, summaries)
        summary_rows.append(summary)
        profit_factor_text = "inf" if summary["avg_profit_factor"] is None else f"{summary['avg_profit_factor']:.4f}"
        log(
            f"{variant_name} | avg pnl {summary['avg_final_pnl']:.2f} | "
            f"avg eth delta {summary['avg_eth_delta']:.8f} | "
            f"profit factor {profit_factor_text} | "
            f"avg dd {summary['avg_max_drawdown_usd']:.2f}"
        )

    write_csv(summary_rows, csv_output)
    with json_output.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "input": str(csv_path),
                "cycle_seconds": cycle_seconds,
                "seeds": seeds,
                "summary_rows": summary_rows,
                "runs": per_variant_results,
            },
            handle,
            indent=2,
        )

    log(f"Variant benchmark CSV: {csv_output}")
    log(f"Variant benchmark JSON: {json_output}")


if __name__ == "__main__":
    main()
