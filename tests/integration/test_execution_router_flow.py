from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from execution_router import ExecutionRouter
from mev_risk_engine import MevRiskEngine
from policy_engine import PolicyEngine
from private_tx_executor import PrivateTxExecutor
from quote_validator import QuoteValidator
from slippage_guard import SlippageGuard
from trade_simulator import TradeSimulator
from types_bot import ExecutionContext, ExecutionSignal


def build_router() -> ExecutionRouter:
    return ExecutionRouter(
        policy_engine=PolicyEngine(profile="balanced"),
        private_tx_executor=PrivateTxExecutor(
            enabled=True,
            rpc_url="https://private-rpc.test",
            timeout_sec=8.0,
            max_retries=2,
            bot_mode="paper",
        ),
        slippage_guard=SlippageGuard(),
        mev_risk_engine=MevRiskEngine(),
        quote_validator=QuoteValidator(),
        trade_simulator=TradeSimulator(),
    )


def build_context(**overrides) -> ExecutionContext:
    values = {
        "pair": "WETH/USDC",
        "router": "uniswap_v3",
        "mid_price": 100.0,
        "quote_bid": 99.95,
        "quote_ask": 100.05,
        "router_price": 100.05,
        "backup_price": 100.0,
        "onchain_ref_price": 100.0,
        "twap_price": 100.0,
        "spread_bps": 10.0,
        "volatility": 0.0015,
        "liquidity_usd": 3_000_000.0,
        "gas_price_gwei": 12.0,
        "block_number": 10,
        "recent_blocks_since_trade": 5,
        "portfolio_usdc": 1000.0,
        "portfolio_eth": 0.2,
        "market_mode": "TREND_UP",
        "metadata": {},
    }
    values.update(overrides)
    return ExecutionContext(**values)


def build_signal(**overrides) -> ExecutionSignal:
    values = {
        "side": "buy",
        "size_usd": 80.0,
        "limit_price": 100.05,
        "trade_reason": "trend_buy",
        "mode": "TREND_UP",
        "source": "test",
        "pair": "WETH/USDC",
        "router": "uniswap_v3",
        "inventory_cap_usd": 1000.0,
        "metadata": {},
    }
    values.update(overrides)
    return ExecutionSignal(**values)


class ExecutionRouterIntegrationTests(unittest.TestCase):
    def test_low_risk_flow_uses_guarded_public(self) -> None:
        result = build_router().execute_trade(build_signal(size_usd=80.0), build_context())

        self.assertTrue(result.allow_trade)
        self.assertEqual(result.execution_mode, "guarded_public")
        self.assertLess(result.mev_risk_score, 40.0)

    def test_medium_risk_flow_uses_private(self) -> None:
        result = build_router().execute_trade(
            build_signal(size_usd=120.0),
            build_context(liquidity_usd=180_000.0, spread_bps=18.0, volatility=0.0035, gas_price_gwei=28.0),
        )

        self.assertTrue(result.allow_trade)
        self.assertEqual(result.execution_mode, "private_tx")
        self.assertTrue(result.private_tx_used)

    def test_medium_risk_without_private_availability_is_skipped(self) -> None:
        router = ExecutionRouter(
            policy_engine=PolicyEngine(profile="balanced"),
            private_tx_executor=PrivateTxExecutor(
                enabled=True,
                rpc_url="",
                timeout_sec=8.0,
                max_retries=2,
                bot_mode="live",
            ),
            slippage_guard=SlippageGuard(),
            mev_risk_engine=MevRiskEngine(),
            quote_validator=QuoteValidator(),
            trade_simulator=TradeSimulator(),
        )

        result = router.execute_trade(
            build_signal(size_usd=120.0),
            build_context(liquidity_usd=180_000.0, spread_bps=18.0, volatility=0.0035, gas_price_gwei=28.0),
        )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.execution_mode, "skip")
        self.assertEqual(result.trade_blocked_reason, "no_safe_execution_mode")

    def test_high_risk_is_skipped(self) -> None:
        result = build_router().execute_trade(
            build_signal(size_usd=180.0),
            build_context(liquidity_usd=45_000.0, spread_bps=28.0, volatility=0.008, gas_price_gwei=34.0),
        )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.execution_mode, "skip")
        self.assertIn(result.trade_blocked_reason, {"mev_risk_too_high", "slippage_above_dynamic_guard", "price_impact_too_high"})

    def test_bad_quote_is_skipped(self) -> None:
        result = build_router().execute_trade(
            build_signal(),
            build_context(router_price=104.0, backup_price=100.0, onchain_ref_price=100.0, twap_price=100.0),
        )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.execution_mode, "skip")
        self.assertEqual(result.trade_blocked_reason, "quote_deviation_too_high")

    def test_high_gas_is_skipped(self) -> None:
        result = build_router().execute_trade(
            build_signal(size_usd=80.0),
            build_context(gas_price_gwei=41.0),
        )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.execution_mode, "skip")
        self.assertEqual(result.trade_blocked_reason, "gas_spike_skip")

    def test_bad_gas_profit_ratio_is_skipped(self) -> None:
        result = build_router().execute_trade(
            build_signal(
                size_usd=100.0,
                metadata={"expected_profit_pct": 0.005},
            ),
            build_context(gas_price_gwei=18.0),
        )

        self.assertFalse(result.allow_trade)
        self.assertEqual(result.execution_mode, "skip")
        self.assertEqual(result.trade_blocked_reason, "gas_cost_exceeds_expected_profit")

    def test_paper_activity_bypasses_gas_profit_ratio_guard(self) -> None:
        result = build_router().execute_trade(
            build_signal(
                size_usd=100.0,
                metadata={
                    "expected_profit_pct": 0.005,
                    "paper_mode": True,
                    "paper_activity_override": True,
                },
            ),
            build_context(gas_price_gwei=18.0),
        )

        self.assertTrue(result.allow_trade)
        self.assertEqual(result.execution_mode, "guarded_public")
        self.assertTrue(result.metadata["gas_profit_guard_bypassed"])


if __name__ == "__main__":
    unittest.main()
