from __future__ import annotations

import time

from web3 import Web3

from config import PRICE_CACHE_SECONDS, RPC_MAX_RETRIES, RPC_RETRY_BACKOFF_SEC, RPC_TIMEOUT_SEC, RPC_URLS
from logger import log
from rpc_manager import RpcFailoverClient, mask_rpc_url


POOL_ADDRESS = "0xd0b53D9277642d899DF5C87A3966A349A798F224"
TOKEN0_DECIMALS = 18
TOKEN1_DECIMALS = 6
TOKEN0_IS_WETH = True

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "", "type": "uint16"},
            {"name": "", "type": "uint16"},
            {"name": "", "type": "uint16"},
            {"name": "", "type": "uint8"},
            {"name": "", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


class DexClient:
    def __init__(
        self,
        *,
        rpc_urls: list[str] | None = None,
        timeout_sec: float = RPC_TIMEOUT_SEC,
        max_retries: int = RPC_MAX_RETRIES,
        retry_backoff_sec: float = RPC_RETRY_BACKOFF_SEC,
        price_cache_seconds: float = PRICE_CACHE_SECONDS,
        web3_factory=None,
        sleep_fn=None,
        time_fn=None,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.max_retries = max(int(max_retries), 0)
        self.retry_backoff_sec = max(float(retry_backoff_sec), 0.0)
        self.price_cache_seconds = max(float(price_cache_seconds), 0.0)
        self.sleep_fn = sleep_fn or time.sleep
        self.time_fn = time_fn or time.time
        self.rpc_client = RpcFailoverClient(
            rpc_urls or RPC_URLS,
            timeout_sec=timeout_sec,
            label="market_data",
            client_factory=web3_factory,
            sleep_fn=self.sleep_fn,
        )
        self.pool_address = Web3.to_checksum_address(POOL_ADDRESS)
        self._pool_contracts: dict[str, object] = {}
        self.last_price: float | None = None
        self.last_price_ts = 0.0

    def _pool_for_client(self, w3, rpc_url: str):
        key = mask_rpc_url(rpc_url) or str(id(w3))
        if key not in self._pool_contracts:
            self._pool_contracts[key] = w3.eth.contract(
                address=self.pool_address,
                abi=POOL_ABI,
            )
        return self._pool_contracts[key]

    def _fetch_price(self, w3, rpc_url: str) -> tuple[float, str]:
        slot0 = self._pool_for_client(w3, rpc_url).functions.slot0().call()
        sqrt_price_x96 = slot0[0]
        raw_price = (sqrt_price_x96**2) / (2**192)
        price_token1_per_token0 = raw_price * (10 ** (TOKEN0_DECIMALS - TOKEN1_DECIMALS))

        if TOKEN0_IS_WETH:
            price = price_token1_per_token0
        else:
            if price_token1_per_token0 <= 0:
                raise ValueError("invalid_pool_price")
            price = 1 / price_token1_per_token0

        return float(price), mask_rpc_url(rpc_url)

    def get_price(self) -> tuple[float, str]:
        now = self.time_fn()
        if self.last_price is not None and (now - self.last_price_ts) < self.price_cache_seconds:
            return self.last_price, "cache"

        try:
            price, endpoint = self.rpc_client.perform(
                "slot0_price",
                self._fetch_price,
                max_retries=self.max_retries,
                backoff_sec=self.retry_backoff_sec,
            )
            self.last_price = price
            self.last_price_ts = self.time_fn()
            return self.last_price, f"rpc:{endpoint or 'active'}"
        except Exception as exc:
            if self.last_price is not None:
                log(f"market_data rpc stale fallback | error {exc}")
                return self.last_price, "stale_cache"
            raise
