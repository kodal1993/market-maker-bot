from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime
from pathlib import Path

from backtest import load_price_rows
from bot_runner import build_summary, create_runtime, process_price_tick
from logger import log
from log_cleanup import cleanup_logs_for_run, format_cleanup_result
from multi_timeframe import required_bootstrap_price_rows


MODE_CONFIGS = {
    "5m_only": {
        "execution_timeframe_seconds": 300.0,
        "trend_timeframe_seconds": 300.0,
        "enable_trend_timeframe_filter": False,
        "enable_confirmation_filter": False,
        "confirmation_timeframe_seconds": 60.0,
    },
    "15m_trend_5m_execution": {
        "execution_timeframe_seconds": 300.0,
        "trend_timeframe_seconds": 900.0,
        "enable_trend_timeframe_filter": True,
        "enable_confirmation_filter": False,
        "confirmation_timeframe_seconds": 60.0,
    },
    "15m_trend_5m_execution_1m_confirmation": {
        "execution_timeframe_seconds": 300.0,
        "trend_timeframe_seconds": 900.0,
        "enable_trend_timeframe_filter": True,
        "enable_confirmation_filter": True,
        "confirmation_timeframe_seconds": 60.0,
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description="Compare the strategy across multi-timeframe execution modes.")
    parser.add_argument("--input", required=True, help="Historical CSV input path.")
    parser.add_argument("--price-column", default="price", help="CSV column that contains the price.")
    parser.add_argument("--source-column", default="", help="Optional source column.")
    parser.add_argument("--cycle-seconds", type=float, default=60.0, help="Input candle duration in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of rows to replay.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic fills.")
    parser.add_argument(
        "--output",
        default="",
        help="Optional summary CSV output path.",
    )
    return parser.parse_args()


def resolve_output_path(path_arg: str) -> Path:
    if path_arg:
        return Path(path_arg)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("logs") / "backtests" / f"multi_timeframe_benchmark_{timestamp}.csv"


def run_mode(rows: list[tuple[float, str]], *, cycle_seconds: float, seed: int, mode_name: str) -> dict[str, object]:
    mode_config = dict(MODE_CONFIGS[mode_name])
    bootstrap_rows = min(
        required_bootstrap_price_rows(
            cycle_seconds=cycle_seconds,
            configured_rows=0,
            execution_timeframe_seconds=mode_config["execution_timeframe_seconds"],
            trend_timeframe_seconds=mode_config["trend_timeframe_seconds"],
            confirmation_timeframe_seconds=mode_config["confirmation_timeframe_seconds"],
            enable_trend_filter=mode_config["enable_trend_timeframe_filter"],
            enable_confirmation=mode_config["enable_confirmation_filter"],
        ),
        max(len(rows) - 1, 0),
    )
    bootstrap_prices = [price for price, _source in rows[:bootstrap_rows]]
    start_index = bootstrap_rows if bootstrap_rows < len(rows) else 0

    random.seed(seed)
    runtime = create_runtime(
        bootstrap_prices=bootstrap_prices,
        reference_price=rows[start_index][0],
        cycle_seconds=cycle_seconds,
        execution_timeframe_seconds=mode_config["execution_timeframe_seconds"],
        trend_timeframe_seconds=mode_config["trend_timeframe_seconds"],
        confirmation_timeframe_seconds=mode_config["confirmation_timeframe_seconds"],
        enable_trend_timeframe_filter=mode_config["enable_trend_timeframe_filter"],
        enable_confirmation_filter=mode_config["enable_confirmation_filter"],
    )

    for cycle_index, (mid, source) in enumerate(rows[start_index:], start=start_index):
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

    summary = build_summary(runtime)
    return {
        "mode": mode_name,
        "bootstrap_rows": bootstrap_rows,
        "trade_count": int(summary.get("trade_count", 0)),
        "profit_factor": summary.get("profit_factor"),
        "alpha_vs_hodl": round(float(summary.get("alpha_vs_hodl", 0.0)), 6),
        "max_drawdown": round(float(summary.get("max_drawdown", 0.0)), 6),
        "win_rate": round(float(summary.get("win_rate", 0.0)), 6),
        "final_pnl": round(float(summary.get("final_pnl", 0.0)), 6),
        "market_regime": summary.get("market_regime", ""),
        "upper_tf_bias": summary.get("upper_tf_bias", ""),
    }


def write_summary(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
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
        raise ValueError("No price rows found for benchmark.")

    output_path = resolve_output_path(args.output)
    cleanup_result = cleanup_logs_for_run([output_path])
    log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

    log(f"Multi-timeframe benchmark input | {csv_path} | rows {len(rows)} | cycle {args.cycle_seconds:.0f}s | seed {args.seed}")
    result_rows = [run_mode(rows, cycle_seconds=args.cycle_seconds, seed=args.seed, mode_name=mode_name) for mode_name in MODE_CONFIGS]
    write_summary(result_rows, output_path)

    for row in result_rows:
        log(
            f"{row['mode']} | trades {row['trade_count']} | pf {row['profit_factor']} | "
            f"alpha {float(row['alpha_vs_hodl']):.2f} | dd {float(row['max_drawdown']):.2f} | "
            f"win_rate {float(row['win_rate']):.2f}%"
        )
    log(f"Multi-timeframe benchmark CSV: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
