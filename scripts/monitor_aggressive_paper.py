#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


try:
    from rich.console import Console
    from rich.table import Table

    _RICH = True
except Exception:
    _RICH = False


@dataclass
class MonitorConfig:
    sqlite_path: Path
    equity_path: Path
    state_path: Path
    env_profile: Path
    interval_seconds: int
    top_skips: int


def _parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _from_profile(profile_path: Path, interval_seconds: int, top_skips: int) -> MonitorConfig:
    env = _parse_env_file(profile_path)
    sqlite_path = Path(env.get("SQLITE_LOG_PATH", "logs/trading.sqlite"))
    equity_path = Path(env.get("EQUITY_CSV", "logs/equity.csv"))
    state_path = Path(env.get("RUNTIME_STATE_PATH", "logs/runtime_state.json"))
    return MonitorConfig(sqlite_path, equity_path, state_path, profile_path, interval_seconds, top_skips)


def _query_trade_counts(sqlite_path: Path, now_utc: datetime) -> tuple[int, int, int]:
    if not sqlite_path.exists():
        return 0, 0, 0
    conn = sqlite3.connect(sqlite_path)
    try:
        hour_since = (now_utc - timedelta(hours=1)).isoformat(timespec="seconds")
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        this_hour = int(conn.execute("SELECT COUNT(*) FROM trades WHERE mode='paper' AND created_at >= ?", (hour_since,)).fetchone()[0])
        today = int(conn.execute("SELECT COUNT(*) FROM trades WHERE mode='paper' AND created_at >= ?", (day_start,)).fetchone()[0])
        total = int(conn.execute("SELECT COUNT(*) FROM trades WHERE mode='paper'").fetchone()[0])
        return this_hour, today, total
    finally:
        conn.close()


def _read_equity_rows(equity_path: Path) -> list[dict[str, str]]:
    if not equity_path.exists():
        return []
    with equity_path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _skips(rows: list[dict[str, str]], top_n: int) -> tuple[int, list[tuple[str, int]]]:
    c = Counter()
    for row in rows:
        reason = (row.get("trade_blocked_reason") or "").strip()
        if reason and reason not in {"-", "none", "n/a"}:
            c[reason] += 1
    return sum(c.values()), c.most_common(top_n)


def _expected_profit(rows: list[dict[str, str]]) -> float:
    total = 0.0
    attempts = 0
    for row in rows:
        try:
            edge = float(row.get("expected_edge_bps") or 0.0)
            size = float(row.get("trade_size_usd") or 0.0)
        except ValueError:
            continue
        if size > 0:
            attempts += 1
            total += max(size * edge / 10000.0, 0.0)
    return (total / attempts) if attempts else 0.0


def _state_info(rows: list[dict[str, str]], state_path: Path) -> tuple[str, str, str, str]:
    inventory_skew = "n/a"
    regime = "n/a"
    pool_price = "n/a"
    volatility = "n/a"
    if rows:
        last = rows[-1]
        inventory_skew = f"{(last.get('inventory_ratio') or 'n/a')}"
        regime = (last.get("strategy_mode") or last.get("active_regime") or "n/a")
        pool_price = (last.get("mid") or "n/a")
        volatility = (last.get("volatility") or "n/a")

    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if regime == "n/a":
                regime = str(payload.get("current_strategy_mode") or payload.get("current_market_mode") or regime)
        except Exception:
            pass

    return inventory_skew, regime, pool_price, volatility


def _print_snapshot(cfg: MonitorConfig) -> None:
    now_utc = datetime.now(timezone.utc)
    hour_trades, today_trades, total_trades = _query_trade_counts(cfg.sqlite_path, now_utc)
    rows = _read_equity_rows(cfg.equity_path)
    skip_count, top_skip_reasons = _skips(rows, cfg.top_skips)
    avg_expected_profit = _expected_profit(rows)
    inventory_skew, regime, pool_price, volatility = _state_info(rows, cfg.state_path)

    if _RICH:
        console = Console()
        table = Table(title="Aggressive Paper Monitor (Uniswap V3)")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Timestamp (UTC)", now_utc.isoformat(timespec="seconds"))
        table.add_row("Profile", str(cfg.env_profile))
        table.add_row("Trades this hour / today", f"{hour_trades} / {today_trades}")
        table.add_row("Skipped trades", str(skip_count))
        table.add_row("Top skip reasons", ", ".join(f"{k}:{v}" for k, v in top_skip_reasons) or "n/a")
        table.add_row("Avg expected profit / attempt", f"{avg_expected_profit:.6f} USD")
        table.add_row("Current inventory skew", inventory_skew)
        table.add_row("Current regime", regime)
        table.add_row("Uniswap V3 pool price", str(pool_price))
        table.add_row("Volatility", str(volatility))
        table.add_row("Total trades since start", str(total_trades))
        console.clear()
        console.print(table)
    else:
        print("=== Aggressive Paper Monitor (Uniswap V3) ===")
        print(f"Timestamp (UTC): {now_utc.isoformat(timespec='seconds')}")
        print(f"Profile: {cfg.env_profile}")
        print(f"Trades this hour / today: {hour_trades} / {today_trades}")
        print(f"Skipped trades: {skip_count}")
        print("Top skip reasons:", ", ".join(f"{k}:{v}" for k, v in top_skip_reasons) or "n/a")
        print(f"Avg expected profit / attempt: {avg_expected_profit:.6f} USD")
        print(f"Current inventory skew: {inventory_skew}")
        print(f"Current regime: {regime}")
        print(f"Uniswap V3 pool price: {pool_price}")
        print(f"Volatility: {volatility}")
        print(f"Total trades since start: {total_trades}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor aggressive paper profile trade frequency")
    parser.add_argument("--profile", default="profiles/aggressive_base_paper.env", help="Path to env profile")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds")
    parser.add_argument("--top-skips", type=int, default=5, help="Top N skip reasons")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    profile_path = Path(args.profile)
    cfg = _from_profile(profile_path, args.interval, args.top_skips)
    os.environ["BOT_CONFIG_PROFILE"] = _parse_env_file(profile_path).get("BOT_CONFIG_PROFILE", os.environ.get("BOT_CONFIG_PROFILE", ""))

    if args.once:
        _print_snapshot(cfg)
        return

    while True:
        _print_snapshot(cfg)
        time.sleep(max(cfg.interval_seconds, 5))


if __name__ == "__main__":
    main()

# Quick run guide:
# 1) Start bot with aggressive profile:
#    set -a && source profiles/aggressive_base_paper.env && set +a && python src/main.py
# 2) In another terminal run monitor:
#    python scripts/monitor_aggressive_paper.py --profile profiles/aggressive_base_paper.env --interval 60
# 3) One-shot snapshot:
#    python scripts/monitor_aggressive_paper.py --once
