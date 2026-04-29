from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from typing import Any

from web3 import Web3


UNISWAP_V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "int256"},
            {"indexed": False, "name": "amount1", "type": "int256"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
        "name": "Swap",
        "type": "event",
    },
]


@dataclass(slots=True)
class PoolMonitorConfig:
    rpc_url: str
    pool_address: str
    token0_decimals: int = 18
    token1_decimals: int = 6
    token0_is_eth: bool = True
    abi: list[dict[str, Any]] | None = None
    chain_id: int | None = 8453
    block_time_seconds: int = 2
    paper_mode: bool = True


class PoolMonitor:
    """ETH/USDC pool monitor Base chainre, paper és live kompatibilitással."""

    def __init__(self, config: PoolMonitorConfig) -> None:
        self.config = config
        self.web3 = Web3(Web3.HTTPProvider(config.rpc_url))
        self.pool_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(config.pool_address),
            abi=config.abi or UNISWAP_V3_POOL_ABI,
        )

    def get_current_price(self) -> float:
        """Aktuális ETH/USDC ár (USDC per ETH)."""
        slot0 = self.pool_contract.functions.slot0().call()
        sqrt_price_x96 = slot0[0]
        raw_price = (sqrt_price_x96**2) / (2**192)
        price_token1_per_token0 = raw_price * (10 ** (self.config.token0_decimals - self.config.token1_decimals))

        if self.config.token0_is_eth:
            return float(price_token1_per_token0)
        if price_token1_per_token0 <= 0:
            raise ValueError("invalid_pool_price")
        return float(1 / price_token1_per_token0)

    def get_pool_liquidity(self) -> float:
        """Pool likviditás (V3 liquidity + becsült USDC notional)."""
        liquidity_raw = float(self.pool_contract.functions.liquidity().call())
        price = self.get_current_price()
        if liquidity_raw <= 0 or price <= 0:
            return 0.0

        # Egyszerű, paper módra stabil becslés: sqrt(k) ~ liquidity, notional ~ 2*L*sqrt(P)
        liquidity_notional = 2.0 * liquidity_raw * math.sqrt(price) / (10 ** self.config.token1_decimals)
        return float(liquidity_notional)

    def get_recent_volume(self, timeframe_minutes: int = 60) -> float:
        """Swap volume becslése USD-ben adott időablakra."""
        timeframe_minutes = max(int(timeframe_minutes), 1)
        current_block = self.web3.eth.block_number
        blocks_back = max(int((timeframe_minutes * 60) / self.config.block_time_seconds), 1)
        from_block = max(current_block - blocks_back, 0)

        logs = self.pool_contract.events.Swap.get_logs(from_block=from_block, to_block=current_block)
        volume_usd = 0.0

        for event in logs:
            amount0 = abs(int(event["args"]["amount0"])) / (10 ** self.config.token0_decimals)
            amount1 = abs(int(event["args"]["amount1"])) / (10 ** self.config.token1_decimals)
            # ETH/USDC párnál az USDC oldal közvetlenül USD notional.
            volume_usd += max(amount1, amount0 * self.get_current_price())

        return float(volume_usd)

    def get_volatility(self, lookback_minutes: int = 60, sample_count: int = 30) -> float:
        """Rövid távú volatilitás (log-return stddev) becslése."""
        lookback_minutes = max(int(lookback_minutes), 5)
        sample_count = max(int(sample_count), 5)

        current_block = self.web3.eth.block_number
        total_blocks = max(int((lookback_minutes * 60) / self.config.block_time_seconds), sample_count)
        step = max(total_blocks // sample_count, 1)

        prices: list[float] = []
        for i in range(sample_count):
            block_number = max(current_block - (sample_count - i) * step, 0)
            try:
                slot0 = self.pool_contract.functions.slot0().call(block_identifier=block_number)
            except Exception:
                continue
            sqrt_price_x96 = slot0[0]
            raw_price = (sqrt_price_x96**2) / (2**192)
            price = raw_price * (10 ** (self.config.token0_decimals - self.config.token1_decimals))
            if self.config.token0_is_eth:
                prices.append(float(price))
            elif price > 0:
                prices.append(float(1 / price))

        if len(prices) < 2:
            return 0.0

        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0 and prices[i] > 0]
        if len(returns) < 2:
            return 0.0
        return float(statistics.pstdev(returns))


if __name__ == "__main__":
    monitor = PoolMonitor(
        PoolMonitorConfig(
            rpc_url="https://mainnet.base.org",
            pool_address="0xd0b53D9277642d899DF5C87A3966A349A798F224",
            token0_decimals=18,
            token1_decimals=6,
            token0_is_eth=True,
            paper_mode=True,
        )
    )

    print({
        "price": monitor.get_current_price(),
        "liquidity": monitor.get_pool_liquidity(),
        "volume_1h": monitor.get_recent_volume(60),
        "volatility": monitor.get_volatility(),
        "ts": int(time.time()),
    })
