from __future__ import annotations

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
        self.trade_events: list[dict[str, object]] = []
        self.drawdown_events: list[dict[str, object]] = []
        self.chunk_events: list[dict[str, object]] = []

    def notify_trade(self, *, cycle_index: int, fill, runtime, mode: str) -> bool:
        self.trade_events.append(
            {
                "cycle_index": cycle_index,
                "side": fill.side,
                "mode": mode,
                "execution_mode": runtime.last_execution_analytics.execution_mode,
            }
        )
        return True

    def notify_risk_limit(self, *, reason: str, details: str, runtime=None) -> bool:
        return True

    def notify_drawdown_alert(self, *, stage: str, drawdown_pct: float, runtime=None) -> bool:
        self.drawdown_events.append(
            {
                "stage": stage,
                "drawdown_pct": drawdown_pct,
            }
        )
        return True

    def notify_chunk_exit(
        self,
        *,
        event: str,
        cycle_index: int,
        trade_reason: str,
        total_size_usd: float,
        completed_size_usd: float,
        chunk_index: int = 0,
        chunk_count: int = 0,
        chunk_size_usd: float = 0.0,
        runtime=None,
    ) -> bool:
        self.chunk_events.append(
            {
                "event": event,
                "cycle_index": cycle_index,
                "trade_reason": trade_reason,
                "total_size_usd": total_size_usd,
                "completed_size_usd": completed_size_usd,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "chunk_size_usd": chunk_size_usd,
            }
        )
        return True

    def notify_error(self, context_message: str, exc: Exception | str) -> bool:
        return True


class BotRunnerTelegramNotificationTests(unittest.TestCase):
    def test_process_price_tick_emits_trade_notification(self) -> None:
        random.seed(0)
        notifier = RecordingNotifier()
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=True,
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

        temp_dir = TMP_ROOT / "telegram_bridge_case"
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
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(len(notifier.trade_events), 1)
        self.assertEqual(notifier.trade_events[0]["side"], "buy")

    def test_process_price_tick_emits_drawdown_alert_stages(self) -> None:
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
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="NONE",
            reason="no_signal",
            source="test",
            block_reason="no_signal",
            allow_trade=False,
            filter_values={},
        )

        market = SimpleNamespace(
            regime="TREND",
            volatility_state="NORMAL",
            short_ma=101.0,
            long_ma=100.0,
            volatility=0.0012,
            trend_strength=1.1,
            market_score=0.30,
        )
        signal = SimpleNamespace(score=0.0, confidence=0.0, blocked=False)
        adaptive = SimpleNamespace(
            performance_score=0.0,
            inventory_multiplier=1.0,
            trade_size_multiplier=1.0,
            spread_multiplier=1.0,
            threshold_multiplier=1.0,
        )

        temp_dir = TMP_ROOT / "telegram_drawdown_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        trade_path = temp_dir / "trades.csv"
        equity_path = temp_dir / "equity.csv"
        trade_logger = CsvLogger(str(trade_path), trade_log_headers())
        equity_logger = CsvLogger(str(equity_path), equity_log_headers())

        with (
            patch("intelligence.build_market_state", return_value=market),
            patch("intelligence.build_news_signal", return_value=signal),
            patch("intelligence.build_macro_signal", return_value=signal),
            patch("intelligence.build_onchain_signal", return_value=signal),
            patch("intelligence.build_adaptive_state", return_value=adaptive),
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
            self.assertTrue(
                process_price_tick(
                    runtime=runtime,
                    cycle_index=31,
                    mid=97.0,
                    source="test",
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=False,
                )
            )
            self.assertTrue(
                process_price_tick(
                    runtime=runtime,
                    cycle_index=32,
                    mid=95.0,
                    source="test",
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=False,
                )
            )
            self.assertTrue(
                process_price_tick(
                    runtime=runtime,
                    cycle_index=33,
                    mid=92.0,
                    source="test",
                    trade_logger=trade_logger,
                    equity_logger=equity_logger,
                    log_progress=False,
                )
            )

        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual([event["stage"] for event in notifier.drawdown_events], ["pause"])
        self.assertEqual(runtime.drawdown_guard_stage, "pause")
        self.assertAlmostEqual(runtime.current_drawdown_pct, 0.08, places=3)

    def test_process_price_tick_emits_chunk_exit_notifications(self) -> None:
        random.seed(0)
        notifier = RecordingNotifier()
        runtime = create_runtime(
            bootstrap_prices=[100.0] * 30,
            reference_price=100.0,
            start_usdc=0.0,
            start_eth=2.5,
            start_eth_usd=0.0,
            enable_trade_filter=False,
            enable_execution_engine=False,
            telegram_notifier=notifier,
        )
        runtime.intelligence.build_snapshot = lambda **kwargs: build_snapshot(mode="OVERWEIGHT_EXIT")
        runtime.decision_engine.decide = lambda **kwargs: DecisionOutcome(
            action="SELL",
            size_usd=180.0,
            reason="force_trade_sell",
            source="force_trade",
            order_price=100.0,
            inventory_cap_usd=5_000.0,
            allow_trade=True,
            filter_values={},
        )

        temp_dir = TMP_ROOT / "telegram_chunk_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        trade_path = temp_dir / "trades.csv"
        equity_path = temp_dir / "equity.csv"
        trade_logger = CsvLogger(str(trade_path), trade_log_headers())
        equity_logger = CsvLogger(str(equity_path), equity_log_headers())

        with (
            patch("sizing_engine.ACCOUNT_SIZE_OVERRIDE", 250.0),
            patch("sizing_engine.MAX_POSITION_PCT", 1.0),
            patch("sizing_engine.MAX_TRADE_SIZE_PCT", 0.30),
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

        self.assertEqual(
            [event["event"] for event in notifier.chunk_events],
            [
                "chunk_exit_started",
                "chunk_exit_progress",
                "chunk_exit_progress",
                "chunk_exit_completed",
            ],
        )
        self.assertEqual(runtime.engine.trade_count, 3)


if __name__ == "__main__":
    unittest.main()
