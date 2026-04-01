import argparse
import csv
import json
import random
import re
from datetime import datetime
from pathlib import Path

from bot_runner import (
    build_summary,
    create_runtime,
    equity_log_headers,
    log_summary,
    process_price_tick,
    resolve_start_balances,
    trade_log_headers,
)
from csv_logger import CsvLogger
from config import LOOP_SECONDS, SQLITE_LOG_PATH
from logger import close_log_sinks, log, register_log_sink
from log_cleanup import cleanup_logs_for_run, format_cleanup_result
from performance import build_report, write_report_csv, write_report_json
from sqlite_logger import SqliteLogger


def parse_args():
    parser = argparse.ArgumentParser(description="Run the strategy on historical CSV prices.")
    parser.add_argument("--input", required=True, help="Path to the historical CSV file.")
    parser.add_argument("--price-column", default="price", help="CSV column that contains the price.")
    parser.add_argument(
        "--source-column",
        default="",
        help="Optional CSV column for the source label. Defaults to 'historical'.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of rows to replay.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic fills.")
    parser.add_argument(
        "--output-dir",
        default=r"logs\backtests",
        help="Directory for generated backtest logs.",
    )
    parser.add_argument("--summary-json", default="", help="Optional JSON file path for the backtest summary.")
    parser.add_argument("--report-json", default="", help="Optional JSON file path for the performance report.")
    parser.add_argument("--report-csv", default="", help="Optional CSV file path for the performance report.")
    parser.add_argument("--label", default="", help="Optional label used in the output filenames.")
    parser.add_argument(
        "--cycle-seconds",
        type=float,
        default=0.0,
        help="Optional cycle duration in seconds. When omitted, tries to infer from the input filename.",
    )
    parser.add_argument(
        "--disable-reentry",
        action="store_true",
        help="Replay the baseline adaptive strategy without the new re-entry engine.",
    )
    parser.add_argument(
        "--disable-decision-engine",
        action="store_true",
        help="Replay with the legacy direct decision flow instead of the central decision engine.",
    )
    parser.add_argument(
        "--disable-execution",
        action="store_true",
        help="Replay without the execution engine layer.",
    )
    parser.add_argument(
        "--disable-trade-filter",
        action="store_true",
        help="Replay without the trade filter layer.",
    )
    parser.add_argument(
        "--disable-inventory-manager",
        action="store_true",
        help="Replay without the inventory manager layer.",
    )
    parser.add_argument(
        "--disable-state-machine",
        action="store_true",
        help="Replay the legacy decision flow without the state machine layer.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every replayed cycle.")
    return parser.parse_args()


def sanitize_label(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_")


def build_output_paths(input_path: Path, output_dir: Path, label: str) -> tuple[Path, Path, Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = sanitize_label(label) or sanitize_label(input_path.stem) or "backtest"
    output_dir.mkdir(parents=True, exist_ok=True)
    return (
        output_dir / f"{stem}_{timestamp}_trades.csv",
        output_dir / f"{stem}_{timestamp}_equity.csv",
        output_dir / f"{stem}_{timestamp}_report.json",
        output_dir / f"{stem}_{timestamp}_report.csv",
    )


def resolve_variant_name(args) -> str:
    tokens: list[str] = []
    tokens.append("decision" if not args.disable_decision_engine else "legacy_decision")
    if not args.disable_state_machine:
        tokens.append("state_machine")
    if not args.disable_reentry:
        tokens.append("reentry")
    if not args.disable_execution:
        tokens.append("execution")
    if not args.disable_trade_filter:
        tokens.append("trade_filter")
    if not args.disable_inventory_manager:
        tokens.append("inventory")
    return "+".join(tokens)


def load_price_rows(csv_path: Path, price_column: str, source_column: str, limit: int) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")
        if price_column not in reader.fieldnames:
            raise ValueError(f"Missing price column: {price_column}")
        if source_column and source_column not in reader.fieldnames:
            raise ValueError(f"Missing source column: {source_column}")

        for row in reader:
            raw_price = row.get(price_column, "")
            if not raw_price:
                continue

            try:
                price = float(raw_price)
            except ValueError:
                continue

            source = row.get(source_column, "") if source_column else ""
            rows.append((price, source or "historical"))

            if limit > 0 and len(rows) >= limit:
                break

    return rows


def resolve_cycle_seconds(input_path: Path, override_seconds: float) -> float:
    if override_seconds > 0:
        return override_seconds

    match = re.search(r"_(\d+)s(?:_|$)", input_path.stem)
    if match:
        return float(match.group(1))
    return LOOP_SECONDS


def main():
    close_log_sinks()
    register_log_sink(SqliteLogger(SQLITE_LOG_PATH))
    try:
        args = parse_args()
        csv_path = Path(args.input)
        if not csv_path.exists():
            raise FileNotFoundError(f"Input CSV not found: {csv_path}")

        random.seed(args.seed)

        rows = load_price_rows(
            csv_path=csv_path,
            price_column=args.price_column,
            source_column=args.source_column,
            limit=args.limit,
        )
        if not rows:
            raise ValueError("No valid price rows found in the input CSV.")

        trades_path, equity_path, default_report_json, default_report_csv = build_output_paths(
            input_path=csv_path,
            output_dir=Path(args.output_dir),
            label=args.label,
        )
        summary_path = Path(args.summary_json) if args.summary_json else None
        report_json_path = Path(args.report_json) if args.report_json else default_report_json
        report_csv_path = Path(args.report_csv) if args.report_csv else default_report_csv
        cleanup_paths = [trades_path, equity_path, report_json_path, report_csv_path]
        if summary_path is not None:
            cleanup_paths.append(summary_path)
        cleanup_result = cleanup_logs_for_run(cleanup_paths)
        log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

        trade_logger = CsvLogger(
            str(trades_path),
            trade_log_headers(),
        )
        equity_logger = CsvLogger(
            str(equity_path),
            equity_log_headers(),
        )

        reference_price = rows[0][0]
        cycle_seconds = resolve_cycle_seconds(csv_path, args.cycle_seconds)
        start_usdc, start_eth = resolve_start_balances(reference_price=reference_price)
        runtime = create_runtime(
            reference_price=reference_price,
            cycle_seconds=cycle_seconds,
            enable_reentry_engine=not args.disable_reentry,
            enable_decision_engine=not args.disable_decision_engine,
            enable_execution_engine=not args.disable_execution,
            enable_trade_filter=not args.disable_trade_filter,
            enable_inventory_manager=not args.disable_inventory_manager,
            enable_state_machine=not args.disable_state_machine,
        )

        log(f"Backtest input: {csv_path}")
        log(f"Loaded rows: {len(rows)}")
        log(f"Random seed: {args.seed}")
        log(
            f"Start portfolio resolved | ref {reference_price:.2f} | usdc {start_usdc:.2f} | "
            f"eth {start_eth:.8f}"
        )
        log(f"Cycle seconds: {cycle_seconds:.0f}")
        log(f"Re-entry engine: {runtime.enable_reentry_engine}")
        log(f"Decision engine: {runtime.enable_decision_engine}")
        log(f"Execution engine: {runtime.enable_execution_engine}")
        log(f"Trade filter: {runtime.enable_trade_filter}")
        log(f"Inventory manager: {runtime.enable_inventory_manager}")
        log(f"State machine: {runtime.enable_state_machine}")

        for cycle_index, (mid, source) in enumerate(rows):
            should_continue = process_price_tick(
                runtime=runtime,
                cycle_index=cycle_index,
                mid=mid,
                source=source,
                trade_logger=trade_logger,
                equity_logger=equity_logger,
                log_progress=args.verbose,
            )
            if not should_continue:
                break

        summary = build_summary(runtime)
        log_summary(summary)
        summary_path_text = ""
        if summary_path is not None:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            with summary_path.open("w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)
            log(f"Backtest summary JSON: {summary_path}")
            summary_path_text = str(summary_path)

        report = build_report(
            summary,
            run_label=args.label or csv_path.stem,
            input_path=str(csv_path),
            seed=args.seed,
            variant=resolve_variant_name(args),
            trade_history_path=str(trades_path),
            equity_curve_path=str(equity_path),
            summary_path=summary_path_text,
        )
        write_report_json(report, report_json_path)
        write_report_csv(report, report_csv_path)
        log(f"Backtest report JSON: {report_json_path}")
        log(f"Backtest report CSV: {report_csv_path}")
        log(f"Backtest trades log: {trades_path}")
        log(f"Backtest equity log: {equity_path}")
    finally:
        close_log_sinks()


if __name__ == "__main__":
    main()
