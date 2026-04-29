from types import SimpleNamespace

from src.bot_runner import _maybe_log_hourly_report


class _Portfolio:
    def inventory_value_usd(self, _mid: float) -> float:
        return 25.0

    def equity(self, _mid: float) -> float:
        return 100.0


def test_hourly_report_uses_computed_inventory_ratio_without_runtime_attr() -> None:
    runtime = SimpleNamespace(
        cycle_seconds=60.0,
        hourly_window_cycle_count=60,
        hourly_skip_reasons={},
        total_skip_reasons={},
        total_attempted_expected_profit_usd=0.0,
        total_attempted_trade_count=0,
        hourly_trade_count=1,
        hourly_skip_count=0,
        current_strategy_mode="normal",
        current_volatility_bucket="low",
        intelligence=SimpleNamespace(volatility=0.01),
        total_trade_count=1,
        total_skip_count=0,
        adaptive_config=None,
        portfolio=_Portfolio(),
        last_mid=1.0,
    )

    _maybe_log_hourly_report(runtime, cycle_index=60)

    assert runtime.hourly_window_cycle_count == 0
    assert runtime.hourly_trade_count == 0
    assert runtime.hourly_skip_count == 0
