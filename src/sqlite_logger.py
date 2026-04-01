from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock


def _as_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now()
    return current.isoformat(timespec="seconds")


def _as_json(value: object) -> str:
    if value is None or value == "":
        return ""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class SqliteLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size_usd REAL NOT NULL,
                    price REAL NOT NULL,
                    pnl_usd REAL NOT NULL,
                    gas_gwei REAL NOT NULL,
                    tx_hash TEXT NOT NULL DEFAULT '',
                    execution_mode TEXT NOT NULL DEFAULT '',
                    execution_type TEXT NOT NULL DEFAULT '',
                    trade_reason TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    fee_usd REAL NOT NULL DEFAULT 0,
                    slippage_bps REAL NOT NULL DEFAULT 0,
                    mev_risk_score REAL NOT NULL DEFAULT 0,
                    entry_price REAL NOT NULL DEFAULT 0,
                    exit_price REAL NOT NULL DEFAULT 0,
                    max_profit_during_trade REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._ensure_trade_column("entry_price", "REAL NOT NULL DEFAULT 0")
            self._ensure_trade_column("exit_price", "REAL NOT NULL DEFAULT 0")
            self._ensure_trade_column("max_profit_during_trade", "REAL NOT NULL DEFAULT 0")
            self._conn.commit()

    def _ensure_trade_column(self, column_name: str, column_type_sql: str) -> None:
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        if column_name in columns:
            return
        self._conn.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type_sql}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def log_event_message(self, created_at: datetime, message: str) -> None:
        self.log_event(
            event_type="log",
            message=message,
            created_at=_as_timestamp(created_at),
        )

    def log_event(
        self,
        *,
        event_type: str,
        message: str,
        created_at: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO events (created_at, event_type, message, context_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    created_at or _as_timestamp(),
                    event_type,
                    message,
                    _as_json(context),
                ),
            )
            self._conn.commit()

    def log_trade(
        self,
        *,
        pair: str,
        side: str,
        size_usd: float,
        price: float,
        pnl_usd: float,
        gas_gwei: float,
        tx_hash: str = "",
        execution_mode: str = "",
        execution_type: str = "",
        trade_reason: str = "",
        state: str = "",
        mode: str = "",
        fee_usd: float = 0.0,
        slippage_bps: float = 0.0,
        mev_risk_score: float = 0.0,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        max_profit_during_trade: float = 0.0,
        metadata: dict[str, object] | None = None,
        created_at: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trades (
                    created_at, pair, side, size_usd, price, pnl_usd, gas_gwei, tx_hash,
                    execution_mode, execution_type, trade_reason, state, mode, fee_usd,
                    slippage_bps, mev_risk_score, entry_price, exit_price,
                    max_profit_during_trade, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at or _as_timestamp(),
                    pair,
                    side,
                    float(size_usd),
                    float(price),
                    float(pnl_usd),
                    float(gas_gwei),
                    tx_hash or "",
                    execution_mode or "",
                    execution_type or "",
                    trade_reason or "",
                    state or "",
                    mode or "",
                    float(fee_usd),
                    float(slippage_bps),
                    float(mev_risk_score),
                    float(entry_price),
                    float(exit_price),
                    float(max_profit_during_trade),
                    _as_json(metadata),
                ),
            )
            self._conn.commit()
