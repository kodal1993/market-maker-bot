from __future__ import annotations

import shutil
import sys
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from security_redaction import redact_secrets
from telegram_notifier import TelegramNotifier

TEST_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = TEST_ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


def build_runtime_stub() -> SimpleNamespace:
    return SimpleNamespace(
        state_context=SimpleNamespace(current_state=SimpleNamespace(value="ACCUMULATING")),
        last_execution_analytics=SimpleNamespace(
            execution_mode="private_tx",
            trade_blocked_reason="",
            mev_risk_score=27.5,
            realized_slippage_bps=3.4,
        ),
        last_decision_block_reason="",
        last_trade_reason="quoted_buy",
        current_regime_assessment=SimpleNamespace(
            market_regime="RANGE",
            regime_confidence=83.0,
        ),
        current_edge_assessment=SimpleNamespace(
            edge_score=76.0,
            expected_edge_usd=0.145,
        ),
        current_signal_gate_decision=SimpleNamespace(
            allow_trade=True,
            approved_mode="range_entry",
            blocked_reason="",
        ),
    )


def build_summary_stub() -> dict[str, float]:
    return {
        "trade_count": 7,
        "closed_trade_count": 4,
        "final_equity": 1034.25,
        "final_pnl": 34.25,
        "current_drawdown_pct": 0.031,
        "drawdown_guard_stage": "size_reduce",
        "defensive_stage": "size_reduce",
        "total_pnl": 34.25,
        "equity": 1034.25,
        "trade_size_usd": 50.0,
        "max_trade_size_usd": 75.0,
        "max_position_usd": 125.0,
        "force_trade_size_usd": 15.0,
        "target_base_pct": 0.50,
        "target_quote_pct": 0.50,
        "market_regime": "RANGE",
        "regime_confidence": 83.0,
        "edge_score": 76.0,
        "expected_edge_usd": 0.145,
        "expected_edge_bps": 58.0,
        "gate_decision": "allow",
        "blocked_reason": "",
        "consecutive_losses": 1,
        "loss_pause_remaining": 0.0,
        "realized_pnl_usd": 21.5,
        "realized_pnl": 21.5,
        "unrealized_pnl": 12.75,
        "win_rate": 57.14,
        "avg_win": 8.5,
        "avg_loss": -3.25,
        "avg_profit": 8.5,
        "avg_loss_abs_usd": 3.25,
        "max_drawdown_pct": 3.8,
        "daily_stats": {
            "realized_pnl": 21.5,
            "unrealized_pnl": 12.75,
            "win_rate": 57.14,
            "trade_count": 7,
            "closed_trade_count": 4,
            "avg_profit": 8.5,
            "avg_loss": -3.25,
            "avg_loss_abs_usd": 3.25,
        },
        "hourly_trade_count": 2,
        "hourly_skip_count": 5,
        "hourly_skip_reasons": {"risk_cap": 3, "spread_too_wide": 2},
        "strategy_mode": "RANGE_MAKER",
        "adaptive_mode": "balanced",
        "adaptive_regime": "RANGE",
        "risk_governor_state": "normal",
        "edge_bucket": "mid_vol",
        "inventory_ratio": 0.42,
        "last_final_action": "buy",
        "last_trade_reason": "quoted_buy",
        "last_execution_mode": "private_tx",
        "last_mev_risk_score": 27.5,
        "last_slippage_bps": 3.4,
        "bot_mode": "paper",
        "config_profile": "aggressive_base_paper",
        "price_source": "uniswap_v3_pool_monitor",
        "uniswap_v3_status": "active",
        "startup_config_status": "ok",
        "final_state": "ACCUMULATING",
        "loop_status": "running",
        "last_error": "",
        "daily_trade_count": 7,
        "max_trades_per_day": 48,
    }


class TelegramNotifierTests(unittest.TestCase):
    def test_send_message_retries_and_rate_limits(self) -> None:
        sleep_calls: list[float] = []
        attempts = {"count": 0}
        monotonic_values = iter([0.0, 0.2, 1.0])

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(method, "sendMessage")
            self.assertEqual(payload["chat_id"], "123")
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary_telegram_error")
            return {"ok": True, "result": {"message_id": attempts["count"]}}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            api_caller=api_caller,
            sleep_fn=sleep_calls.append,
            monotonic_fn=lambda: next(monotonic_values),
            api_max_retries=1,
            rate_limit_seconds=1.0,
        )

        self.assertTrue(notifier.send_message("*first*"))
        self.assertTrue(notifier.send_message("*second*"))
        self.assertEqual(attempts["count"], 3)
        self.assertEqual([round(value, 1) for value in sleep_calls], [1.0, 0.8])

    def test_handle_commands_discovers_chat_id_and_replies(self) -> None:
        runtime = build_runtime_stub()
        summary = build_summary_stub()
        sent_payloads: list[dict[str, object]] = []
        update_batches = [
            {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": "987"}, "text": "/status"}},
                ],
            },
            {
                "ok": True,
                "result": [
                    {"update_id": 2, "message": {"chat": {"id": "987"}, "text": "/pnl"}},
                ],
            },
        ]

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "getUpdates":
                return update_batches.pop(0)
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": len(sent_payloads)}}
            raise AssertionError(f"Unexpected method: {method}")

        temp_dir = TMP_ROOT / "telegram_notifier_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        env_path = temp_dir / ".env"
        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="",
            poll_commands=True,
            rate_limit_seconds=0.0,
            env_path=env_path,
            api_caller=api_caller,
        )

        self.assertEqual(notifier.handle_commands(runtime, lambda _: summary), 1)
        self.assertEqual(notifier.chat_id, "987")
        self.assertIn("TELEGRAM_CHAT_ID=987", env_path.read_text(encoding="utf-8"))
        self.assertIn("Statusz", str(sent_payloads[0]["text"]))
        self.assertNotIn("parse_mode", sent_payloads[0])
        self.assertIn("Final equity", str(sent_payloads[0]["text"]))
        self.assertIn("Total trade count", str(sent_payloads[0]["text"]))
        self.assertIn("Current strategy mode", str(sent_payloads[0]["text"]))
        self.assertIn("Current adaptive mode", str(sent_payloads[0]["text"]))
        self.assertIn("Risk governor state", str(sent_payloads[0]["text"]))
        self.assertIn("Hourly skip count", str(sent_payloads[0]["text"]))
        self.assertNotIn("Realizalt", str(sent_payloads[0]["text"]))

        self.assertEqual(notifier.handle_commands(runtime, lambda _: summary), 1)
        self.assertIn("PnL osszefoglalo", str(sent_payloads[1]["text"]))
        self.assertNotIn("parse_mode", sent_payloads[1])
        self.assertIn("Nem realizalt", str(sent_payloads[1]["text"]))
        self.assertIn("Realizalt", str(sent_payloads[1]["text"]))
        self.assertIn("Win/Loss", str(sent_payloads[1]["text"]))
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_health_command_works(self) -> None:
        runtime = build_runtime_stub()
        summary = build_summary_stub()
        sent_payloads: list[dict[str, object]] = []

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "getUpdates":
                return {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": "987"}, "text": "/health"}}]}
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": 1}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(enabled=True, bot_token="token", chat_id="987", poll_commands=True, api_caller=api_caller)
        self.assertEqual(notifier.handle_commands(runtime, lambda _: summary), 1)
        text = str(sent_payloads[0]["text"])
        self.assertIn("Health", text)
        self.assertIn("Uniswap V3 status", text)
        self.assertIn("Daily trades", text)

    def test_status_command_handles_missing_volatility_fields(self) -> None:
        runtime = build_runtime_stub()
        summary = build_summary_stub()
        summary.pop("volatility_bucket", None)
        summary.pop("edge_bucket", None)
        sent_payloads: list[dict[str, object]] = []

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "getUpdates":
                return {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": "987"}, "text": "/status"}}]}
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": 1}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(enabled=True, bot_token="token", chat_id="987", poll_commands=True, api_caller=api_caller)
        self.assertEqual(notifier.handle_commands(runtime, lambda _: summary), 1)
        text = str(sent_payloads[0]["text"])
        self.assertIn("Current volatility bucket: n/a", text)

    def test_telegram_disabled_mode_does_not_crash(self) -> None:
        notifier = TelegramNotifier(enabled=False, bot_token="", chat_id="", poll_commands=True)
        self.assertEqual(notifier.handle_commands(None, lambda _: {}), 0)
        self.assertFalse(notifier.send_message("hello", markdown=False))

    def test_daily_report_sends_once_per_day(self) -> None:
        runtime = build_runtime_stub()
        summary = build_summary_stub()
        sent_payloads: list[dict[str, object]] = []
        clock = {"now": datetime(2026, 3, 26, 20, 5, 0)}

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": len(sent_payloads)}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            daily_report_enabled=True,
            daily_report_hour=20,
            rate_limit_seconds=0.0,
            api_caller=api_caller,
            now_fn=lambda: clock["now"],
        )

        self.assertTrue(notifier.maybe_send_daily_report(runtime, lambda _: summary))
        self.assertFalse(notifier.maybe_send_daily_report(runtime, lambda _: summary))
        clock["now"] = datetime(2026, 3, 27, 20, 5, 0)
        self.assertTrue(notifier.maybe_send_daily_report(runtime, lambda _: summary))
        self.assertEqual(len(sent_payloads), 2)
        self.assertIn("Napi PnL riport", str(sent_payloads[0]["text"]))
        self.assertNotIn("parse_mode", sent_payloads[0])
        self.assertIn("Trade-ek szama", str(sent_payloads[0]["text"]))

    def test_notify_risk_limit_formats_alert(self) -> None:
        sent_payloads: list[dict[str, object]] = []
        runtime = build_runtime_stub()
        runtime.daily_pnl_usd = -12.5

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": len(sent_payloads)}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
            api_caller=api_caller,
            now_fn=lambda: datetime(2026, 3, 26, 12, 0, 0),
        )

        self.assertTrue(
            notifier.notify_risk_limit(
                reason="max_daily_loss_limit",
                details="daily_pnl -12.50 <= -10.00",
                runtime=runtime,
            )
        )
        self.assertIn("*Risk Stop*", str(sent_payloads[0]["text"]))
        self.assertIn("max\\_daily\\_loss\\_limit", str(sent_payloads[0]["text"]))
        self.assertIn("Daily PnL", str(sent_payloads[0]["text"]))

    def test_notify_drawdown_alert_formats_alert(self) -> None:
        sent_payloads: list[dict[str, object]] = []
        runtime = build_runtime_stub()

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": len(sent_payloads)}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
            api_caller=api_caller,
            now_fn=lambda: datetime(2026, 3, 26, 12, 0, 0),
        )

        self.assertTrue(
            notifier.notify_drawdown_alert(
                stage="pause",
                drawdown_pct=0.081,
                runtime=runtime,
            )
        )
        self.assertIn("*Trading Pause*", str(sent_payloads[0]["text"]))
        self.assertIn("pause", str(sent_payloads[0]["text"]))
        self.assertIn("8\\.10%", str(sent_payloads[0]["text"]))

    def test_notify_trade_includes_regime_and_edge_context(self) -> None:
        sent_payloads: list[dict[str, object]] = []
        runtime = build_runtime_stub()
        fill = SimpleNamespace(
            side="buy",
            price=100.25,
            size_usd=25.0,
            slippage_bps=2.4,
            execution_type="maker",
            trade_reason="quoted_buy",
        )

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": len(sent_payloads)}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
            api_caller=api_caller,
        )

        self.assertTrue(notifier.notify_trade(cycle_index=42, fill=fill, runtime=runtime, mode="RANGE_MAKER"))
        self.assertIn("Market regime", str(sent_payloads[0]["text"]))
        self.assertIn("Edge score", str(sent_payloads[0]["text"]))
        self.assertIn("Approved mode", str(sent_payloads[0]["text"]))

    def test_send_message_falls_back_to_plain_text_when_markdown_fails(self) -> None:
        sent_payloads: list[dict[str, object]] = []
        attempts = {"count": 0}

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(method, "sendMessage")
            sent_payloads.append(payload)
            attempts["count"] += 1
            if attempts["count"] == 1:
                return {
                    "ok": False,
                    "description": "Bad Request: can't parse entities: Character '.' is reserved",
                }
            return {"ok": True, "result": {"message_id": attempts["count"]}}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
            api_max_retries=0,
            api_caller=api_caller,
        )

        self.assertTrue(notifier.send_message("*Teszt*\nAr: `1\\.23`"))
        self.assertEqual(len(sent_payloads), 2)
        self.assertEqual(sent_payloads[0]["parse_mode"], "MarkdownV2")
        self.assertNotIn("parse_mode", sent_payloads[1])
        self.assertEqual(sent_payloads[1]["text"], "Teszt\nAr: 1.23")

    def test_send_message_falls_back_when_api_raises_generic_http_400(self) -> None:
        sent_payloads: list[dict[str, object]] = []
        attempts = {"count": 0}

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(method, "sendMessage")
            sent_payloads.append(payload)
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("HTTP Error 400: Bad Request")
            return {"ok": True, "result": {"message_id": attempts["count"]}}

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
            api_max_retries=0,
            api_caller=api_caller,
        )

        self.assertTrue(notifier.send_message("*Statusz*\nAr: `1\\.23`"))
        self.assertEqual(len(sent_payloads), 2)
        self.assertEqual(sent_payloads[0]["parse_mode"], "MarkdownV2")
        self.assertNotIn("parse_mode", sent_payloads[1])
        self.assertEqual(sent_payloads[1]["text"], "Statusz\nAr: 1.23")

    def test_http_post_json_parses_telegram_http_error_body(self) -> None:
        notifier = TelegramNotifier(
            enabled=True,
            bot_token="token",
            chat_id="123",
            rate_limit_seconds=0.0,
        )
        http_error = HTTPError(
            url="https://api.telegram.org/bot-token/sendMessage",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"ok":false,"description":"Bad Request: can\'t parse entities: Character \'.\' is reserved"}'),
        )

        with patch("telegram_notifier.request.urlopen", side_effect=http_error):
            response = notifier._http_post_json("sendMessage", {"chat_id": "123", "text": "x"})

        self.assertFalse(response["ok"])
        self.assertIn("can't parse entities", response["description"])

    def test_notify_error_redacts_infura_rpc_url(self) -> None:
        sent_payloads: list[dict[str, object]] = []

        def api_caller(method: str, payload: dict[str, object]) -> dict[str, object]:
            if method == "sendMessage":
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": 1}}
            return {"ok": True, "result": []}

        notifier = TelegramNotifier(enabled=True, bot_token="token", chat_id="123", rate_limit_seconds=0.0, api_caller=api_caller)
        err = "RPC failed: https://base-mainnet.infura.io/v3/abc123secret"
        self.assertTrue(notifier.notify_error("main_loop", err))
        text = str(sent_payloads[0]["text"])
        self.assertIn(r"https://base\-mainnet\.infura\.io", text)
        self.assertNotIn("/v3/abc123secret", text)

    def test_redact_secrets_masks_private_key_hex(self) -> None:
        secret = "0x" + "a" * 64
        sanitized = redact_secrets(f"wallet key leaked: {secret}")
        self.assertIn("[REDACTED_PRIVATE_KEY]", sanitized)
        self.assertNotIn(secret, sanitized)

    def test_redact_secrets_masks_telegram_token(self) -> None:
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        sanitized = redact_secrets(f"token={token}")
        self.assertIn("[REDACTED_TELEGRAM_BOT_TOKEN]", sanitized)
        self.assertNotIn(token, sanitized)



if __name__ == "__main__":
    unittest.main()
