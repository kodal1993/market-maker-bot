from __future__ import annotations

import sys
import unittest
from pathlib import Path
import shutil
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mev_risk_engine import MevRiskEngine
from order_slicer import OrderSlicer
from policy_engine import PolicyEngine
from private_tx_executor import PrivateTxExecutor
from quote_validator import QuoteValidator
from slippage_guard import SlippageGuard
from types_bot import ExecutionContext, ExecutionPolicy, ExecutionSignal

TEST_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = TEST_ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)


def build_policy(**overrides) -> ExecutionPolicy:
    values = {
        "profile": "balanced",
        "allow_private_tx": True,
        "allow_cow": False,
        "allow_guarded_public": True,
        "public_swap_max_risk": 40.0,
        "mev_risk_threshold_block": 70.0,
        "max_quote_deviation_bps": 35.0,
        "max_twap_deviation_bps": 55.0,
        "max_price_impact_bps": 45.0,
        "max_slippage_bps": 40.0,
        "max_gas_spike_gwei": 35.0,
        "max_single_swap_usd": 125.0,
        "slice_count_max": 4,
        "slice_delay_ms": 250,
        "cow_min_notional_usd": 150.0,
        "cow_supported": True,
        "liquidity_hint_usd": 1_000_000.0,
        "preferred_mode": "",
        "metadata": {"enable_order_slicing": True},
    }
    values.update(overrides)
    return ExecutionPolicy(**values)


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
        "twap_price": 99.98,
        "spread_bps": 10.0,
        "volatility": 0.0015,
        "liquidity_usd": 2_000_000.0,
        "gas_price_gwei": 12.0,
        "block_number": 1,
        "recent_blocks_since_trade": 2,
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
        "size_usd": 100.0,
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


class MevExecutionComponentTests(unittest.TestCase):
    def test_quote_validator_blocks_large_deviation(self) -> None:
        validator = QuoteValidator()
        signal = build_signal()
        context = build_context(router_price=103.5)

        result = validator.validate(signal, context, build_policy())

        self.assertFalse(result.is_valid)
        self.assertEqual(result.block_reason, "quote_deviation_too_high")

    def test_slippage_guard_blocks_large_price_impact(self) -> None:
        guard = SlippageGuard()
        signal = build_signal(size_usd=750.0)
        context = build_context(liquidity_usd=40_000.0, spread_bps=18.0, volatility=0.004)

        result = guard.evaluate(signal, context, build_policy(max_price_impact_bps=35.0))

        self.assertFalse(result.is_valid)
        self.assertIn(result.block_reason, {"price_impact_too_high", "slippage_above_dynamic_guard"})

    def test_mev_risk_engine_uses_public_private_skip_thresholds(self) -> None:
        risk_engine = MevRiskEngine()
        policy = build_policy()

        low_signal = build_signal(size_usd=80.0)
        low_context = build_context(liquidity_usd=3_000_000.0, gas_price_gwei=12.0, spread_bps=8.0, volatility=0.001)
        low_guard = SlippageGuard().evaluate(low_signal, low_context, policy)
        low_quote = QuoteValidator().validate(low_signal, low_context, policy)
        low_result = risk_engine.assess(low_signal, low_context, policy, low_guard, low_quote)
        self.assertLess(low_result.mev_risk_score, 40.0)
        self.assertEqual(low_result.recommended_execution_mode, "guarded_public")

        mid_signal = build_signal(size_usd=120.0)
        mid_context = build_context(liquidity_usd=180_000.0, gas_price_gwei=28.0, spread_bps=18.0, volatility=0.0035)
        mid_guard = SlippageGuard().evaluate(mid_signal, mid_context, policy)
        mid_quote = QuoteValidator().validate(mid_signal, mid_context, policy)
        mid_result = risk_engine.assess(mid_signal, mid_context, policy, mid_guard, mid_quote)
        self.assertGreaterEqual(mid_result.mev_risk_score, 40.0)
        self.assertLessEqual(mid_result.mev_risk_score, 70.0)
        self.assertEqual(mid_result.recommended_execution_mode, "private_tx")

        high_signal = build_signal(size_usd=180.0)
        high_context = build_context(liquidity_usd=45_000.0, gas_price_gwei=42.0, spread_bps=28.0, volatility=0.008)
        high_guard = SlippageGuard().evaluate(high_signal, high_context, policy)
        high_quote = QuoteValidator().validate(high_signal, high_context, policy)
        high_result = risk_engine.assess(high_signal, high_context, policy, high_guard, high_quote)
        self.assertGreater(high_result.mev_risk_score, 70.0)
        self.assertEqual(high_result.recommended_execution_mode, "skip")

    def test_order_slicer_splits_large_order(self) -> None:
        slicer = OrderSlicer()
        signal = build_signal(size_usd=400.0)
        policy = build_policy(max_single_swap_usd=125.0, slice_count_max=4, slice_delay_ms=200)
        slippage = SlippageGuard().evaluate(signal, build_context(), policy)

        slices = slicer.slice_order(signal, policy, slippage)

        self.assertEqual(len(slices), 4)
        self.assertAlmostEqual(sum(item.size_usd for item in slices), 400.0, places=6)
        self.assertEqual(slices[1].delay_ms, 200)

    def test_policy_engine_loads_pair_router_rules(self) -> None:
        temp_dir = TMP_ROOT / "policy_engine_case"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        policy_path = temp_dir / "mev_policy.yaml"
        policy_path.write_text(
            '{"defaults":{"max_slippage_bps":33.0,"allow_cow":true},'
            '"profiles":{"safe":{"public_swap_max_risk":20.0}},'
            '"pairs":{"WETH/USDC":{"cow_supported":true,"liquidity_hint_usd":4440000.0}},'
            '"routers":{"uniswap_v3":{"slice_delay_ms":180}}}',
            encoding="utf-8",
        )

        policy = PolicyEngine(policy_path=policy_path, profile="safe").resolve("WETH/USDC", "uniswap_v3")
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertTrue(policy.cow_supported)
        self.assertEqual(policy.slice_delay_ms, 180)
        self.assertEqual(policy.public_swap_max_risk, 20.0)
        self.assertEqual(policy.liquidity_hint_usd, 4_440_000.0)

    def test_private_tx_executor_sends_signed_raw_transaction(self) -> None:
        sent_payloads: list[bytes] = []

        class FakeAccount:
            @staticmethod
            def sign_transaction(tx_params, private_key):
                self.assertEqual(private_key, "0xabc")
                self.assertEqual(tx_params["nonce"], 7)
                return SimpleNamespace(raw_transaction=b"\x12\x34")

        class FakeEth:
            account = FakeAccount()
            chain_id = 8453
            gas_price = 12_000_000_000

            @staticmethod
            def get_transaction_count(address, block_identifier):
                self.assertEqual(block_identifier, "pending")
                return 7

            @staticmethod
            def estimate_gas(tx_params):
                return 210_000

            @staticmethod
            def send_raw_transaction(raw_tx):
                sent_payloads.append(bytes(raw_tx))
                return SimpleNamespace(hex=lambda: "0xdeadbeef")

        fake_web3 = SimpleNamespace(
            eth=FakeEth(),
            to_wei=lambda value, unit: int(value * 1_000_000_000),
        )
        executor = PrivateTxExecutor(
            enabled=True,
            rpc_url="https://private-rpc.test",
            timeout_sec=2.0,
            max_retries=1,
            wallet_private_key="0xabc",
            wallet_address="0x1111111111111111111111111111111111111111",
            bot_mode="live",
            web3_factory=lambda rpc_url, timeout_sec: fake_web3,
            sleep_fn=lambda seconds: None,
        )

        result = executor.execute(
            build_signal(
                metadata={
                    "tx_params": {
                        "to": "0x2222222222222222222222222222222222222222",
                        "data": "0xabcdef",
                    }
                }
            ),
            build_context(gas_price_gwei=14.0),
            build_policy(),
        )

        self.assertTrue(result.allow_trade)
        self.assertEqual(result.execution_mode, "private_tx")
        self.assertEqual(result.metadata["tx_hash"], "0xdeadbeef")
        self.assertEqual(sent_payloads, [b"\x12\x34"])


if __name__ == "__main__":
    unittest.main()
