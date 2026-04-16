from __future__ import annotations

import json
import random
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, equity_log_headers, process_price_tick, trade_log_headers
from csv_logger import CsvLogger

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
        blockers=[],
    )


class BotRunnerAntiOvertradingTests(unittest.TestCase):
    def _make_loggers(self, name: str) -> tuple[CsvLogger, CsvLogger, Path]:
        temp_dir = TMP_ROOT / name
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        trade_path = temp_dir / "trades.csv"
        equity_path = temp_dir / "equity.csv"
        return (
            CsvLogger(str(trade_path), trade_log_headers()),
            CsvLogger(str(equity_path), equity_log_headers()),
            temp_dir,
        )

    def test_process_price_tick_softens_when_min_gap_not_met(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_execution_engine=False,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.last_trade_cycle_any = 29
        runtime.last_trade_price_any = 100.0
        runtime.daily_trade_count = 1
        runtime.daily_reset_date = date.today().isoformat()
        runtime.daily_start_equity = runtime.portfolio.total_equity_usd(100.0)
        trade_logger, equity_logger, temp_dir = self._make_loggers("anti_overtrading_gap_case")

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 10.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 20),
            patch("runtime_risk.MAX_TRADES_PER_DAY", 20),
            patch("bot_runner.should_place_trend_buy", return_value=True),
        ):
            self.assertTrue(
                process_price_tick(
                    runtime=runtime,
                    cycle_index=30,
                    mid=100.0,
                    source="test",
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=False,
                )
            )

        shutil.rmtree(temp_dir, ignore_errors=True)
        filter_values = json.loads(runtime.last_filter_values)

        self.assertEqual(runtime.engine.trade_count, 1)
        self.assertEqual(runtime.daily_trade_count, 2)
        self.assertTrue(runtime.last_allow_trade)
        self.assertEqual(runtime.last_decision_block_reason, "")
        self.assertIn("min_time_between_trades_soft", filter_values["adjustment_reasons"])
        self.assertGreater(filter_values["remaining_trade_gap_minutes"], 0.0)
        self.assertTrue(filter_values["size_clamped_to_min"])

    def test_process_price_tick_softens_after_daily_trade_cap(self) -> None:
        random.seed(0)
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_execution_engine=False,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.last_trade_cycle_any = 1
        runtime.last_trade_price_any = 100.0
        runtime.daily_trade_count = 1
        runtime.daily_reset_date = date.today().isoformat()
        runtime.daily_start_equity = runtime.portfolio.total_equity_usd(100.0)
        trade_logger, equity_logger, temp_dir = self._make_loggers("anti_overtrading_daily_cap_case")

        with (
            patch("trade_filter.MIN_TIME_BETWEEN_TRADES_MINUTES", 0.0),
            patch("trade_filter.MAX_TRADES_PER_DAY", 1),
            patch("runtime_risk.MAX_TRADES_PER_DAY", 1),
            patch("bot_runner.should_place_trend_buy", return_value=True),
        ):
            self.assertTrue(
                process_price_tick(
                    runtime=runtime,
                    cycle_index=30,
                    mid=100.0,
                    source="test",
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=False,
                )
            )

        shutil.rmtree(temp_dir, ignore_errors=True)
        filter_values = json.loads(runtime.last_filter_values)

        self.assertEqual(runtime.engine.trade_count, 1)
        self.assertEqual(runtime.daily_trade_count, 2)
        self.assertTrue(runtime.last_allow_trade)
        self.assertEqual(runtime.last_decision_block_reason, "")
        self.assertIn("max_trades_soft_limit", filter_values["adjustment_reasons"])
        self.assertTrue(filter_values["daily_trade_limit_hit"])
        self.assertTrue(filter_values["size_clamped_to_min"])


if __name__ == "__main__":
    unittest.main()
