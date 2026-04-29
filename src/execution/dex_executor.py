from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from web3 import Web3

from logger import log


# Base mainnet canonical token addresses.
WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fCD6EDB6E08f4c7C32D4f71b54bdA02913"


@dataclass(frozen=True)
class QuoteResult:
    """Container returned by get_quote with execution-relevant estimations."""

    expected_output: float
    gas_units: int
    gas_price_gwei: float
    gas_cost_eth: float


class DexExecutor:
    """Paper-mode DEX executor for ETH/USDC swap simulations on Base.

    Notes:
    - This class intentionally does NOT broadcast transactions.
    - Web3 is used for RPC connectivity and market/gas context only.
    - Quote math is a lightweight paper approximation (price * amount,
      optional fee/slippage assumptions) to keep the module deterministic.
    """

    def __init__(
        self,
        rpc_url: str | None = None,
        *,
        paper_mode: bool = True,
        default_slippage_bps: float = 30.0,
        assumed_pool_fee_bps: float = 5.0,
        default_gas_units: int = 220_000,
    ) -> None:
        self.rpc_url = (rpc_url or os.getenv("BASE_RPC_URL") or "").strip()
        if not self.rpc_url:
            raise ValueError("BASE_RPC_URL is required for DexExecutor")

        self.paper_mode = paper_mode
        self.default_slippage_bps = max(default_slippage_bps, 0.0)
        self.assumed_pool_fee_bps = max(assumed_pool_fee_bps, 0.0)
        self.default_gas_units = max(int(default_gas_units), 21_000)

        self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self.web3.is_connected():
            raise ConnectionError("Failed to connect to BASE_RPC_URL")

        self.weth = Web3.to_checksum_address(WETH_BASE)
        self.usdc = Web3.to_checksum_address(USDC_BASE)

    def _validate_pair(self, input_token: str, output_token: str) -> tuple[str, str]:
        in_token = Web3.to_checksum_address(input_token)
        out_token = Web3.to_checksum_address(output_token)
        supported = {self.weth, self.usdc}
        if in_token not in supported or out_token not in supported or in_token == out_token:
            raise ValueError("Only ETH/USDC pair is supported in paper mode")
        return in_token, out_token

    def _fetch_eth_price_usdc(self) -> float:
        """Fetches a lightweight mark price using latest block base fee context.

        For now we keep a stable approximation if no external oracle/router call is wired.
        This makes simulation deterministic and test-friendly.
        """
        # Future extension point: hook Uniswap Quoter/Cow quote endpoint.
        return 3000.0

    def get_quote(self, input_token: str, output_token: str, amount: float) -> QuoteResult:
        """Return expected output amount and gas estimate for a simulated swap."""
        if amount <= 0:
            raise ValueError("amount must be > 0")

        in_token, out_token = self._validate_pair(input_token, output_token)
        eth_price_usdc = self._fetch_eth_price_usdc()

        amount_dec = Decimal(str(amount))
        if in_token == self.weth and out_token == self.usdc:
            gross_output = amount_dec * Decimal(str(eth_price_usdc))
        else:
            gross_output = amount_dec / Decimal(str(eth_price_usdc))

        total_fee_bps = Decimal(str(self.assumed_pool_fee_bps + self.default_slippage_bps))
        net_multiplier = Decimal("1") - (total_fee_bps / Decimal("10000"))
        expected_output = float(gross_output * net_multiplier)

        gas_price_wei = int(self.web3.eth.gas_price)
        gas_price_gwei = float(Web3.from_wei(gas_price_wei, "gwei"))
        gas_cost_eth = float((Decimal(gas_price_wei) * Decimal(self.default_gas_units)) / Decimal(10**18))

        return QuoteResult(
            expected_output=expected_output,
            gas_units=self.default_gas_units,
            gas_price_gwei=gas_price_gwei,
            gas_cost_eth=gas_cost_eth,
        )

    def calculate_min_profit_after_gas(
        self,
        expected_profit_usd: float,
        gas_cost_eth: float,
        *,
        eth_price_usdc: float | None = None,
    ) -> tuple[bool, float]:
        """Check whether expected USD profit remains positive after gas cost.

        Returns (is_profitable, net_profit_usd).
        """
        mark_price = eth_price_usdc if eth_price_usdc and eth_price_usdc > 0 else self._fetch_eth_price_usdc()
        gas_cost_usd = float(Decimal(str(gas_cost_eth)) * Decimal(str(mark_price)))
        net_profit_usd = float(Decimal(str(expected_profit_usd)) - Decimal(str(gas_cost_usd)))
        return net_profit_usd > 0, net_profit_usd

    def execute_swap(
        self,
        input_token: str,
        output_token: str,
        amount: float,
        *,
        expected_profit_usd: float,
    ) -> dict[str, object]:
        """Simulate swap execution in paper mode and write detailed logs."""
        if not self.paper_mode:
            raise RuntimeError("Live execution is not supported yet. Use paper_mode=True.")

        quote = self.get_quote(input_token, output_token, amount)
        profitable, net_profit_usd = self.calculate_min_profit_after_gas(
            expected_profit_usd=expected_profit_usd,
            gas_cost_eth=quote.gas_cost_eth,
        )

        gas_cost_usd = expected_profit_usd - net_profit_usd
        simulation = {
            "status": "simulated",
            "input_token": Web3.to_checksum_address(input_token),
            "output_token": Web3.to_checksum_address(output_token),
            "input_amount": float(amount),
            "expected_output": quote.expected_output,
            "expected_profit_usd": float(expected_profit_usd),
            "gas_units": quote.gas_units,
            "gas_price_gwei": quote.gas_price_gwei,
            "gas_cost_eth": quote.gas_cost_eth,
            "gas_cost_usd": float(gas_cost_usd),
            "net_profit_usd": net_profit_usd,
            "is_profitable_after_gas": profitable,
        }

        log(
            "paper_swap_simulation "
            f"input={simulation['input_amount']} output={simulation['expected_output']:.6f} "
            f"expected_profit_usd={simulation['expected_profit_usd']:.6f} "
            f"gas_cost_usd={simulation['gas_cost_usd']:.6f} "
            f"net_profit_usd={simulation['net_profit_usd']:.6f} "
            f"profitable={simulation['is_profitable_after_gas']}"
        )

        return simulation
