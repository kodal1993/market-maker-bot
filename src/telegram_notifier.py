from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib import error, request

from config import (
    ENV_PATH,
    TELEGRAM_API_MAX_RETRIES,
    TELEGRAM_API_TIMEOUT_SEC,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_DAILY_REPORT_ENABLED,
    TELEGRAM_DAILY_REPORT_HOUR,
    TELEGRAM_ENABLED,
    TELEGRAM_POLL_COMMANDS,
    TELEGRAM_RATE_LIMIT_SECONDS,
)
from logger import log

if TYPE_CHECKING:
    from bot_runner import BotRuntime


def _escape_markdown_v2(value: object) -> str:
    text = str(value)
    escape_chars = set("_*[]()~`>#+-=|{}.!\\")
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)


def _markdown_to_plain(text: str) -> str:
    escape_chars = "_*[]()~`>#+-=|{}.!\\"
    plain = text
    for char in escape_chars:
        plain = plain.replace(f"\\{char}", char)
    return plain.replace("`", "").replace("*", "")


def _is_markdown_error(description: object) -> bool:
    text = str(description or "").lower()
    return (
        "parse entities" in text
        or "can't parse entities" in text
        or "bad request" in text
        or "http error 400" in text
    )


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _code(value: object) -> str:
    return f"`{_escape_markdown_v2(value)}`"


def _money(value: object, decimals: int = 2) -> str:
    return _code(f"${_as_float(value):.{decimals}f}")


def _bps(value: object, decimals: int = 2) -> str:
    return _code(f"{_as_float(value):.{decimals}f} bps")


def _pct(value: object, decimals: int = 2) -> str:
    return _code(f"{_as_float(value):.{decimals}f}%")


def _plain(value: object) -> str:
    return _code(value if value not in {"", None} else "n/a")


def _formatted(value: object, decimals: int = 1) -> str:
    return f"{_as_float(value):.{decimals}f}"


def _balance_pnl_trade_count_lines(summary: dict[str, object]) -> list[str]:
    return [
        f"Egyenleg: {_money(summary.get('final_equity', 0.0))}",
        f"PnL: {_money(summary.get('final_pnl', summary.get('total_pnl', 0.0)))}",
        f"Trade-ek szama: {_plain(summary.get('trade_count', 0))}",
    ]


def _sizing_lines(summary: dict[str, object]) -> list[str]:
    return [
        f"Trade meret: {_money(summary.get('trade_size_usd', 0.0))}",
        f"Max trade meret: {_money(summary.get('max_trade_size_usd', 0.0))}",
        f"Max pozicio: {_money(summary.get('max_position_usd', 0.0))}",
        f"Force trade meret: {_money(summary.get('force_trade_size_usd', 0.0))}",
        f"Cel base: {_pct(_as_float(summary.get('target_base_pct', 0.0)) * 100.0)}",
        f"Cel quote: {_pct(_as_float(summary.get('target_quote_pct', 0.0)) * 100.0)}",
    ]


def _market_gate_lines(summary: dict[str, object]) -> list[str]:
    return [
        f"Piaci rezsim: {_plain(summary.get('market_regime', 'n/a'))}",
        f"Rezsim bizalom: {_plain(_formatted(summary.get('regime_confidence', 0.0)))}",
        f"Edge pontszam: {_plain(_formatted(summary.get('edge_score', 0.0)))}",
        f"Varhato edge: {_money(summary.get('expected_edge_usd', 0.0), decimals=4)}",
        f"Varhato edge bps: {_bps(summary.get('expected_edge_bps', 0.0))}",
        f"Gate dontes: {_plain(summary.get('gate_decision', 'reject'))}",
        f"Blokkolas oka: {_plain(summary.get('blocked_reason', 'n/a'))}",
        f"Egymas utani vesztesgek: {_plain(summary.get('consecutive_losses', 0))}",
        f"Vesztesegszunet: {_plain(_formatted(summary.get('loss_pause_remaining', 0.0)) + ' perc')}",
    ]


def _pnl_lines(summary: dict[str, object]) -> list[str]:
    return [
        *_balance_pnl_trade_count_lines(summary),
        f"Start equity: {_money(summary.get('starting_equity', summary.get('start_equity', 0.0)))}",
        f"End equity: {_money(summary.get('ending_equity', summary.get('final_equity', 0.0)))}",
        f"Realizalt: {_money(summary.get('realized_pnl_usd', summary.get('realized_pnl', 0.0)))}",
        f"Nem realizalt: {_money(summary.get('unrealized_pnl', 0.0))}",
        f"Net PnL: {_money(summary.get('net_pnl_usd', 0.0))} ({_pct(summary.get('net_pnl_pct', 0.0))})",
        f"Avg trade PnL: {_money(summary.get('average_trade_pnl_usd', 0.0))}",
        f"Fees: {_money(summary.get('fees_paid_usd', 0.0))} | Gas est: {_money(summary.get('estimated_gas_cost_usd', 0.0))}",
        f"Slippage est: {_money(summary.get('estimated_slippage_cost_usd', 0.0))}",
        f"Most common no-trade: {_plain(summary.get('most_common_no_trade_reason', 'n/a'))}",
        f"Regime: {_plain(summary.get('active_regime', summary.get('market_regime', 'n/a')))}",
        f"Inventory ratio: {_pct(_as_float(summary.get('inventory_ratio', 0.0)) * 100.0)}",
        (
            f"Target tracker: {_plain(summary.get('target_tracker_status', 'n/a'))} | "
            f"90d proj {_pct(summary.get('projected_90d_return_pct', 0.0))} | "
            f"annual proj {_pct(summary.get('projected_annualized_return_pct', 0.0))}"
        ),
    ]


def _drawdown_stage_label(stage: object) -> str:
    labels = {
        "normal": "normal",
        "size_reduce": "size_reduce",
        "aggression_reduce": "aggression_reduce",
        "pause": "pause",
    }
    return labels.get(str(stage), str(stage) or "normal")


class TelegramNotifier:
    def __init__(
        self,
        *,
        enabled: bool = TELEGRAM_ENABLED,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
        poll_commands: bool = TELEGRAM_POLL_COMMANDS,
        daily_report_enabled: bool = TELEGRAM_DAILY_REPORT_ENABLED,
        daily_report_hour: int = TELEGRAM_DAILY_REPORT_HOUR,
        api_timeout_sec: float = TELEGRAM_API_TIMEOUT_SEC,
        api_max_retries: int = TELEGRAM_API_MAX_RETRIES,
        rate_limit_seconds: float = TELEGRAM_RATE_LIMIT_SECONDS,
        env_path: str | Path = ENV_PATH,
        api_caller: Callable[[str, dict[str, object]], dict[str, object]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self.enabled = enabled and bool(bot_token)
        self.bot_token = bot_token
        self.chat_id = str(chat_id).strip()
        self.poll_commands = poll_commands
        self.daily_report_enabled = daily_report_enabled
        self.daily_report_hour = max(min(int(daily_report_hour), 23), 0)
        self.api_timeout_sec = max(float(api_timeout_sec), 1.0)
        self.api_max_retries = max(int(api_max_retries), 0)
        self.rate_limit_seconds = max(float(rate_limit_seconds), 0.0)
        self.env_path = Path(env_path)
        self.api_caller = api_caller or self._http_post_json
        self.sleep_fn = sleep_fn or time.sleep
        self.now_fn = now_fn or datetime.now
        self.monotonic_fn = monotonic_fn or time.monotonic
        self.last_send_ts: float | None = None
        self.last_update_id = 0
        self.last_daily_report_date = None

    def is_enabled(self) -> bool:
        return self.enabled

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _http_post_json(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url(method),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.api_timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed = None

            if isinstance(parsed, dict):
                return parsed

            description = raw_body.strip() or f"HTTP Error {exc.code}: {exc.reason}"
            return {
                "ok": False,
                "description": description,
                "result": [],
                "error_code": exc.code,
            }

    def _api_call(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        if not self.is_enabled():
            return {"ok": False, "result": []}

        last_error = "telegram_disabled"
        for attempt in range(self.api_max_retries + 1):
            try:
                response = self.api_caller(method, payload)
                if response.get("ok"):
                    return response
                last_error = str(response.get("description", "telegram_api_error"))
            except Exception as exc:  # noqa: BLE001 - notifier must never break the bot
                last_error = str(exc)

            if attempt < self.api_max_retries:
                self.sleep_fn(min(1.0 * (attempt + 1), 3.0))

        log(f"Telegram API failed | method {method} | error {last_error}")
        return {"ok": False, "description": last_error, "result": []}

    def _throttle(self) -> None:
        if self.rate_limit_seconds <= 0:
            return

        now_ts = self.monotonic_fn()
        if self.last_send_ts is None:
            self.last_send_ts = now_ts
            return

        elapsed = now_ts - self.last_send_ts
        if elapsed < self.rate_limit_seconds:
            self.sleep_fn(self.rate_limit_seconds - elapsed)
        self.last_send_ts = self.monotonic_fn()

    def _persist_chat_id(self) -> None:
        if not self.chat_id:
            return
        try:
            existing = self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
            lines = existing.splitlines()
            updated = False
            for index, line in enumerate(lines):
                if line.startswith("TELEGRAM_CHAT_ID="):
                    lines[index] = f"TELEGRAM_CHAT_ID={self.chat_id}"
                    updated = True
                    break
            if not updated:
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(f"TELEGRAM_CHAT_ID={self.chat_id}")
            self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            log(f"Telegram chat id persist failed: {exc}")

    def discover_chat_id(self) -> str:
        if self.chat_id or not self.is_enabled():
            return self.chat_id

        response = self._api_call(
            "getUpdates",
            {
                "offset": self.last_update_id + 1,
                "limit": 5,
                "timeout": 0,
                "allowed_updates": ["message"],
            },
        )
        updates = response.get("result", [])
        for update in updates:
            self.last_update_id = max(self.last_update_id, int(update.get("update_id", 0)))
            message = update.get("message", {})
            if not isinstance(message, dict):
                continue
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", "")).strip()
            if chat_id:
                self.chat_id = chat_id
                self._persist_chat_id()

        return self.chat_id

    def _send_message_payload(self, payload: dict[str, object]) -> dict[str, object]:
        self._throttle()
        return self._api_call("sendMessage", payload)

    def send_message(self, text: str, *, markdown: bool = True) -> bool:
        if not self.is_enabled() or not text.strip():
            return False

        if not self.chat_id:
            self.discover_chat_id()
        if not self.chat_id:
            log("Telegram chat_id not available yet. Send /start or any message to the bot first.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text if markdown else _markdown_to_plain(text),
            "disable_web_page_preview": True,
        }
        if markdown:
            payload["parse_mode"] = "MarkdownV2"

        response = self._send_message_payload(payload)
        if response.get("ok"):
            return True

        if not markdown:
            return False

        description = response.get("description", "")
        log(f"Telegram send fallback triggered | error {description}")
        if _is_markdown_error(description):
            log(f"Telegram Markdown fallback | error {description}")

        fallback_response = self._send_message_payload(
            {
                "chat_id": self.chat_id,
                "text": _markdown_to_plain(text),
                "disable_web_page_preview": True,
            }
        )
        return bool(fallback_response.get("ok"))

    def poll_updates(self) -> list[dict[str, object]]:
        if not self.is_enabled() or not self.poll_commands:
            return []

        response = self._api_call(
            "getUpdates",
            {
                "offset": self.last_update_id + 1,
                "limit": 20,
                "timeout": 0,
                "allowed_updates": ["message"],
            },
        )
        if not response.get("ok"):
            description = str(response.get("description", ""))
            if "webhook" in description.lower():
                log("Telegram polling blocked by active webhook. Clear the webhook or keep polling disabled.")
            return []

        updates = response.get("result", [])
        messages: list[dict[str, object]] = []
        for update in updates:
            self.last_update_id = max(self.last_update_id, int(update.get("update_id", 0)))
            message = update.get("message", {})
            if not isinstance(message, dict):
                continue
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", "")).strip()
            if chat_id and not self.chat_id:
                self.chat_id = chat_id
                self._persist_chat_id()
            messages.append(message)
        return messages

    def _summary_text(self, runtime: BotRuntime, summary: dict[str, object]) -> str:
        return "\n".join(
            [
                "*Statusz*",
                *_balance_pnl_trade_count_lines(summary),
                *_market_gate_lines(summary),
                *_sizing_lines(summary),
            ]
        )

    def _pnl_text(self, summary: dict[str, object]) -> str:
        return "\n".join(
            [
                "*PnL osszefoglalo*",
                *_pnl_lines(summary),
            ]
        )

    def handle_commands(self, runtime: BotRuntime | None, build_summary_fn) -> int:
        sent_count = 0
        for message in self.poll_updates():
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", "")).strip()
            if self.chat_id and chat_id and chat_id != self.chat_id:
                log("Telegram command ignored from unexpected chat_id")
                continue

            text = str(message.get("text", "")).strip()
            if not text.startswith("/"):
                continue

            command = text.split()[0].split("@")[0].lower()
            if runtime is None:
                reply = "*Bot statusz*\nFutas: `not_ready`"
            else:
                summary = build_summary_fn(runtime)
                if command == "/status":
                    reply = self._summary_text(runtime, summary)
                elif command == "/pnl":
                    reply = self._pnl_text(summary)
                else:
                    reply = "*Parancsok*\n`/status`\n`/pnl`"

            if self.send_message(reply, markdown=False):
                sent_count += 1

        return sent_count

    def notify_trade(self, *, cycle_index: int, fill, runtime: BotRuntime, mode: str) -> bool:
        analytics = runtime.last_execution_analytics
        regime = getattr(runtime, "current_regime_assessment", None)
        edge = getattr(runtime, "current_edge_assessment", None)
        gate = getattr(runtime, "current_signal_gate_decision", None)
        slippage_bps = analytics.realized_slippage_bps or fill.slippage_bps
        gate_allowed = bool(getattr(gate, "allow_trade", False))
        gate_reason = getattr(gate, "blocked_reason", "") or runtime.last_decision_block_reason or "-"
        approved_mode = getattr(gate, "approved_mode", "") or mode
        message = "\n".join(
            [
                f"*{_escape_markdown_v2(fill.side.upper())} Trade*",
                f"Cycle: {_plain(cycle_index)}",
                f"Mode: {_plain(mode)}",
                f"Approved mode: {_plain(approved_mode)}",
                f"Execution: {_plain(analytics.execution_mode or fill.execution_type)}",
                f"Market regime: {_plain(getattr(regime, 'market_regime', 'n/a'))}",
                f"Regime confidence: {_plain(_formatted(getattr(regime, 'regime_confidence', 0.0)))}",
                f"Edge score: {_plain(_formatted(getattr(edge, 'edge_score', 0.0)))}",
                f"Expected edge: {_money(getattr(edge, 'expected_edge_usd', 0.0), decimals=4)}",
                f"Price: {_money(fill.price, decimals=4)}",
                f"Size: {_money(fill.size_usd)}",
                f"MEV risk: {_plain(_formatted(analytics.mev_risk_score))}",
                f"Slippage: {_bps(slippage_bps)}",
                f"Gate: {_plain('allow' if gate_allowed else 'reject')}",
                f"Gate detail: {_plain('approved' if gate_allowed else gate_reason)}",
                f"Reason: {_plain(fill.trade_reason or runtime.last_trade_reason or '-')}",
            ]
        )
        return self.send_message(message)

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
        runtime: BotRuntime | None = None,
    ) -> bool:
        remaining_size_usd = max(total_size_usd - completed_size_usd, 0.0)
        state_line = []
        if runtime is not None:
            state_line = [f"State: {_plain(runtime.state_context.current_state.value)}"]

        message = "\n".join(
            [
                "*Chunk Exit*",
                f"Event: {_plain(event)}",
                f"Cycle: {_plain(cycle_index)}",
                f"Reason: {_plain(trade_reason)}",
                f"Chunk: {_plain(f'{chunk_index}/{chunk_count}' if chunk_count else '-')}",
                f"Chunk size: {_money(chunk_size_usd)}",
                f"Completed: {_money(completed_size_usd)}",
                f"Remaining: {_money(remaining_size_usd)}",
                *state_line,
            ]
        )
        return self.send_message(message)

    def notify_error(self, context_message: str, exc: Exception | str) -> bool:
        message = "\n".join(
            [
                "*Bot Error*",
                f"Context: {_plain(context_message)}",
                f"Error: {_plain(exc)}",
                f"Time: {_plain(self.now_fn().isoformat(timespec='seconds'))}",
            ]
        )
        return self.send_message(message)

    def notify_risk_limit(self, *, reason: str, details: str, runtime: BotRuntime | None = None) -> bool:
        summary_lines = []
        if runtime is not None:
            summary_lines = [
                f"State: {_plain(runtime.state_context.current_state.value)}",
                f"Daily PnL: {_money(getattr(runtime, 'daily_pnl_usd', 0.0))}",
            ]

        message = "\n".join(
            [
                "*Risk Stop*",
                f"Reason: {_plain(reason)}",
                f"Details: {_plain(details)}",
                *summary_lines,
                f"Time: {_plain(self.now_fn().isoformat(timespec='seconds'))}",
            ]
        )
        return self.send_message(message)

    def notify_drawdown_alert(self, *, stage: str, drawdown_pct: float, runtime: BotRuntime | None = None) -> bool:
        if str(stage) != "pause":
            return False

        state_line = []
        if runtime is not None:
            state_line = [f"State: {_plain(runtime.state_context.current_state.value)}"]

        message = "\n".join(
            [
                "*Trading Pause*",
                f"Reason: {_plain(_drawdown_stage_label(stage))}",
                f"Drawdown: {_pct(drawdown_pct * 100.0)}",
                *state_line,
                f"Time: {_plain(self.now_fn().isoformat(timespec='seconds'))}",
            ]
        )
        return self.send_message(message)

    def notify_daily_report(self, summary: dict[str, object], *, force: bool = False) -> bool:
        if not self.is_enabled():
            return False

        now = self.now_fn()
        today = now.date()
        if not force:
            if not self.daily_report_enabled or now.hour < self.daily_report_hour:
                return False
            if self.last_daily_report_date == today:
                return False

        message = "\n".join(
            [
                "*Napi PnL riport*",
                f"Datum: {_plain(today.isoformat())}",
                *_pnl_lines(summary),
                f"Max DD: {_pct(summary.get('max_drawdown_pct', 0.0))}",
            ]
        )
        sent = self.send_message(message, markdown=False)
        if sent:
            self.last_daily_report_date = today
        return sent

    def maybe_send_daily_report(self, runtime: BotRuntime | None, build_summary_fn) -> bool:
        if runtime is None or not self.daily_report_enabled:
            return False
        return self.notify_daily_report(build_summary_fn(runtime), force=False)
