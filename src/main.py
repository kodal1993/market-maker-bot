import asyncio
import time
from itertools import count
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
from config import (
    ACTIVITY_DAILY_GOOD_TRADE_TARGET,
    ACTIVITY_DAILY_MIN_TRADE_TARGET,
    BOT_CONFIG_PROFILE,
    MAX_TRADES_PER_DAY,
    LOOP_SECONDS,
    MAX_LOOPS,
    TRADES_CSV,
    EQUITY_CSV,
    PRICE_BOOTSTRAP_ROWS,
    PRICE_HISTORY_MAX_AGE_SECONDS,
    RUNTIME_STATE_ENABLED,
    RUNTIME_STATE_PATH,
    SQLITE_LOG_PATH,
)
from csv_logger import CsvLogger
from dex.pool_monitor import PoolMonitor
from dex_client import DexClient
from execution.dex_executor import DexExecutor
from logger import close_log_sinks, log, register_log_sink
from log_cleanup import cleanup_logs_for_run, format_cleanup_result
from multi_timeframe import required_bootstrap_price_rows
from performance import build_report, write_report_csv, write_report_json
from price_history import load_bootstrap_prices
from sqlite_logger import SqliteLogger
from startup_validation import validate_startup_config
from telegram_notifier import TelegramNotifier
from state_persistence import apply_state, dump_state, load_state


def resolve_report_paths(trades_csv: str) -> tuple[Path, Path]:
    trade_path = Path(trades_csv)
    stem = trade_path.stem
    if stem.endswith("_trades"):
        stem = stem[: -len("_trades")]
    return (
        trade_path.with_name(f"{stem}_report.json"),
        trade_path.with_name(f"{stem}_report.csv"),
    )


def cycle_indices(max_loops: int):
    if max_loops > 0:
        return range(max_loops)
    return count()


async def get_uniswap_price_and_info(pool_monitor: PoolMonitor):
    return await pool_monitor.get_pool_info()


def main():
    close_log_sinks()
    register_log_sink(SqliteLogger(SQLITE_LOG_PATH))
    try:
        log("main() started")
        notifier = TelegramNotifier()

        startup_errors = validate_startup_config()
        if startup_errors:
            log("ERROR: startup config validation failed")
            for error in startup_errors:
                log(f"CONFIG ERROR | {error}")
            try:
                notifier.notify_error("startup", "; ".join(startup_errors[:3]))
            except Exception as exc:  # noqa: BLE001 - notifications must not break shutdown
                log(f"Telegram startup notify failed: {exc}")
            return

        trades_path = Path(TRADES_CSV)
        equity_path = Path(EQUITY_CSV)
        report_json_path, report_csv_path = resolve_report_paths(TRADES_CSV)
        cleanup_result = cleanup_logs_for_run(
            [trades_path, equity_path, report_json_path, report_csv_path],
        )
        log(f"Log cleanup | {format_cleanup_result(cleanup_result)}")

        trade_logger = CsvLogger(
            str(trades_path),
            trade_log_headers(),
        )
        equity_logger = CsvLogger(
            str(equity_path),
            equity_log_headers(),
        )

        bootstrap_rows = required_bootstrap_price_rows(
            cycle_seconds=LOOP_SECONDS,
            configured_rows=PRICE_BOOTSTRAP_ROWS,
        )
        bootstrap_prices = load_bootstrap_prices(
            equity_csv_path=EQUITY_CSV,
            max_rows=bootstrap_rows,
            max_age_seconds=PRICE_HISTORY_MAX_AGE_SECONDS,
        )
        if bootstrap_prices:
            log(
                f"bootstrap price history loaded | rows {len(bootstrap_prices)} / requested {bootstrap_rows} | "
                f"last {bootstrap_prices[-1]:.2f}"
            )

        runtime = None
        persisted_state = load_state(RUNTIME_STATE_PATH) if RUNTIME_STATE_ENABLED else {}
        dex = DexClient()
        pool_monitor = None
        dex_executor = None
        try:
            pool_monitor = PoolMonitor()
            dex_executor = DexExecutor()
            log("Uniswap V3 modules initialized for paper mode integration")
        except Exception as exc:
            log(f"Uniswap V3 modules unavailable, falling back to DexClient price feed: {exc}")
        manual_stop_requested = False
        log(
            "Trade activity config | "
            f"max_trades_per_day {MAX_TRADES_PER_DAY} | "
            f"daily_min_trade_target {ACTIVITY_DAILY_MIN_TRADE_TARGET} | "
            f"daily_good_trade_target {ACTIVITY_DAILY_GOOD_TRADE_TARGET}"
        )
        if MAX_LOOPS > 0:
            log(f"Loop mode | max_loops {MAX_LOOPS}")
        else:
            log("Loop mode | continuous until manual stop")

        for cycle_index in cycle_indices(MAX_LOOPS):
            try:
                try:
                    notifier.handle_commands(runtime, build_summary)
                except Exception as exc:  # noqa: BLE001 - notifications must not break the bot
                    log(f"Telegram command handling failed: {exc}")

                current_price = None
                if pool_monitor is not None:
                    pool_info = asyncio.run(get_uniswap_price_and_info(pool_monitor))
                    if pool_info and pool_info.get("price") is not None:
                        current_price = float(pool_info["price"])
                        log(
                            f"[Uniswap V3] ETH/USDC Price: {current_price:.4f} | "
                            f"Liquidity: {float(pool_info.get('liquidity', 0) or 0):.2f} | "
                            f"Volatility: {float(pool_info.get('volatility', 0) or 0):.2f}%"
                        )
                        mid = current_price
                        source = "uniswap_v3_pool_monitor"
                    else:
                        mid, source = dex.get_price()
                else:
                    mid, source = dex.get_price()
                if runtime is None:
                    start_usdc, start_eth = resolve_start_balances(reference_price=mid)
                    runtime = create_runtime(
                        bootstrap_prices=bootstrap_prices,
                        reference_price=mid,
                        cycle_seconds=LOOP_SECONDS,
                        telegram_notifier=notifier,
                    )
                    if persisted_state:
                        apply_state(runtime, persisted_state)
                        portfolio_state = persisted_state.get("portfolio", {})
                        if isinstance(portfolio_state, dict):
                            runtime.portfolio.usdc = float(portfolio_state.get("usdc", runtime.portfolio.usdc))
                            runtime.portfolio.eth = float(portfolio_state.get("eth", runtime.portfolio.eth))
                            runtime.portfolio.fees_paid_usd = float(
                                portfolio_state.get("fees_paid_usd", runtime.portfolio.fees_paid_usd)
                            )
                            runtime.portfolio.realized_pnl_usd = float(
                                portfolio_state.get("realized_pnl_usd", runtime.portfolio.realized_pnl_usd)
                            )
                            eth_cost_basis = portfolio_state.get("eth_cost_basis")
                            runtime.portfolio.eth_cost_basis = None if eth_cost_basis is None else float(eth_cost_basis)
                        log(f"Runtime state restored from {RUNTIME_STATE_PATH}")
                    log(
                        f"start portfolio resolved | ref {mid:.2f} | usdc {start_usdc:.2f} | "
                        f"eth {start_eth:.8f}"
                    )
                should_continue = process_price_tick(
                    runtime=runtime,
                    cycle_index=cycle_index,
                    mid=mid,
                    source=source,
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=True,
                )
                try:
                    notifier.maybe_send_daily_report(runtime, build_summary)
                except Exception as exc:  # noqa: BLE001 - notifications must not break the bot
                    log(f"Telegram daily report failed: {exc}")
                if not should_continue:
                    break
                if RUNTIME_STATE_ENABLED:
                    dump_state(runtime, path=RUNTIME_STATE_PATH)
                time.sleep(LOOP_SECONDS)
            except KeyboardInterrupt:
                manual_stop_requested = True
                log("Manual stop requested | graceful shutdown")
                break
            except Exception as exc:
                log(f"loop error: {exc}")
                try:
                    notifier.notify_error("main_loop", exc)
                except Exception as notify_exc:  # noqa: BLE001 - notifications must not break the bot
                    log(f"Telegram error notify failed: {notify_exc}")
                try:
                    notifier.handle_commands(runtime, build_summary)
                except Exception as command_exc:  # noqa: BLE001 - notifications must not break the bot
                    log(f"Telegram command handling failed during recovery: {command_exc}")
                time.sleep(LOOP_SECONDS)

        if runtime is None:
            log("No runtime initialized.")
            return

        summary = build_summary(runtime)
        if RUNTIME_STATE_ENABLED:
            dump_state(runtime, path=RUNTIME_STATE_PATH)
        log_summary(summary)
        report = build_report(
            summary,
            run_label=Path(TRADES_CSV).stem,
            variant=f"paper_live_{BOT_CONFIG_PROFILE}",
            trade_history_path=str(trades_path),
            equity_curve_path=str(equity_path),
        )
        write_report_json(report, report_json_path)
        write_report_csv(report, report_csv_path)
        log(f"Paper report JSON: {report_json_path}")
        log(f"Paper report CSV: {report_csv_path}")
        if manual_stop_requested:
            log("Bot manually stopped.")
    finally:
        close_log_sinks()


if __name__ == "__main__":
    main()
