from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from config import (
    COW_MIN_NOTIONAL_USD,
    COW_SUPPORTED_PAIRS,
    ENABLE_COW,
    ENABLE_ORDER_SLICING,
    ENABLE_PRIVATE_TX,
    EXECUTION_POLICY_PROFILE,
    MAX_GAS_SPIKE_GWEI,
    MAX_PRICE_IMPACT_BPS,
    MAX_QUOTE_DEVIATION_BPS,
    MAX_SINGLE_SWAP_USD,
    MAX_SLIPPAGE_BPS,
    MAX_TWAP_DEVIATION_BPS,
    MEV_POLICY_PATH,
    MEV_RISK_THRESHOLD_BLOCK,
    PUBLIC_SWAP_MAX_RISK,
    SLICE_COUNT_MAX,
    SLICE_DELAY_MS,
)
from types_bot import ExecutionPolicy


DEFAULT_PROFILES = {
    "safe": {
        "allow_guarded_public": True,
        "public_swap_max_risk": min(PUBLIC_SWAP_MAX_RISK, 25.0),
        "mev_risk_threshold_block": max(MEV_RISK_THRESHOLD_BLOCK - 10.0, 40.0),
        "max_quote_deviation_bps": min(MAX_QUOTE_DEVIATION_BPS, 20.0),
        "max_twap_deviation_bps": min(MAX_TWAP_DEVIATION_BPS, 35.0),
        "max_price_impact_bps": min(MAX_PRICE_IMPACT_BPS, 25.0),
        "max_slippage_bps": min(MAX_SLIPPAGE_BPS, 18.0),
        "max_gas_spike_gwei": min(MAX_GAS_SPIKE_GWEI, 24.0),
        "max_single_swap_usd": min(MAX_SINGLE_SWAP_USD, 75.0),
        "slice_count_max": max(SLICE_COUNT_MAX, 3),
        "slice_delay_ms": max(SLICE_DELAY_MS, 350),
        "liquidity_hint_usd": 2_000_000.0,
    },
    "balanced": {
        "allow_guarded_public": True,
        "public_swap_max_risk": PUBLIC_SWAP_MAX_RISK,
        "mev_risk_threshold_block": MEV_RISK_THRESHOLD_BLOCK,
        "max_quote_deviation_bps": MAX_QUOTE_DEVIATION_BPS,
        "max_twap_deviation_bps": MAX_TWAP_DEVIATION_BPS,
        "max_price_impact_bps": MAX_PRICE_IMPACT_BPS,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "max_gas_spike_gwei": MAX_GAS_SPIKE_GWEI,
        "max_single_swap_usd": MAX_SINGLE_SWAP_USD,
        "slice_count_max": SLICE_COUNT_MAX,
        "slice_delay_ms": SLICE_DELAY_MS,
        "liquidity_hint_usd": 1_000_000.0,
    },
    "aggressive": {
        "allow_guarded_public": True,
        "public_swap_max_risk": max(PUBLIC_SWAP_MAX_RISK, 55.0),
        "mev_risk_threshold_block": max(MEV_RISK_THRESHOLD_BLOCK, 80.0),
        "max_quote_deviation_bps": max(MAX_QUOTE_DEVIATION_BPS, 50.0),
        "max_twap_deviation_bps": max(MAX_TWAP_DEVIATION_BPS, 75.0),
        "max_price_impact_bps": max(MAX_PRICE_IMPACT_BPS, 70.0),
        "max_slippage_bps": max(MAX_SLIPPAGE_BPS, 60.0),
        "max_gas_spike_gwei": max(MAX_GAS_SPIKE_GWEI, 45.0),
        "max_single_swap_usd": max(MAX_SINGLE_SWAP_USD, 200.0),
        "slice_count_max": max(SLICE_COUNT_MAX, 5),
        "slice_delay_ms": min(SLICE_DELAY_MS, 150),
        "liquidity_hint_usd": 500_000.0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class PolicyEngine:
    def __init__(
        self,
        policy_path: str | Path = MEV_POLICY_PATH,
        profile: str = EXECUTION_POLICY_PROFILE,
    ) -> None:
        self.policy_path = Path(policy_path)
        self.profile = profile if profile in DEFAULT_PROFILES else "balanced"
        self.raw_policy = self._load_policy()

    def _load_policy(self) -> dict:
        if not self.policy_path.exists():
            return {}

        text = self.policy_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}

        return json.loads(text)

    def _base_policy(self) -> dict:
        return {
            "allow_private_tx": ENABLE_PRIVATE_TX,
            "allow_cow": ENABLE_COW,
            "allow_guarded_public": True,
            "public_swap_max_risk": PUBLIC_SWAP_MAX_RISK,
            "mev_risk_threshold_block": MEV_RISK_THRESHOLD_BLOCK,
            "max_quote_deviation_bps": MAX_QUOTE_DEVIATION_BPS,
            "max_twap_deviation_bps": MAX_TWAP_DEVIATION_BPS,
            "max_price_impact_bps": MAX_PRICE_IMPACT_BPS,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
            "max_gas_spike_gwei": MAX_GAS_SPIKE_GWEI,
            "max_single_swap_usd": MAX_SINGLE_SWAP_USD,
            "slice_count_max": SLICE_COUNT_MAX,
            "slice_delay_ms": SLICE_DELAY_MS,
            "cow_min_notional_usd": COW_MIN_NOTIONAL_USD,
            "cow_supported": False,
            "liquidity_hint_usd": DEFAULT_PROFILES[self.profile]["liquidity_hint_usd"],
            "preferred_mode": "",
            "enable_order_slicing": ENABLE_ORDER_SLICING,
        }

    def resolve(self, pair: str, router: str) -> ExecutionPolicy:
        merged = self._base_policy()
        merged = _deep_merge(merged, DEFAULT_PROFILES[self.profile])
        merged = _deep_merge(merged, self.raw_policy.get("defaults", {}))
        merged = _deep_merge(merged, self.raw_policy.get("profiles", {}).get(self.profile, {}))
        merged = _deep_merge(merged, self.raw_policy.get("pairs", {}).get(pair, {}))
        merged = _deep_merge(merged, self.raw_policy.get("routers", {}).get(router, {}))

        cow_supported_pairs = {
            normalized_pair.strip().upper()
            for normalized_pair in COW_SUPPORTED_PAIRS
        }
        merged["cow_supported"] = bool(
            merged.get("allow_cow", ENABLE_COW)
            and (
                pair.strip().upper() in cow_supported_pairs
                or merged.get("cow_supported", False)
            )
        )

        return ExecutionPolicy(
            profile=self.profile,
            allow_private_tx=bool(merged.get("allow_private_tx", ENABLE_PRIVATE_TX)),
            allow_cow=bool(merged.get("allow_cow", ENABLE_COW)),
            allow_guarded_public=bool(merged.get("allow_guarded_public", True)),
            public_swap_max_risk=float(merged.get("public_swap_max_risk", PUBLIC_SWAP_MAX_RISK)),
            mev_risk_threshold_block=float(
                merged.get("mev_risk_threshold_block", MEV_RISK_THRESHOLD_BLOCK)
            ),
            max_quote_deviation_bps=float(
                merged.get("max_quote_deviation_bps", MAX_QUOTE_DEVIATION_BPS)
            ),
            max_twap_deviation_bps=float(
                merged.get("max_twap_deviation_bps", MAX_TWAP_DEVIATION_BPS)
            ),
            max_price_impact_bps=float(
                merged.get("max_price_impact_bps", MAX_PRICE_IMPACT_BPS)
            ),
            max_slippage_bps=float(merged.get("max_slippage_bps", MAX_SLIPPAGE_BPS)),
            max_gas_spike_gwei=float(merged.get("max_gas_spike_gwei", MAX_GAS_SPIKE_GWEI)),
            max_single_swap_usd=float(merged.get("max_single_swap_usd", MAX_SINGLE_SWAP_USD)),
            slice_count_max=max(int(merged.get("slice_count_max", SLICE_COUNT_MAX)), 1),
            slice_delay_ms=max(int(merged.get("slice_delay_ms", SLICE_DELAY_MS)), 0),
            cow_min_notional_usd=float(merged.get("cow_min_notional_usd", COW_MIN_NOTIONAL_USD)),
            cow_supported=bool(merged.get("cow_supported", False)),
            liquidity_hint_usd=float(merged.get("liquidity_hint_usd", 0.0)),
            preferred_mode=str(merged.get("preferred_mode", "")),
            metadata={
                "pair": pair,
                "router": router,
                "enable_order_slicing": bool(merged.get("enable_order_slicing", ENABLE_ORDER_SLICING)),
            },
        )
