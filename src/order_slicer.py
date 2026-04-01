from __future__ import annotations

import math

from config import ENABLE_ORDER_SLICING
from types_bot import ExecutionPolicy, ExecutionSignal, ExecutionSlice, SlippageEstimate


class OrderSlicer:
    def slice_order(
        self,
        signal: ExecutionSignal,
        policy: ExecutionPolicy,
        slippage: SlippageEstimate,
    ) -> list[ExecutionSlice]:
        slicing_enabled = bool(policy.metadata.get("enable_order_slicing", ENABLE_ORDER_SLICING))
        if not slicing_enabled or signal.size_usd <= policy.max_single_swap_usd:
            return [
                ExecutionSlice(
                    index=1,
                    size_usd=signal.size_usd,
                    delay_ms=0,
                    expected_slippage_bps=slippage.expected_slippage_bps,
                )
            ]

        target_slices = math.ceil(signal.size_usd / max(policy.max_single_swap_usd, 1.0))
        slice_count = min(max(target_slices, 2), max(policy.slice_count_max, 1))
        slice_size = signal.size_usd / slice_count
        slippage_reduction_factor = min(0.55 + (0.1 * slice_count), 0.95)

        return [
            ExecutionSlice(
                index=index + 1,
                size_usd=slice_size,
                delay_ms=index * policy.slice_delay_ms,
                expected_slippage_bps=slippage.expected_slippage_bps * slippage_reduction_factor,
            )
            for index in range(slice_count)
        ]
