#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class WindowStat:
    hours: int
    trade_count: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper trade activity monitor")
    parser.add_argument("--sqlite", default="logs/trading.sqlite", help="SQLite trade log path")
    parser.add_argument("--equity", default="logs/equity.csv", help="Equity CSV path")
    parser.add_argument("--state", default="logs/runtime_state.json", help="Runtime state JSON path")
    parser.add_argument("--top-skips", type=int, default=5, help="How many skip reasons to display")
    return parser.parse_args()


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _window_trade_counts(sqlite_path: Path, now_utc: datetime) -> list[WindowStat]:
    windows = [1, 4, 24]
    if not sqlite_path.exists():
        return [WindowStat(hours=w, trade_count=0) for w in windows]

    conn = sqlite3.connect(sqlite_path)
    try:
        stats: list[WindowStat] = []
        for hours in windows:
            since = (now_utc - timedelta(hours=hours)).isoformat(timespec="seconds")
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE mode = 'paper' AND created_at >= ?",
                (since,),
            ).fetchone()[0]
            stats.append(WindowStat(hours=hours, trade_count=int(count)))
        return stats
    finally:
        conn.close()


def _avg_profit_per_trade(sqlite_path: Path) -> float:
    if not sqlite_path.exists():
        return 0.0
    conn = sqlite3.connect(sqlite_path)
    try:
        row = conn.execute(
            "SELECT AVG(pnl_usd) FROM trades WHERE mode = 'paper'"
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


def _read_equity_rows(equity_path: Path) -> list[dict[str, str]]:
    if not equity_path.exists():
        return []
    with equity_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _top_skip_reasons(rows: list[dict[str, str]], top_n: int) -> list[tuple[str, int]]:
    reasons = Counter()
    for row in rows:
        reason = (row.get("trade_blocked_reason") or "").strip()
        if reason and reason not in {"-", "none", "n/a"}:
            reasons[reason] += 1
    return reasons.most_common(top_n)


def _current_inventory_and_regime(rows: list[dict[str, str]], state_path: Path) -> tuple[str, str]:
    inventory = "n/a"
    regime = "n/a"

    if rows:
        last = rows[-1]
        inventory_ratio = (last.get("inventory_ratio") or "").strip()
        inventory_usd = (last.get("inventory_usd") or "").strip()
        if inventory_ratio or inventory_usd:
            inventory = f"ratio={inventory_ratio or '?'} | usd={inventory_usd or '?'}"
        regime = (last.get("active_regime") or last.get("regime") or "").strip() or regime

    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if inventory == "n/a":
                portfolio = payload.get("portfolio") if isinstance(payload, dict) else None
                if isinstance(portfolio, dict):
                    usdc = portfolio.get("usdc")
                    eth = portfolio.get("eth")
                    inventory = f"portfolio_usdc={usdc} | portfolio_eth={eth}"
            if regime == "n/a" and isinstance(payload, dict):
                regime = str(payload.get("current_active_regime") or payload.get("current_market_mode") or "n/a")
        except (OSError, json.JSONDecodeError):
            pass

    return inventory, regime


def main() -> None:
    args = _parse_args()
    now_utc = datetime.now(timezone.utc)

    sqlite_path = Path(args.sqlite)
    equity_path = Path(args.equity)
    state_path = Path(args.state)

    window_stats = _window_trade_counts(sqlite_path, now_utc)
    avg_profit = _avg_profit_per_trade(sqlite_path)
    equity_rows = _read_equity_rows(equity_path)
    top_skips = _top_skip_reasons(equity_rows, args.top_skips)
    inventory, regime = _current_inventory_and_regime(equity_rows, state_path)

    print("=== Paper trade monitor ===")
    print(f"Timestamp (UTC): {now_utc.isoformat(timespec='seconds')}")
    print("\nTrade count windows:")
    for stat in window_stats:
        print(f"- last {stat.hours:>2}h: {stat.trade_count}")

    print(f"\nAverage profit / trade (paper, all-time): {avg_profit:.6f} USD")

    print("\nTop skip reasons (from equity trade_blocked_reason):")
    if top_skips:
        for reason, count in top_skips:
            print(f"- {reason}: {count}")
    else:
        print("- no skip reasons found")

    print("\nCurrent state:")
    print(f"- inventory: {inventory}")
    print(f"- regime: {regime}")


if __name__ == "__main__":
    main()
