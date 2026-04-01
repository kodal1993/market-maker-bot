from __future__ import annotations

import random
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, process_price_tick
from logger import clear_log_sinks, close_log_sinks, log, register_log_sink
from sqlite_logger import SqliteLogger
from types_bot import DecisionOutcome

TEST_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = TEST_ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


def build_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        regime="TREND",
        volatility_state="NORMAL",
        feed_state="NORMAL",
        mode="TREND_UP",
        short_ma=100.0,
        long_ma=99.8,
        volatility=0.0012,
        spread_multiplier=1.0,
        signal_score=0.25,
        feed_score=0.1,
        risk_score=0.05,
        news_score=0.0,
        macro_score=0.0,
        onchain_score=0.0,
        adaptive_score=0.0,
        confidence=0.8,
        buy_enabled=True,
        sell_enabled=True,
        max_inventory_multiplier=5.0,
        target_inventory_pct=0.7,
        trade_size_multiplier=1.0,
        market_score=0.35,
        trend_strength=1.15,
        inventory_skew_multiplier=1.0,
        directional_bias=0.0,
        max_chase_bps_multiplier=1.0,
    )


class SqliteTradeLoggingTests(unittest.TestCase):
    def test_process_price_tick_persists_events_and_trade_rows(self) -> None:
        random.seed(0)
        temp_dir = TMP_ROOT / "sqlite_logging_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        db_path = temp_dir / "trading.sqlite"

        clear_log_sinks()
        register_log_sink(SqliteLogger(db_path))
        try:
            log("sqlite smoke event")
            runtime = create_runtime(
                bootstrap_prices=[100.0] * 30,
                reference_price=100.0,
                start_eth_usd=0.0,
                enable_trade_filter=False,
                enable_execution_engine=True,
            )
            runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
            runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
                action="BUY",
                size_usd=30.0,
                reason="trend_buy",
                source="test",
                order_price=100.0,
                inventory_cap_usd=5_000.0,
                allow_trade=True,
                filter_values={},
            )

            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )
        finally:
            close_log_sinks()

        with sqlite3.connect(db_path) as conn:
            event_row = conn.execute(
                "SELECT event_type, message FROM events WHERE message = ?",
                ("sqlite smoke event",),
            ).fetchone()
            trade_row = conn.execute(
                """
                SELECT pair, side, size_usd, price, pnl_usd, gas_gwei, tx_hash,
                       entry_price, exit_price, max_profit_during_trade
                FROM trades
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()

        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(event_row, ("log", "sqlite smoke event"))
        self.assertIsNotNone(trade_row)
        self.assertEqual(trade_row[0], "WETH/USDC")
        self.assertEqual(trade_row[1], "buy")
        self.assertGreater(trade_row[2], 0.0)
        self.assertGreater(trade_row[3], 0.0)
        self.assertGreaterEqual(trade_row[5], 0.0)
        self.assertGreater(trade_row[7], 0.0)
        self.assertGreaterEqual(trade_row[8], 0.0)
        self.assertGreaterEqual(trade_row[9], 0.0)


if __name__ == "__main__":
    unittest.main()
