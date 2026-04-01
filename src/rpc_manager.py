from __future__ import annotations

import time
from urllib.parse import urlsplit

from logger import log
from wallet import connect_web3


def normalize_rpc_urls(primary_url: str, urls: list[str] | None = None) -> list[str]:
    normalized: list[str] = []
    for raw_value in [primary_url, *(urls or [])]:
        candidate = str(raw_value).strip()
        if not candidate or candidate in normalized:
            continue
        normalized.append(candidate)
    return normalized


def mask_rpc_url(url: str) -> str:
    candidate = str(url).strip()
    if not candidate:
        return ""

    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return candidate.split("?")[0]


def classify_rpc_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "429" in message or "too many requests" in message or "rate limit" in message:
        return "rate_limit"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "connection" in message or "gateway" in message or "service unavailable" in message:
        return "connection"
    return "rpc_error"


def is_retryable_rpc_error(exc: Exception) -> bool:
    category = classify_rpc_error(exc)
    return category in {"rate_limit", "timeout", "connection"}


class RpcFailoverClient:
    def __init__(
        self,
        urls: list[str] | None,
        *,
        timeout_sec: float,
        label: str,
        client_factory=None,
        sleep_fn=None,
    ) -> None:
        self.urls = normalize_rpc_urls("", urls or [])
        self.timeout_sec = max(float(timeout_sec), 1.0)
        self.label = label
        self.client_factory = client_factory or connect_web3
        self.sleep_fn = sleep_fn or time.sleep
        self.current_index = 0
        self._clients: dict[str, object] = {}

    def is_available(self) -> bool:
        return bool(self.urls)

    def current_url(self) -> str:
        if not self.urls:
            return ""
        return self.urls[self.current_index]

    def current_endpoint_label(self) -> str:
        return mask_rpc_url(self.current_url())

    def endpoint_count(self) -> int:
        return len(self.urls)

    def client(self):
        if not self.urls:
            raise ValueError(f"{self.label}_rpc_unavailable")

        url = self.current_url()
        if url not in self._clients:
            self._clients[url] = self.client_factory(url, timeout_sec=self.timeout_sec)
        return self._clients[url]

    def rotate(self, reason: str = "") -> bool:
        if len(self.urls) <= 1:
            return False

        previous_endpoint = self.current_endpoint_label()
        self.current_index = (self.current_index + 1) % len(self.urls)
        log(
            f"{self.label} rpc failover | from {previous_endpoint or '-'} | "
            f"to {self.current_endpoint_label() or '-'} | reason {reason or 'retryable_error'}"
        )
        return True

    def perform(
        self,
        operation_name: str,
        operation,
        *,
        max_retries: int,
        backoff_sec: float,
    ):
        last_error: Exception | None = None
        total_attempts = max(int(max_retries), 0) + 1

        for attempt in range(total_attempts):
            endpoint_label = self.current_endpoint_label() or "-"
            try:
                return operation(self.client(), self.current_url())
            except Exception as exc:  # noqa: BLE001 - RPC failover must classify provider errors dynamically
                last_error = exc
                retryable = is_retryable_rpc_error(exc)
                reason = classify_rpc_error(exc)
                log(
                    f"{self.label} rpc error | operation {operation_name} | endpoint {endpoint_label} | "
                    f"attempt {attempt + 1}/{total_attempts} | retryable {int(retryable)} | error {exc}"
                )
                if attempt >= total_attempts - 1:
                    break
                if retryable:
                    self.rotate(reason)
                delay = max(float(backoff_sec), 0.0) * (attempt + 1)
                if delay > 0:
                    self.sleep_fn(delay)

        if last_error is None:
            raise RuntimeError(f"{self.label}_rpc_operation_failed")
        raise last_error
