from __future__ import annotations

from datetime import datetime
from pathlib import Path

_LOG_SINKS: list[object] = []


def register_log_sink(sink: object) -> None:
    if sink not in _LOG_SINKS:
        _LOG_SINKS.append(sink)


def clear_log_sinks() -> None:
    _LOG_SINKS.clear()


def close_log_sinks() -> None:
    for sink in list(_LOG_SINKS):
        close_fn = getattr(sink, "close", None)
        if callable(close_fn):
            close_fn()
    clear_log_sinks()


def log(message: str) -> None:
    now = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
    for sink in list(_LOG_SINKS):
        sink_fn = getattr(sink, "log_event_message", None)
        if callable(sink_fn):
            try:
                sink_fn(now, message)
            except Exception as exc:  # noqa: BLE001 - logging sinks must not break runtime logging
                print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] log sink error: {exc}", flush=True)


def log_trade_record(**record: object) -> None:
    for sink in list(_LOG_SINKS):
        sink_fn = getattr(sink, "log_trade", None)
        if callable(sink_fn):
            try:
                sink_fn(**record)
            except Exception as exc:  # noqa: BLE001 - trade sinks must not break runtime logging
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{now}] trade sink error: {exc}", flush=True)


def export_last_log_lines(log_path: str | Path, *, max_lines: int = 2000) -> list[str]:
    path = Path(log_path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if max_lines <= 0:
        return lines
    return lines[-max_lines:]
