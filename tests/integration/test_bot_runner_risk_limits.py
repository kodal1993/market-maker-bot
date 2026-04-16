from __future__ import annotations

import json
import random
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import create_runtime, equity_log_headers, process_price_tick, trade_log_headers
from csv_logger import CsvLogger
from types_bot import DecisionOutcome

TEST_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = TEST_ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


def build_snapshot(*, mode: str = "TREND_UP") -> SimpleNamespace:
    return SimpleNamespace(
        regime="TREND",
        volatility_state="NORMAL",
        feed_state="NORMAL",
        mode=mode,
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


class RecordingNotifier:
    def __init__(self) -> None:
        self.risk_alerts: list[dict[str, object]] = []

    def notify_trade(self, *, cycle_index: int, fill, runtime, mode: str) -> bool:
        return True

    def notify_risk_limit(self, *, reason: str, details: str, runtime=None) -> bool:
        self.risk_alerts.append(
            {
                "reason": reason,
                "details": details,
                "state": getattr(getattr(runtime, "state_context", None), "current_state", None),
            }
        )
        return True

    def notify_error(self, context_message: str, exc: Exception | str) -> bool:
        return True


class BotRunnerRiskLimitTests(unittest.TestCase):
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

    def test_trade_size_limit_reduces_instead_of_stopping_bot(self) -> None:
        random.seed(0)
        notifier = RecordingNotifier()
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            telegram_notifier=notifier,
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
        trade_logger, equity_logger, temp_dir = self._make_loggers("risk_limit_trade_size_case")

        with (
            patch("sizing_engine.ACCOUNT_SIZE_OVERRIDE", 250.0),
            patch("sizing_engine.MAX_TRADE_SIZE_PCT", 0.04),
            patch("runtime_risk.MAX_DAILY_LOSS_USD", 500.0),
            patch("runtime_risk.MAX_EXPOSURE_USD", 500.0),
        ):
            should_continue = process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=trade_logger,
                equity_logger=equity_logger,
                log_progress=False,
            )

        shutil.rmtree(temp_dir, ignore_errors=True)
        filter_values = json.loads(runtime.last_filter_values)

        self.assertTrue(should_continue)
        self.assertGreaterEqual(runtime.engine.trade_count, 1)
        self.assertFalse(runtime.risk_stop_active)
        self.assertEqual(notifier.risk_alerts, [])
        self.assertTrue(filter_values["risk_stop_size_exceeded"])
        self.assertEqual(filter_values["reduced_trade_size_usd"], 10.0)

    def test_daily_loss_limit_stops_bot_and_sends_alert(self) -> None:
        random.seed(0)
        notifier = RecordingNotifier()
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            telegram_notifier=notifier,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="NONE",
            reason="no_signal",
            source="test",
            block_reason="no_signal",
            allow_trade=False,
            filter_values={},
        )
        trade_logger, equity_logger, temp_dir = self._make_loggers("risk_limit_daily_loss_case")

        with (
            patch("runtime_risk.MAX_DAILY_LOSS_USD", 5.0),
            patch("runtime_risk.MAX_EXPOSURE_USD", 500.0),
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
            should_continue = process_price_tick(
                runtime=runtime,
                cycle_index=31,
                mid=94.0,
                source="test",
                trade_logger=trade_logger,
                equity_logger=equity_logger,
                log_progress=False,
            )

        shutil.rmtree(temp_dir, ignore_errors=True)
        self.assertFalse(should_continue)
        self.assertEqual(runtime.risk_stop_reason, "max_daily_loss_limit")
        self.assertLessEqual(runtime.daily_pnl_usd, -5.0)
        self.assertEqual(notifier.risk_alerts[0]["reason"], "max_daily_loss_limit")

    def test_exposure_limit_triggers_soft_reduction_without_stopping_bot(self) -> None:
        random.seed(0)
        notifier = RecordingNotifier()
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=1.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            telegram_notifier=notifier,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot()
        trade_logger, equity_logger, temp_dir = self._make_loggers("risk_limit_exposure_case")

        with (
            patch("runtime_risk.MAX_DAILY_LOSS_USD", 500.0),
            patch("runtime_risk.MAX_EXPOSURE_USD", 50.0),
        ):
            should_continue = process_price_tick(
                runtime=runtime,
                cycle_index=30,
                mid=100.0,
                source="test",
                trade_logger=trade_logger,
                equity_logger=equity_logger,
                log_progress=False,
            )

        shutil.rmtree(temp_dir, ignore_errors=True)
        filter_values = json.loads(runtime.last_filter_values)

        self.assertTrue(should_continue)
        self.assertFalse(runtime.risk_stop_active)
        self.assertEqual(runtime.risk_stop_reason, "")
        self.assertEqual(notifier.risk_alerts, [])
        self.assertTrue(runtime.last_allow_trade)
        self.assertEqual(runtime.last_final_action, "SELL")
        self.assertEqual(filter_values["inventory_limit_state"], "force_limit")
        self.assertEqual(filter_values["decision_reason"], "inventory_force_reduce")
        self.assertEqual(filter_values["trade_gate"], "allow")


if __name__ == "__main__":
    unittest.main()
