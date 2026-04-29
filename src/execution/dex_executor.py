from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any

from web3 import Web3


# Minimal ABI for Uniswap V3 QuoterV2 quoteExactInputSingle
QUOTER_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


logger = logging.getLogger(__name__)


class DexExecutor:
    """Paper-mode executor for ETH/USDC simulation on Base via Uniswap V3 Quoter."""

    def __init__(self) -> None:
        # Environment-driven configuration
        self.rpc_url = os.getenv("BASE_RPC_URL", "").strip()
        self.quoter_address = os.getenv("UNISWAP_V3_QUOTER", "").strip()
        self.weth_address = os.getenv("WETH_ADDRESS", "0x4200000000000000000000000000000000000006").strip()
        self.usdc_address = os.getenv("USDC_ADDRESS", "0x833589fCD6EDB6E08f4c7C32D4f71b54bdA02913").strip()
        self.pool_fee = int(os.getenv("UNISWAP_V3_POOL_FEE", "500"))
        self.slippage_tolerance = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))
        self.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.default_gas_estimate = int(os.getenv("DEFAULT_SWAP_GAS_ESTIMATE", "230000"))

        if not self.rpc_url:
            raise ValueError("Missing required env var: BASE_RPC_URL")
        if not self.quoter_address:
            raise ValueError("Missing required env var: UNISWAP_V3_QUOTER")

        self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.web3.is_connected():
            raise ConnectionError("Unable to connect to Base RPC via BASE_RPC_URL")

        self.weth = Web3.to_checksum_address(self.weth_address)
        self.usdc = Web3.to_checksum_address(self.usdc_address)
        self.quoter = self.web3.eth.contract(
            address=Web3.to_checksum_address(self.quoter_address),
            abi=QUOTER_ABI,
        )

    def _to_wei_amount(self, amount_in: float, token: str) -> int:
        """Convert human-readable amount into token base units."""
        decimals = 18 if Web3.to_checksum_address(token) == self.weth else 6
        return int(Decimal(str(amount_in)) * (Decimal(10) ** decimals))

    def _from_base_units(self, amount: int, token: str) -> float:
        """Convert token base units to human-readable amount."""
        decimals = 18 if Web3.to_checksum_address(token) == self.weth else 6
        return float(Decimal(amount) / (Decimal(10) ** decimals))

    async def get_quote(self, amount_in: int, token_in: str, token_out: str) -> dict[str, float | int]:
        """Query Uniswap V3 Quoter for expected output and gas estimate."""
        token_in_ck = Web3.to_checksum_address(token_in)
        token_out_ck = Web3.to_checksum_address(token_out)

        params = (token_in_ck, token_out_ck, int(amount_in), int(self.pool_fee), 0)

        try:
            quote_result = await asyncio.to_thread(
                self.quoter.functions.quoteExactInputSingle(params).call
            )
            amount_out, _sqrt_after, _ticks_crossed, gas_estimate = quote_result
            return {
                "amount_in": int(amount_in),
                "amount_out": int(amount_out),
                "gas_estimate": int(gas_estimate),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Quoter RPC call failed: %s", exc)
            raise

    def calculate_gas_cost(self, gas_estimate: int) -> float:
        """Estimate gas cost in USD using on-chain gas price and quoted ETH/USDC."""
        try:
            gas_price_wei = int(self.web3.eth.gas_price)
            gas_cost_eth = Decimal(gas_estimate) * Decimal(gas_price_wei) / Decimal(10**18)

            # 1 ETH -> USDC quote (WETH -> USDC)
            one_eth_wei = 10**18
            eth_usdc_quote = self.quoter.functions.quoteExactInputSingle(
                (self.weth, self.usdc, one_eth_wei, int(self.pool_fee), 0)
            ).call()
            eth_price_usdc = Decimal(eth_usdc_quote[0]) / Decimal(10**6)
            return float(gas_cost_eth * eth_price_usdc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gas cost estimation failed, using fallback: %s", exc)
            # Conservative fallback if RPC fails
            fallback_eth_price = Decimal(os.getenv("FALLBACK_ETH_USDC", "3000"))
            fallback_gwei = Decimal(os.getenv("FALLBACK_GAS_GWEI", "0.15"))
            gas_cost_eth = Decimal(gas_estimate) * fallback_gwei / Decimal(10**9)
            return float(gas_cost_eth * fallback_eth_price)

    async def simulate_swap(self, amount_in: float, is_buy: bool) -> dict[str, float | bool | str | int]:
        """Simulate ETH/USDC swap in paper mode and evaluate profitability."""
        if amount_in <= 0:
            raise ValueError("amount_in must be positive")

        if is_buy:
            token_in, token_out = self.usdc, self.weth
            side = "BUY"
            print(f"Simulating BUY ETH with USDC... amount={amount_in}")
        else:
            token_in, token_out = self.weth, self.usdc
            side = "SELL"
            print(f"Simulating SELL ETH for USDC... amount={amount_in}")

        amount_in_base = self._to_wei_amount(amount_in, token_in)

        quote = await self.get_quote(amount_in_base, token_in, token_out)
        expected_output_raw = int(quote["amount_out"])
        gas_estimate = int(quote.get("gas_estimate", self.default_gas_estimate)) or self.default_gas_estimate
        expected_output = self._from_base_units(expected_output_raw, token_out)

        slippage_amount = expected_output * self.slippage_tolerance
        min_output_after_slippage = expected_output - slippage_amount

        gas_cost_usd = self.calculate_gas_cost(gas_estimate)

        # Paper PnL approximation: slippage impact treated as execution loss; gas explicit.
        if is_buy:
            pnl_before_gas = -slippage_amount
        else:
            try:
                pnl_before_gas = -float(
                    Decimal(str(slippage_amount))
                    * Decimal(str(min_output_after_slippage))
                )
            except (InvalidOperation, ValueError):
                pnl_before_gas = -slippage_amount

        expected_profit_after_gas = pnl_before_gas - gas_cost_usd
        is_profitable = expected_profit_after_gas > 0

        print(
            f"Expected output: {expected_output:.8f} | Gas cost: ${gas_cost_usd:.6f} "
            f"| Profit after gas: ${expected_profit_after_gas:.6f}"
        )

        return {
            "mode": "paper",
            "side": side,
            "input_amount": float(amount_in),
            "input_token": token_in,
            "output_token": token_out,
            "expected_output": float(expected_output),
            "gas_estimate": int(gas_estimate),
            "slippage": float(slippage_amount),
            "min_output_after_slippage": float(min_output_after_slippage),
            "gas_cost_usd": float(gas_cost_usd),
            "expected_profit_after_gas": float(expected_profit_after_gas),
            "is_profitable": bool(is_profitable),
        }

    async def execute_swap(self, amount_in: float, is_buy: bool) -> dict[str, float | bool | str | int]:
        """Execute swap entrypoint; in paper mode only simulation is performed."""
        if self.paper_trading:
            logger.info("PAPER_TRADING=true, simulating only (no real transaction sent).")
            return await self.simulate_swap(amount_in=amount_in, is_buy=is_buy)

        raise RuntimeError("Live swap execution is intentionally disabled in this module.")
