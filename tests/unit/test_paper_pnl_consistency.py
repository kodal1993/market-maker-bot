from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bot_runner import build_summary, create_runtime
from runtime_logging import kill_switch_allows_continue


def test_zero_trades_keeps_pnl_reference_consistent_and_no_false_kill_switch() -> None:
    runtime = create_runtime(
        bootstrap_prices=[2300.0] * 40,
        reference_price=2300.0,
        start_usdc=250.0,
        start_eth=0.11005297,
    )

    runtime.total_estimated_gas_cost_usd = 959.90
    runtime.total_estimated_slippage_cost_usd = 0.0
    runtime.portfolio.fees_paid_usd = 0.0

    summary = build_summary(runtime)

    assert summary["trade_count"] == 0
    assert abs(summary["starting_equity"] - summary["ending_equity"]) < 1e-6
    assert abs(summary["gross_pnl_usd"]) < 1e-6
    assert abs(summary["net_pnl_usd"]) < 1e-6
    assert abs(summary["net_pnl_pct"]) < 1e-6
    assert kill_switch_allows_continue(runtime, summary["net_pnl_usd"], log_progress=False)
