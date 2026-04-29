from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from statistics import mean
from typing import Any

from web3 import Web3

logger = logging.getLogger(__name__)

UNISWAP_V3_FACTORY_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

UNISWAP_V3_POOL_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "recipient", "type": "address"},
            {"indexed": False, "internalType": "int256", "name": "amount0", "type": "int256"},
            {"indexed": False, "internalType": "int256", "name": "amount1", "type": "int256"},
            {"indexed": False, "internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"indexed": False, "internalType": "int24", "name": "tick", "type": "int24"},
        ],
        "name": "Swap",
        "type": "event",
    },
]


class PoolMonitor:
    """Realtime Uniswap V3 ETH/USDC pool monitor for Base chain (paper mode compatible)."""

    def __init__(self, fee_tier: int = 500, block_time_seconds: int = 2) -> None:
        self.rpc_url = os.getenv("BASE_RPC_URL", "https://mainnet.base.org").strip()
        self.weth_address = Web3.to_checksum_address(
            os.getenv("WETH_ADDRESS", "0x4200000000000000000000000000000000000006").strip()
        )
        self.usdc_address = Web3.to_checksum_address(
            os.getenv("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913").strip()
        )
        self.uniswap_v3_factory = Web3.to_checksum_address(
            os.getenv("UNISWAP_V3_FACTORY", "0x33128a8fC17869897dcE68Ed026d694621f6FDfD").strip()
        )
        self.fee_tier = int(os.getenv("UNISWAP_V3_POOL_FEE", str(fee_tier)))
        self.block_time_seconds = int(block_time_seconds)
        self.min_interval_seconds = float(os.getenv("POOL_MONITOR_MIN_INTERVAL_SECONDS", "8"))
        self._last_successful_pool_info: dict[str, Any] | None = None
        self._last_successful_at: float = 0.0
        self._last_rpc_error: str | None = None

        self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.factory = self.web3.eth.contract(address=self.uniswap_v3_factory, abi=UNISWAP_V3_FACTORY_ABI)

        self.pool_address = self._resolve_pool_address()
        self.pool = self.web3.eth.contract(address=self.pool_address, abi=UNISWAP_V3_POOL_ABI)

        logger.info("PoolMonitor initialized for pool: %s", self.pool_address)

    def _resolve_pool_address(self) -> str:
        pool_address = self.factory.functions.getPool(
            self.weth_address,
            self.usdc_address,
            int(self.fee_tier),
        ).call()
        if int(pool_address, 16) == 0:
            raise ValueError("No Uniswap V3 pool found for WETH/USDC on selected fee tier")
        return Web3.to_checksum_address(pool_address)

    async def _safe_rpc_call(self, fn: Any, default: Any = None) -> Any:
        try:
            self._last_rpc_error = None
            return await asyncio.to_thread(fn)
        except Exception as exc:  # noqa: BLE001
            self._last_rpc_error = str(exc)
            logger.exception("RPC call failed: %s", exc)
            return default

    def _tick_to_price_usdc_per_eth(self, tick: int) -> float:
        # price(token1/token0)=1.0001^tick; adjust decimals token0=WETH(18), token1=USDC(6)
        raw_price = math.pow(1.0001, tick)
        return raw_price * math.pow(10, 18 - 6)

    async def get_current_price(self) -> float | None:
        slot0 = await self._safe_rpc_call(self.pool.functions.slot0().call)
        if not slot0:
            return None

        tick = int(slot0[1])
        price = self._tick_to_price_usdc_per_eth(tick)
        logger.info("Current ETH/USDC price: %.2f USDC", price)
        return price

    async def get_pool_liquidity(self) -> float | None:
        liquidity_raw = await self._safe_rpc_call(self.pool.functions.liquidity().call)
        if liquidity_raw is None:
            return None

        price = await self.get_current_price()
        if not price:
            return float(liquidity_raw)

        # Simplified ETH-equivalent approximation for monitoring dashboards.
        liquidity_eth_equiv = float(liquidity_raw) / 1e18
        logger.info("Pool liquidity: %.2f ETH equivalent", liquidity_eth_equiv)
        return liquidity_eth_equiv

    async def get_recent_volume(self, minutes: int = 60) -> float | None:
        latest_block = await self._safe_rpc_call(lambda: self.web3.eth.block_number)
        if latest_block is None:
            return None

        lookback_blocks = max(1, int((minutes * 60) / max(1, self.block_time_seconds)))
        from_block = max(0, int(latest_block) - lookback_blocks)

        def _fetch_logs() -> list[dict[str, Any]]:
            event = self.pool.events.Swap()
            return event.get_logs(from_block=from_block, to_block=latest_block)

        logs = await self._safe_rpc_call(_fetch_logs, default=[])
        if logs is None:
            return None

        usdc_volume = 0.0
        for log in logs:
            amount1 = float(abs(log["args"]["amount1"]))
            usdc_volume += amount1 / 1e6

        logger.info("Recent swap volume (%sm): %.2f USDC", minutes, usdc_volume)
        return usdc_volume

    async def get_volatility(self, period_minutes: int = 30) -> float | None:
        latest_block = await self._safe_rpc_call(lambda: self.web3.eth.block_number)
        if latest_block is None:
            return None

        lookback_blocks = max(2, int((period_minutes * 60) / max(1, self.block_time_seconds)))
        from_block = max(0, int(latest_block) - lookback_blocks)

        def _collect_ticks() -> list[int]:
            ticks: list[int] = []
            for block_number in range(from_block, int(latest_block) + 1, max(1, lookback_blocks // 20)):
                block_identifier = min(block_number, int(latest_block))
                slot0 = self.pool.functions.slot0().call(block_identifier=block_identifier)
                ticks.append(int(slot0[1]))
            return ticks

        ticks = await self._safe_rpc_call(_collect_ticks, default=[])
        if not ticks:
            return None

        prices = [self._tick_to_price_usdc_per_eth(tick) for tick in ticks]
        avg_price = mean(prices)
        if avg_price <= 0:
            return None

        pct_change = ((max(prices) - min(prices)) / avg_price) * 100.0
        logger.info("Volatility (%sm): %.2f%%", period_minutes, pct_change)
        return pct_change

    async def get_pool_info(self) -> dict[str, Any]:
        now = time.time()
        if (
            self._last_successful_pool_info is not None
            and (now - self._last_successful_at) < self.min_interval_seconds
        ):
            cached = dict(self._last_successful_pool_info)
            cached["cached"] = True
            return cached

        started_at = time.time()

        price, liquidity, volatility, recent_volume = await asyncio.gather(
            self.get_current_price(),
            self.get_pool_liquidity(),
            self.get_volatility(),
            self.get_recent_volume(),
        )

        if price is None:
            payload: dict[str, Any] = {
                "status": "rpc_error",
                "error_reason": self._last_rpc_error or "price_unavailable",
                "pool_fee": int(self.fee_tier),
                "cached": False,
            }
            logger.warning("Pool info fetch failed in %.2fs: %s", time.time() - started_at, payload)
            return payload

        payload = {
            "status": "ok",
            "price": price,
            "liquidity": liquidity,
            "volatility": volatility,
            "recent_volume": recent_volume,
            "pool_fee": int(self.fee_tier),
            "cached": False,
        }
        self._last_successful_pool_info = dict(payload)
        self._last_successful_at = now

        logger.info("Pool info fetched in %.2fs: %s", time.time() - started_at, payload)
        return payload
