from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dex_client import DexClient
from private_tx_executor import PrivateTxExecutor
from rpc_manager import RpcFailoverClient
from types_bot import ExecutionContext, ExecutionPolicy, ExecutionSignal


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
        "metadata": {},
    }
    values.update(overrides)
    return ExecutionPolicy(**values)


def sqrt_price_x96_for_price(price: float) -> int:
    raw_price = price / (10 ** 12)
    return int(math.sqrt(raw_price) * (2 ** 96))


class RpcFailoverTests(unittest.TestCase):
    def test_rpc_failover_client_switches_on_retryable_error(self) -> None:
        attempts: list[str] = []
        pool = RpcFailoverClient(
            ["https://rpc-a.example", "https://rpc-b.example"],
            timeout_sec=1.0,
            label="test_rpc",
            client_factory=lambda url, timeout_sec: {"url": url, "timeout": timeout_sec},
            sleep_fn=lambda seconds: None,
        )

        def operation(client, rpc_url: str) -> str:
            del client
            attempts.append(rpc_url)
            if rpc_url.endswith("rpc-a.example"):
                raise RuntimeError("429 too many requests")
            return "ok"

        result = pool.perform("sample_call", operation, max_retries=1, backoff_sec=0.0)

        self.assertEqual(result, "ok")
        self.assertEqual(
            attempts,
            ["https://rpc-a.example", "https://rpc-b.example"],
        )

    def test_dex_client_uses_next_rpc_after_timeout(self) -> None:
        calls: list[str] = []

        class FakeCall:
            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

            def call(self):
                calls.append(self.rpc_url)
                if self.rpc_url.endswith("primary.example"):
                    raise RuntimeError("request timed out")
                return (sqrt_price_x96_for_price(100.0), 0, 0, 0, 0, 0, False)

        class FakeFunctions:
            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

            def slot0(self):
                return FakeCall(self.rpc_url)

        class FakeEth:
            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

            def contract(self, address, abi):
                del address, abi
                return SimpleNamespace(functions=FakeFunctions(self.rpc_url))

        def web3_factory(rpc_url: str, timeout_sec: float):
            del timeout_sec
            return SimpleNamespace(eth=FakeEth(rpc_url))

        client = DexClient(
            rpc_urls=["https://primary.example", "https://secondary.example"],
            timeout_sec=1.0,
            max_retries=1,
            retry_backoff_sec=0.0,
            price_cache_seconds=0.0,
            web3_factory=web3_factory,
            sleep_fn=lambda seconds: None,
            time_fn=lambda: 1000.0,
        )

        price, source = client.get_price()

        self.assertAlmostEqual(price, 100.0, places=4)
        self.assertEqual(
            calls,
            ["https://primary.example", "https://secondary.example"],
        )
        self.assertEqual(source, "rpc:https://secondary.example")

    def test_private_tx_executor_switches_rpc_after_timeout(self) -> None:
        attempts: list[str] = []

        class FakeAccount:
            @staticmethod
            def sign_transaction(tx_params, private_key):
                self.assertEqual(private_key, "0xabc")
                self.assertEqual(tx_params["nonce"], 7)
                return SimpleNamespace(raw_transaction=b"\xaa\xbb")

        class FakeEth:
            account = FakeAccount()
            chain_id = 8453
            gas_price = 12_000_000_000

            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

            @staticmethod
            def get_transaction_count(address, block_identifier):
                self.assertEqual(block_identifier, "pending")
                return 7

            @staticmethod
            def estimate_gas(tx_params):
                del tx_params
                return 210_000

            def send_raw_transaction(self, raw_tx):
                attempts.append(self.rpc_url)
                del raw_tx
                if self.rpc_url.endswith("private-a.example"):
                    raise RuntimeError("timeout while sending raw transaction")
                return SimpleNamespace(hex=lambda: "0xbeef")

        def web3_factory(rpc_url: str, timeout_sec: float):
            del timeout_sec
            return SimpleNamespace(
                eth=FakeEth(rpc_url),
                to_wei=lambda value, unit: int(value * 1_000_000_000),
            )

        executor = PrivateTxExecutor(
            enabled=True,
            rpc_url="",
            rpc_urls=["https://private-a.example", "https://private-b.example"],
            timeout_sec=2.0,
            max_retries=1,
            wallet_private_key="0xabc",
            wallet_address="0x1111111111111111111111111111111111111111",
            bot_mode="live",
            web3_factory=web3_factory,
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
        self.assertEqual(result.metadata["tx_hash"], "0xbeef")
        self.assertEqual(
            attempts,
            ["https://private-a.example", "https://private-b.example"],
        )


if __name__ == "__main__":
    unittest.main()
