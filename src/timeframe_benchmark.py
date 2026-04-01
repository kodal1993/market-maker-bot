import argparse
import csv
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backtest import load_price_rows
from bot_runner import build_summary, create_runtime, process_price_tick, resolve_start_balances
from download_coinbase_history import (
    default_output_path,
    download_candles,
    parse_utc_datetime,
    to_iso8601_z,
    write_csv,
)
from logger import log
from log_cleanup import cleanup_logs_for_run, format_cleanup_result


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark the strategy across multiple candle granularities.")
    parser.add_argument("--product", default="ETH-USD", help="Coinbase product id, for example ETH-USD.")
    parser.add_argument(
        "--granularities",
        default="60,300,900,3600",
        help="Comma-separated Coinbase candle granularities in seconds.",
    )
    parser.add_argument(
        "--seeds",
        default="41,42,43,44,45",
        help="Comma-separated random seeds used to average the stochastic fill model.",
    )
    parser.add_argument("--days", type=int, default=30, help="Trailing days to benchmark when start/end are not provided.")
    parser.add_argument("--start", default="", help="Optional UTC start time in ISO-8601.")
    parser.add_argument("--end", default="", help="Optional UTC end time in ISO-8601.")
    parser.add_argument("--pause-ms", type=int, default=200, help="Pause between Coinbase requests in milliseconds.")
    parser.add_argument(
        "--summary-output",
        default="",
        help="Optional output CSV for the benchmark summary.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download historical CSVs even if they already exist locally.",
    )
    return parser.parse_args()


def parse_int_list(value: str) -> list[int]:
    items = []
    for part in value.split(","):
        stripped = part.strip()
        if stripped:
            items.append(int(stripped))
    if not items:
        raise ValueError("Expected at least one integer value.")
    return items


def resolve_range(start_arg: str, end_arg: str, days: int) -> tuple[datetime, datetime]:
    if start_arg and end_arg:
        start_dt = parse_utc_datetime(start_arg)
        end_dt = parse_utc_datetime(end_arg)
    else:
        if days <= 0:
            raise ValueError("--days must be greater than zero when --start/--end are not provided.")
        end_dt = datetime.now(tz=UTC)
        start_dt = end_dt - timedelta(days=days)

    if end_dt <= start_dt:
        raise ValueError("end time must be later than start time")
    return start_dt, end_dt


def ensure_history_file(
    product: str,
    granularity: int,
    start_dt: datetime,
    end_dt: datetime,
    pause_ms: int,
    refresh: bool,
) -> Path:
    output_path = default_output_path(product, granularity, start_dt, end_dt)
    if output_path.exists() and not refresh:
        log(f"Using cached history | {granularity}s | {output_path}")
        return output_path

    rows = download_candles(
        product=product,
        start_dt=start_dt,
        end_dt=end_dt,
        granularity=granularity,
        pause_ms=pause_ms,
    )
    if not rows:
        raise ValueError(f"No candles returned for {product} at {granularity}s")

    write_csv(rows, output_path, product, granularity)
    log(f"Downloaded history | {granularity}s | rows {len(rows)} | {output_path}")
    return output_path


def run_backtest_once(rows: list[tuple[float, str]], seed: int, cycle_seconds: float) -> dict:
    random.seed(seed)
    runtime = create_runtime(reference_price=rows[0][0], cycle_seconds=cycle_seconds)

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


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def average_mode_count(summaries: list[dict], mode: str) -> float:
    return average([summary["mode_counts"].get(mode, 0) for summary in summaries])


def build_summary_row(granularity: int, candle_count: int, summaries: list[dict]) -> dict:
    avg_start_equity = average(
        [
            summary["final_equity"] - summary["final_pnl"]
            for summary in summaries
        ]
    )
    avg_final_pnl = average([summary["final_pnl"] for summary in summaries])
    return {
        "granularity_seconds": granularity,
        "candles": candle_count,
        "avg_start_equity": round(avg_start_equity, 6),
        "avg_final_pnl": round(avg_final_pnl, 6),
        "avg_return_pct": round((avg_final_pnl / avg_start_equity) * 100.0, 6) if avg_start_equity else 0.0,
        "avg_realized_pnl": round(average([summary["realized_pnl_usd"] for summary in summaries]), 6),
        "avg_trade_count": round(average([summary["trade_count"] for summary in summaries]), 6),
        "avg_pnl_per_trade": round(average([summary["pnl_per_trade"] for summary in summaries]), 6),
        "avg_buy_count": round(average([summary["buy_count"] for summary in summaries]), 6),
        "avg_sell_count": round(average([summary["sell_count"] for summary in summaries]), 6),
        "avg_max_pnl": round(average([summary["max_pnl"] for summary in summaries]), 6),
        "avg_min_pnl": round(average([summary["min_pnl"] for summary in summaries]), 6),
        "avg_max_drawdown_usd": round(average([summary["max_drawdown_usd"] for summary in summaries]), 6),
        "avg_inventory_min": round(average([summary["inventory_min"] for summary in summaries]), 6),
        "avg_inventory_max": round(average([summary["inventory_max"] for summary in summaries]), 6),
        "avg_no_trade_cycles": round(average_mode_count(summaries, "NO_TRADE"), 6),
        "avg_no_trade_ratio": round(average([summary["no_trade_ratio"] for summary in summaries]), 6),
        "avg_range_maker_cycles": round(average_mode_count(summaries, "RANGE_MAKER"), 6),
        "avg_trend_up_cycles": round(average_mode_count(summaries, "TREND_UP"), 6),
        "avg_overweight_exit_cycles": round(average_mode_count(summaries, "OVERWEIGHT_EXIT"), 6),
        "avg_final_usdc": round(average([summary["final_usdc"] for summary in summaries]), 6),
        "avg_final_eth": round(average([summary["final_eth"] for summary in summaries]), 8),
        "best_final_pnl": round(max(summary["final_pnl"] for summary in summaries), 6),
        "worst_final_pnl": round(min(summary["final_pnl"] for summary in summaries), 6),
    }


def write_summary_csv(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    granularities = parse_int_list(args.granularities)
    seeds = parse_int_list(args.seeds)
    start_dt, end_dt = resolve_range(args.start, args.end, args.days)
    summary_output = (
        Path(args.summary_output)
        if args.summary_output
        else Path("logs") / "backtests" / f"timeframe_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    cleanup_result = cleanup_logs_for_run([summary_output])
    log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

    log(f"Benchmark product: {args.product}")
    log(f"Benchmark range: {to_iso8601_z(start_dt)} -> {to_iso8601_z(end_dt)}")
    log(f"Granularities: {granularities}")
    log(f"Seeds: {seeds}")

    summary_rows = []
    for granularity in granularities:
        history_path = ensure_history_file(
            product=args.product,
            granularity=granularity,
            start_dt=start_dt,
            end_dt=end_dt,
            pause_ms=args.pause_ms,
            refresh=args.refresh,
        )
        rows = load_price_rows(
            csv_path=history_path,
            price_column="close",
            source_column="source",
            limit=0,
        )
        reference_price = rows[0][0]
        start_usdc, start_eth = resolve_start_balances(reference_price=reference_price)
        start_equity = start_usdc + (start_eth * reference_price)
        log(
            f"{granularity}s start portfolio | ref {reference_price:.2f} | "
            f"usdc {start_usdc:.2f} | eth {start_eth:.8f} | equity {start_equity:.2f}"
        )
        summaries = [run_backtest_once(rows, seed, granularity) for seed in seeds]
        row = build_summary_row(granularity, len(rows), summaries)
        summary_rows.append(row)
        log(
            f"{granularity}s | candles {row['candles']} | avg final pnl {row['avg_final_pnl']:.2f} | "
            f"avg realized pnl {row['avg_realized_pnl']:.2f} | avg max drawdown {row['avg_max_drawdown_usd']:.2f} | "
            f"avg trades {row['avg_trade_count']:.1f}"
        )

    summary_rows.sort(key=lambda item: item["avg_final_pnl"], reverse=True)

    write_summary_csv(summary_rows, summary_output)

    log("========================================")
    for row in summary_rows:
        log(
            f"Rank | {row['granularity_seconds']}s | avg pnl {row['avg_final_pnl']:.2f} | "
            f"return {row['avg_return_pct']:.2f}% | dd {row['avg_max_drawdown_usd']:.2f} | "
            f"trades {row['avg_trade_count']:.1f}"
        )
    log(f"Benchmark summary CSV: {summary_output}")


if __name__ == "__main__":
    main()
