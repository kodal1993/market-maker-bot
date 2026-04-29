from __future__ import annotations

from config import (
    INVENTORY_ALLOW_REDUCE_ONLY_ABOVE_HARD_LIMIT,
    INVENTORY_BLOCK_SAME_SIDE_ENTRIES_ABOVE_HARD_LIMIT,
    INVENTORY_FORCE_REDUCE_LIMIT_PCT,
    INVENTORY_DOWNTREND_MAX,
    INVENTORY_DOWNTREND_MIN,
    INVENTORY_HARD_LIMIT_PCT,
    INVENTORY_NORMAL_MAX,
    INVENTORY_NORMAL_MIN,
    INVENTORY_SOFT_LIMIT_PCT,
    INVENTORY_UPTREND_MAX,
    INVENTORY_UPTREND_MIN,
)
from types_bot import InventoryProfile


class InventoryManager:
    def build_profile(self, regime: str, inventory_usd: float, equity_usd: float) -> InventoryProfile:
        if regime == "TREND":
            regime_label = "uptrend"
            lower_bound = INVENTORY_UPTREND_MIN
            upper_bound = INVENTORY_UPTREND_MAX
        elif regime == "RISK_OFF":
            regime_label = "downtrend"
            lower_bound = INVENTORY_DOWNTREND_MIN
            upper_bound = INVENTORY_DOWNTREND_MAX
        else:
            regime_label = "normal"
            lower_bound = min(INVENTORY_NORMAL_MIN, 0.22)
            upper_bound = max(INVENTORY_NORMAL_MAX, 0.78)

        inventory_ratio = (inventory_usd / equity_usd) if equity_usd > 0 else 0.0
        hard_limit_pct = upper_bound * max(INVENTORY_HARD_LIMIT_PCT, 0.0)
        if hard_limit_pct <= 0:
            hard_limit_pct = upper_bound
        soft_limit_pct = min(hard_limit_pct, upper_bound * max(INVENTORY_SOFT_LIMIT_PCT, 0.0))
        force_limit_pct = min(max(INVENTORY_FORCE_REDUCE_LIMIT_PCT, hard_limit_pct), 1.0)
        soft_limit_usd = max(soft_limit_pct * equity_usd, 0.0)
        hard_limit_usd = max(hard_limit_pct * equity_usd, 0.0)
        force_limit_usd = max(force_limit_pct * equity_usd, 0.0)
        max_buy_usd = max(hard_limit_usd - inventory_usd, 0.0)
        max_sell_usd = max(inventory_usd - (lower_bound * equity_usd), 0.0)
        soft_limit_hit = inventory_usd >= soft_limit_usd > 0
        hard_limit_hit = inventory_usd >= hard_limit_usd > 0
        force_limit_hit = inventory_usd >= force_limit_usd > 0
        return InventoryProfile(
            regime_label=regime_label,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            inventory_ratio=inventory_ratio,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            allow_buy=(
                max_buy_usd > 0
                and not (hard_limit_hit and INVENTORY_BLOCK_SAME_SIDE_ENTRIES_ABOVE_HARD_LIMIT)
            ),
            allow_sell=max_sell_usd > 0,
            max_buy_usd=max_buy_usd,
            max_sell_usd=max_sell_usd,
            soft_limit_usd=soft_limit_usd,
            hard_limit_usd=hard_limit_usd,
            force_limit_usd=force_limit_usd,
            soft_limit_hit=soft_limit_hit,
            hard_limit_hit=hard_limit_hit,
            force_limit_hit=force_limit_hit,
            reduction_only=hard_limit_hit and INVENTORY_ALLOW_REDUCE_ONLY_ABOVE_HARD_LIMIT,
        )

    def cap_buy_usd(self, profile: InventoryProfile, desired_usd: float, available_usdc: float) -> float:
        if desired_usd <= 0 or not profile.allow_buy:
            return 0.0
        return max(min(desired_usd, profile.max_buy_usd, available_usdc), 0.0)

    def cap_sell_usd(self, profile: InventoryProfile, desired_usd: float) -> float:
        if desired_usd <= 0 or not profile.allow_sell:
            return 0.0
        return max(min(desired_usd, profile.max_sell_usd), 0.0)
