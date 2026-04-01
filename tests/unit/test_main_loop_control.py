from __future__ import annotations

import sys
import unittest
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import main as main_module


class MainLoopControlTests(unittest.TestCase):
    def test_cycle_indices_respects_positive_max_loops(self) -> None:
        self.assertEqual(list(main_module.cycle_indices(3)), [0, 1, 2])

    def test_cycle_indices_is_unbounded_when_zero_or_negative(self) -> None:
        self.assertEqual(list(islice(main_module.cycle_indices(0), 5)), [0, 1, 2, 3, 4])
        self.assertEqual(list(islice(main_module.cycle_indices(-1), 4)), [0, 1, 2, 3])

    def test_main_polls_telegram_commands_before_price_fetch(self) -> None:
        notifier_calls: list[object] = []

        class RecordingNotifier:
            def handle_commands(self, runtime, build_summary_fn) -> int:
                notifier_calls.append(runtime)
                return 0

            def notify_error(self, context_message: str, exc: Exception | str) -> bool:
                return True

            def maybe_send_daily_report(self, runtime, build_summary_fn) -> bool:
                return False

        class FailingDex:
            def get_price(self):
                raise RuntimeError("rpc_down")

        with (
            patch.object(main_module, "close_log_sinks"),
            patch.object(main_module, "register_log_sink"),
            patch.object(main_module, "SqliteLogger", return_value=SimpleNamespace()),
            patch.object(main_module, "TelegramNotifier", return_value=RecordingNotifier()),
            patch.object(main_module, "validate_startup_config", return_value=[]),
            patch.object(main_module, "cleanup_logs_for_run", return_value={}),
            patch.object(main_module, "format_cleanup_result", return_value="ok"),
            patch.object(main_module, "CsvLogger", return_value=SimpleNamespace()),
            patch.object(main_module, "required_bootstrap_price_rows", return_value=0),
            patch.object(main_module, "load_bootstrap_prices", return_value=[]),
            patch.object(main_module, "DexClient", return_value=FailingDex()),
            patch.object(main_module, "log"),
            patch.object(main_module, "MAX_LOOPS", 1),
            patch.object(main_module, "LOOP_SECONDS", 0.0),
            patch.object(main_module.time, "sleep"),
        ):
            main_module.main()

        self.assertEqual(notifier_calls, [None, None])


if __name__ == "__main__":
    unittest.main()
