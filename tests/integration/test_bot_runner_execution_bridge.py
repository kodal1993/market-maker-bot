from __future__ import annotations

import csv
import json
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import shutil
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, process_price_tick, trade_log_headers, equity_log_headers
from csv_logger import CsvLogger
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


class BotRunnerExecutionBridgeTests(unittest.TestCase):
    def test_process_price_tick_logs_execution_analytics(self) -> None:
        random.seed(0)
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

        temp_dir = TMP_ROOT / "bridge_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        trade_path = temp_dir / "trades.csv"
        equity_path = temp_dir / "equity.csv"
        trade_logger = CsvLogger(str(trade_path), trade_log_headers())
        equity_logger = CsvLogger(str(equity_path), equity_log_headers())

        process_price_tick(
            runtime=runtime,
            cycle_index=30,
            mid=100.0,
            source="test",
            trade_logger=trade_logger,
            equity_logger=equity_logger,
            log_progress=False,
        )

        with trade_path.open("r", encoding="utf-8", newline="") as trade_file:
            trade_rows = list(csv.reader(trade_file))
        with equity_path.open("r", encoding="utf-8", newline="") as equity_file:
            equity_rows = list(csv.reader(equity_file))
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertGreaterEqual(len(trade_rows), 2)
        self.assertGreaterEqual(len(equity_rows), 2)
        self.assertIn("execution_mode", trade_rows[0])
        self.assertIn("entry_price", trade_rows[0])
        self.assertIn("exit_price", trade_rows[0])
        self.assertIn("max_profit_during_trade", trade_rows[0])
        self.assertIn("mev_risk_score", equity_rows[0])
        filter_values_index = equity_rows[0].index("filter_values")
        entry_price_index = trade_rows[0].index("entry_price")
        exit_price_index = trade_rows[0].index("exit_price")
        max_profit_index = trade_rows[0].index("max_profit_during_trade")
        self.assertIn("decision_reason", equity_rows[1][filter_values_index])
        self.assertGreater(float(trade_rows[1][entry_price_index]), 0.0)
        self.assertEqual(trade_rows[1][exit_price_index], "")
        self.assertEqual(float(trade_rows[1][max_profit_index]), 0.0)
        self.assertTrue(runtime.last_execution_analytics.execution_mode)
        self.assertGreaterEqual(runtime.engine.trade_count, 1)

    def test_process_price_tick_logs_skipped_trade_reason_for_gas_spike(self) -> None:
        random.seed(0)
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

        with patch("runtime_execution.estimate_execution_gas_gwei", return_value=50.0):
            process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=None,
                equity_logger=None,
                log_progress=False,
            )

        self.assertEqual(runtime.last_execution_analytics.execution_mode, "skip")
        self.assertEqual(runtime.last_decision_block_reason, "gas_spike_skip")
        filter_values = json.loads(runtime.last_filter_values)
        self.assertEqual(filter_values["trade_blocked_reason"], "gas_spike_skip")
        self.assertEqual(filter_values["gas_price_gwei"], 50.0)
        self.assertEqual(runtime.engine.trade_count, 0)


if __name__ == "__main__":
    unittest.main()
