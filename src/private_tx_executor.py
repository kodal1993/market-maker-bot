from __future__ import annotations

import time

from web3 import Web3

from config import (
    BOT_MODE,
    ENABLE_PRIVATE_TX,
    PRIVATE_RPC_URL,
    PRIVATE_RPC_URLS,
    PRIVATE_TX_MAX_RETRIES,
    PRIVATE_TX_TIMEOUT_SEC,
    TAKER_FEE_BPS,
    WALLET_ADDRESS,
    WALLET_PRIVATE_KEY,
)
from rpc_manager import RpcFailoverClient, mask_rpc_url
from types_bot import ExecutionContext, ExecutionPolicy, ExecutionResult, ExecutionSignal
from wallet import connect_web3


class PrivateTxExecutor:
    def __init__(
        self,
        enabled: bool = ENABLE_PRIVATE_TX,
        rpc_url: str = PRIVATE_RPC_URL,
        rpc_urls: list[str] | None = None,
        timeout_sec: float = PRIVATE_TX_TIMEOUT_SEC,
        max_retries: int = PRIVATE_TX_MAX_RETRIES,
        wallet_private_key: str = WALLET_PRIVATE_KEY,
        wallet_address: str = WALLET_ADDRESS,
        bot_mode: str = BOT_MODE,
        web3_factory=None,
        sleep_fn=None,
    ) -> None:
        self.enabled = enabled
        self.rpc_url = rpc_url
        self.timeout_sec = timeout_sec
        self.max_retries = max(max_retries, 0)
        self.wallet_private_key = wallet_private_key
        self.wallet_address = wallet_address
        self.bot_mode = bot_mode
        self.web3_factory = web3_factory or connect_web3
        self.sleep_fn = sleep_fn or time.sleep
        self.rpc_client = RpcFailoverClient(
            rpc_urls or ([rpc_url] if rpc_url else PRIVATE_RPC_URLS),
            timeout_sec=timeout_sec,
            label="private_tx",
            client_factory=self.web3_factory,
            sleep_fn=self.sleep_fn,
        )

    def _is_paper_mode(self) -> bool:
        return self.bot_mode.strip().lower().startswith("paper")

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self._is_paper_mode():
            return True
        return self.rpc_client.is_available()

    def _client(self):
        return self.rpc_client.client()

    def _resolve_payload(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
    ) -> tuple[str | None, dict[str, object]]:
        raw_tx_hex = context.metadata.get("raw_tx_hex") or signal.metadata.get("raw_tx_hex")
        tx_params = context.metadata.get("tx_params") or signal.metadata.get("tx_params") or {}
        return raw_tx_hex, dict(tx_params)

    def _normalize_tx_params(
        self,
        w3,
        tx_params: dict[str, object],
        context: ExecutionContext,
    ) -> dict[str, object]:
        normalized = dict(tx_params)
        from_address = str(normalized.get("from") or self.wallet_address or "").strip()
        if not from_address:
            raise ValueError("missing_wallet_address")

        checksum_from = Web3.to_checksum_address(from_address)
        normalized["from"] = checksum_from
        if "nonce" not in normalized:
            normalized["nonce"] = w3.eth.get_transaction_count(checksum_from, "pending")
        if "chainId" not in normalized:
            normalized["chainId"] = int(w3.eth.chain_id)
        if "gasPrice" not in normalized:
            if context.gas_price_gwei > 0:
                normalized["gasPrice"] = int(w3.to_wei(context.gas_price_gwei, "gwei"))
            else:
                normalized["gasPrice"] = int(w3.eth.gas_price)
        if "value" not in normalized:
            normalized["value"] = 0
        if "gas" not in normalized:
            try:
                normalized["gas"] = int(w3.eth.estimate_gas(normalized))
            except Exception:
                normalized["gas"] = 250_000

        to_address = normalized.get("to")
        if not to_address:
            raise ValueError("missing_private_tx_target")
        normalized["to"] = Web3.to_checksum_address(str(to_address))
        return normalized

    @staticmethod
    def _signed_raw_bytes(signed_tx) -> bytes:
        raw_tx = getattr(signed_tx, "raw_transaction", None)
        if raw_tx is None:
            raw_tx = getattr(signed_tx, "rawTransaction", None)
        if raw_tx is None:
            raise ValueError("missing_raw_transaction")
        return bytes(raw_tx)

    def _send_raw_transaction(self, w3, raw_tx: bytes | str) -> str:
        payload = raw_tx
        if isinstance(raw_tx, str):
            payload = Web3.to_bytes(hexstr=raw_tx)
        tx_hash = w3.eth.send_raw_transaction(payload)
        return tx_hash.hex()

    def _live_execute(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
    ) -> tuple[bool, str, dict[str, object]]:
        raw_tx_hex, tx_params = self._resolve_payload(signal, context)
        if raw_tx_hex is None and not tx_params:
            return False, "missing_private_tx_payload", {}
        if raw_tx_hex is None and not self.wallet_private_key:
            return False, "missing_wallet_private_key", {}

        def execute_once(w3, rpc_url: str) -> dict[str, object]:
            if raw_tx_hex is not None:
                tx_hash = self._send_raw_transaction(w3, raw_tx_hex)
            else:
                normalized_tx = self._normalize_tx_params(w3, tx_params, context)
                signed = w3.eth.account.sign_transaction(
                    normalized_tx,
                    private_key=self.wallet_private_key,
                )
                tx_hash = self._send_raw_transaction(w3, self._signed_raw_bytes(signed))
            return {
                "tx_hash": tx_hash,
                "rpc_endpoint": mask_rpc_url(rpc_url),
            }

        try:
            metadata = self.rpc_client.perform(
                "private_tx_send",
                execute_once,
                max_retries=self.max_retries,
                backoff_sec=1.0,
            )
            return True, "", metadata
        except Exception as exc:
            return False, "private_tx_send_failed", {"errors": [str(exc)]}

    def execute(
        self,
        signal: ExecutionSignal,
        context: ExecutionContext,
        policy: ExecutionPolicy,
    ) -> ExecutionResult:
        if not policy.allow_private_tx:
            return ExecutionResult(
                allow_trade=False,
                execution_mode="skip",
                private_tx_used=False,
                cow_used=False,
                quoted_price=context.router_price,
                order_price=0.0,
                size_usd=signal.size_usd,
                fee_bps=0.0,
                execution_type="maker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
                trade_blocked_reason="private_tx_policy_disabled",
            )

        if not self.is_available():
            return ExecutionResult(
                allow_trade=False,
                execution_mode="skip",
                private_tx_used=False,
                cow_used=False,
                quoted_price=context.router_price,
                order_price=0.0,
                size_usd=signal.size_usd,
                fee_bps=0.0,
                execution_type="maker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
                trade_blocked_reason="private_tx_unavailable",
            )

        metadata = {
            "timeout_sec": self.timeout_sec,
            "max_retries": self.max_retries,
            "paper_mode": self._is_paper_mode(),
            "rpc_configured": self.rpc_client.is_available(),
            "rpc_endpoint_count": self.rpc_client.endpoint_count(),
        }
        if self._is_paper_mode():
            metadata["paper_private_tx_simulation"] = True
            return ExecutionResult(
                allow_trade=True,
                execution_mode="private_tx",
                private_tx_used=True,
                cow_used=False,
                quoted_price=context.router_price,
                order_price=context.router_price,
                size_usd=signal.size_usd,
                fee_bps=TAKER_FEE_BPS,
                execution_type="taker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
                metadata=metadata,
            )

        success, block_reason, execution_metadata = self._live_execute(signal, context)
        metadata.update(execution_metadata)
        if not success:
            return ExecutionResult(
                allow_trade=False,
                execution_mode="skip",
                private_tx_used=False,
                cow_used=False,
                quoted_price=context.router_price,
                order_price=0.0,
                size_usd=signal.size_usd,
                fee_bps=0.0,
                execution_type="maker",
                mev_risk_score=0.0,
                sandwich_risk=0.0,
                execution_window_score=0.0,
                expected_slippage_bps=0.0,
                realized_slippage_bps=0.0,
                price_impact_bps=0.0,
                quote_deviation_bps=0.0,
                trade_blocked_reason=block_reason,
                metadata=metadata,
            )

        return ExecutionResult(
            allow_trade=True,
            execution_mode="private_tx",
            private_tx_used=True,
            cow_used=False,
            quoted_price=context.router_price,
            order_price=context.router_price,
            size_usd=signal.size_usd,
            fee_bps=TAKER_FEE_BPS,
            execution_type="taker",
            mev_risk_score=0.0,
            sandwich_risk=0.0,
            execution_window_score=0.0,
            expected_slippage_bps=0.0,
            realized_slippage_bps=0.0,
            price_impact_bps=0.0,
            quote_deviation_bps=0.0,
            metadata=metadata,
        )
