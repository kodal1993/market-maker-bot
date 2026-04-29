from __future__ import annotations

from dataclasses import dataclass
import logging

from config import MIN_ORDER_SIZE_USD
from types_bot import DecisionOutcome


logger = logging.getLogger(__name__)


@dataclass
class _DecisionCandidate:
    action: str
    size_usd: float
    reason: str
    source: str
    order_price: float
    inventory_cap_usd: float
    filter_values: dict[str, object]


class DecisionEngine:
    def __init__(self, trade_filter, reentry_engine, inventory_manager):
        self.trade_filter = trade_filter
        self.reentry_engine = reentry_engine
        self.inventory_manager = inventory_manager

    def _is_priority_sell_reason(self, reason: str) -> bool:
        return reason.startswith("profit_lock_") or reason in {
            "failsafe_sell",
            "inventory_force_reduce",
            "time_exit_sell",
            "stop_loss_sell",
            "profit_exit_sell",
        }

    def _normalize_candidate(self, candidate: DecisionOutcome | None) -> _DecisionCandidate | None:
        if candidate is None:
            return None
        if candidate.action not in {"BUY", "SELL"} or candidate.size_usd <= 0:
            return None
        return _DecisionCandidate(
            action=candidate.action,
            size_usd=candidate.size_usd,
            reason=candidate.reason,
            source=candidate.source,
            order_price=candidate.order_price,
            inventory_cap_usd=candidate.inventory_cap_usd,
            filter_values=dict(candidate.filter_values),
        )

    def _candidate_tag(self, candidate: _DecisionCandidate | None) -> str:
        if candidate is None:
            return ""
        return f"{candidate.source}:{candidate.action}:{candidate.reason}"

    def _apply_inventory_caps(
        self,
        candidate: _DecisionCandidate,
        inventory_profile,
        available_usdc: float,
        inventory_manager_enabled: bool,
    ) -> _DecisionCandidate:
        if candidate.action == "BUY":
            capped_size = min(candidate.size_usd, available_usdc)
            if inventory_manager_enabled and not (
                candidate.reason.startswith("reentry_") or candidate.reason.startswith("force_trade_")
            ):
                capped_size = self.inventory_manager.cap_buy_usd(
                    inventory_profile,
                    capped_size,
                    available_usdc,
                )
        else:
            capped_size = candidate.size_usd
            if inventory_manager_enabled and not (
                self._is_priority_sell_reason(candidate.reason) or candidate.reason.startswith("force_trade_")
            ):
                capped_size = self.inventory_manager.cap_sell_usd(
                    inventory_profile,
                    capped_size,
                )

        return _DecisionCandidate(
            action=candidate.action,
            size_usd=max(capped_size, 0.0),
            reason=candidate.reason,
            source=candidate.source,
            order_price=candidate.order_price,
            inventory_cap_usd=candidate.inventory_cap_usd,
            filter_values=dict(candidate.filter_values),
        )

    def _evaluate_trade_filter(
        self,
        candidate: _DecisionCandidate,
        cycle_index: int,
        trade_filter_enabled: bool,
        last_trade_cycle: int | None,
        last_trade_price: float | None,
        loss_streak: int,
        rsi_value: float,
        momentum_bps: float,
        regime: str,
        market_score: float,
        volatility_state: str,
        trade_count: int,
        daily_trade_count: int = 0,
        market_mode: str = "base_mm",
        recent_trade_count_60m: int = 0,
        activity_boost: float = 0.0,
        freeze_recovery_mode: bool = False,
        fill_quality_tier: str = "normal",
        cooldown_multiplier: float = 1.0,
    ):
        if not trade_filter_enabled:
            return None

        filter_result = self.trade_filter.evaluate(
            side="buy" if candidate.action == "BUY" else "sell",
            trade_reason=candidate.reason,
            cycle_index=cycle_index,
            order_price=candidate.order_price,
            last_trade_cycle=last_trade_cycle,
            last_trade_price=last_trade_price,
            loss_streak=loss_streak,
            rsi_value=rsi_value,
            momentum_bps=momentum_bps,
            regime=regime,
            market_score=market_score,
            volatility_state=volatility_state,
            trade_count=trade_count,
            daily_trade_count=daily_trade_count,
            market_mode=market_mode,
            recent_trade_count_60m=recent_trade_count_60m,
            activity_boost=activity_boost,
            freeze_recovery_mode=freeze_recovery_mode,
            fill_quality_tier=fill_quality_tier,
            cooldown_multiplier=cooldown_multiplier,
        )
        return filter_result

    def _choose_strategy_candidate(
        self,
        strategy_buy: _DecisionCandidate | None,
        strategy_sell: _DecisionCandidate | None,
        inventory_profile=None,
        cycle_index: int = 0,
    ) -> tuple[_DecisionCandidate | None, list[str]]:
        overrides: list[str] = []
        if strategy_sell and self._is_priority_sell_reason(strategy_sell.reason):
            if strategy_buy:
                overrides.append(self._candidate_tag(strategy_buy))
            return strategy_sell, overrides
        if strategy_buy and strategy_sell:
            inventory_ratio = float(getattr(inventory_profile, "inventory_ratio", 0.5))
            lower_bound = float(getattr(inventory_profile, "lower_bound", 0.45))
            upper_bound = float(getattr(inventory_profile, "upper_bound", 0.55))
            target_ratio = (lower_bound + upper_bound) / 2.0
            if inventory_ratio > min(target_ratio + 0.01, upper_bound):
                overrides.append(self._candidate_tag(strategy_buy))
                return strategy_sell, overrides
            if inventory_ratio < max(target_ratio - 0.01, lower_bound):
                overrides.append(self._candidate_tag(strategy_sell))
                return strategy_buy, overrides
            if cycle_index % 2 == 0:
                overrides.append(self._candidate_tag(strategy_sell))
                return strategy_buy, overrides
            overrides.append(self._candidate_tag(strategy_buy))
            return strategy_sell, overrides
        if strategy_buy:
            if strategy_sell:
                overrides.append(self._candidate_tag(strategy_sell))
            return strategy_buy, overrides
        return strategy_sell, overrides

    def _log_trade_skipped(self, reason: str, candidate: _DecisionCandidate | None = None, extra: dict[str, object] | None = None) -> None:
        details: dict[str, object] = {"block_reason": reason}
        if candidate is not None:
            details.update({
                "candidate_action": candidate.action,
                "candidate_reason": candidate.reason,
                "candidate_source": candidate.source,
                "candidate_size_usd": round(candidate.size_usd, 6),
            })
        if extra:
            details.update(extra)
        logger.info("Trade skipped because: %s", details)

    def decide(
        self,
        cycle_index: int,
        reentry_active: bool,
        reentry_candidate: DecisionOutcome | None,
        inventory_candidate: DecisionOutcome | None,
        strategy_buy_candidate: DecisionOutcome | None,
        strategy_sell_candidate: DecisionOutcome | None,
        inventory_profile,
        available_usdc: float,
        inventory_manager_enabled: bool,
        trade_filter_enabled: bool,
        last_trade_cycle: int | None,
        last_trade_price: float | None,
        loss_streak: int,
        rsi_value: float,
        momentum_bps: float,
        regime: str,
        market_score: float,
        volatility_state: str,
        trade_count: int,
        daily_trade_count: int = 0,
        market_mode: str = "base_mm",
        recent_trade_count_60m: int = 0,
        activity_boost: float = 0.0,
        freeze_recovery_mode: bool = False,
        fill_quality_tier: str = "normal",
        cooldown_multiplier: float = 1.0,
    ) -> DecisionOutcome:
        reentry_signal = self._normalize_candidate(reentry_candidate)
        inventory_signal = self._normalize_candidate(inventory_candidate)
        strategy_buy_signal = self._normalize_candidate(strategy_buy_candidate)
        strategy_sell_signal = self._normalize_candidate(strategy_sell_candidate)

        overridden_signals: list[str] = []
        selected: _DecisionCandidate | None = None
        priority_sell_signal = None
        if strategy_sell_signal and self._is_priority_sell_reason(strategy_sell_signal.reason):
            priority_sell_signal = strategy_sell_signal

        if priority_sell_signal is not None:
            if reentry_signal:
                overridden_signals.append(self._candidate_tag(reentry_signal))
            if inventory_signal:
                overridden_signals.append(self._candidate_tag(inventory_signal))
            if strategy_buy_signal:
                overridden_signals.append(self._candidate_tag(strategy_buy_signal))
            selected = priority_sell_signal

        if selected is None and reentry_active:
            if strategy_buy_signal:
                overridden_signals.append(self._candidate_tag(strategy_buy_signal))
            if inventory_signal and inventory_signal.action == "BUY":
                overridden_signals.append(self._candidate_tag(inventory_signal))

            if reentry_signal:
                selected = reentry_signal
            elif inventory_signal and inventory_signal.action == "SELL":
                selected = inventory_signal
            else:
                strategy_candidate, strategy_overrides = self._choose_strategy_candidate(
                    None,
                    strategy_sell_signal,
                    inventory_profile=inventory_profile,
                    cycle_index=cycle_index,
                )
                overridden_signals.extend(strategy_overrides)
                if strategy_candidate:
                    selected = strategy_candidate

            if selected is None:
                self._log_trade_skipped("reentry_wait", extra={"cycle_index": cycle_index})
                return DecisionOutcome(
                    action="NONE",
                    reason="reentry_wait",
                    source="reentry",
                    block_reason="reentry_wait",
                    overridden_signals=[value for value in overridden_signals if value],
                )

        elif selected is None and inventory_signal:
            selected = inventory_signal
            if strategy_buy_signal:
                overridden_signals.append(self._candidate_tag(strategy_buy_signal))
            if strategy_sell_signal:
                overridden_signals.append(self._candidate_tag(strategy_sell_signal))

        elif selected is None:
            selected, strategy_overrides = self._choose_strategy_candidate(
                strategy_buy_signal,
                strategy_sell_signal,
                inventory_profile=inventory_profile,
                cycle_index=cycle_index,
            )
            overridden_signals.extend(strategy_overrides)
            if selected is None:
                self._log_trade_skipped("no_signal", extra={"cycle_index": cycle_index})
                return DecisionOutcome(
                    action="NONE",
                    block_reason="no_signal",
                    overridden_signals=[value for value in overridden_signals if value],
                )

        selected = self._apply_inventory_caps(
            selected,
            inventory_profile=inventory_profile,
            available_usdc=available_usdc,
            inventory_manager_enabled=inventory_manager_enabled,
        )
        if selected.size_usd < MIN_ORDER_SIZE_USD:
            self._log_trade_skipped("inventory_cap", candidate=selected)
            return DecisionOutcome(
                action="NONE",
                reason=selected.reason,
                source=selected.source,
                order_price=selected.order_price,
                inventory_cap_usd=selected.inventory_cap_usd,
                overridden_signals=[value for value in overridden_signals if value],
                block_reason="inventory_cap",
            )

        filter_result = self._evaluate_trade_filter(
            selected,
            cycle_index=cycle_index,
            trade_filter_enabled=trade_filter_enabled,
            last_trade_cycle=last_trade_cycle,
            last_trade_price=last_trade_price,
            loss_streak=loss_streak,
            rsi_value=rsi_value,
            momentum_bps=momentum_bps,
            regime=regime,
            market_score=market_score,
            volatility_state=volatility_state,
            trade_count=trade_count,
            daily_trade_count=daily_trade_count,
            market_mode=market_mode,
            recent_trade_count_60m=recent_trade_count_60m,
            activity_boost=activity_boost,
            freeze_recovery_mode=freeze_recovery_mode,
            fill_quality_tier=fill_quality_tier,
            cooldown_multiplier=cooldown_multiplier,
        )
        filter_values = dict(selected.filter_values)
        if filter_result is not None:
            filter_values.update(dict(filter_result.filter_values))
        if selected.filter_values.get("force_trade_active"):
            filter_values["force_trade_active"] = True
        if selected.filter_values.get("activity_floor_force"):
            filter_values["activity_floor_force"] = True
        if filter_result is not None and filter_result.size_multiplier > 0:
            adjusted_size_usd = selected.size_usd * filter_result.size_multiplier
            filter_values["pre_filter_size_usd"] = round(selected.size_usd, 6)
            filter_values["post_filter_size_usd"] = round(adjusted_size_usd, 6)
            selected = _DecisionCandidate(
                action=selected.action,
                size_usd=adjusted_size_usd,
                reason=selected.reason,
                source=selected.source,
                order_price=selected.order_price,
                inventory_cap_usd=selected.inventory_cap_usd,
                filter_values=dict(selected.filter_values),
            )
        if 0.0 < selected.size_usd < MIN_ORDER_SIZE_USD and filter_result is not None and filter_result.allow_trade:
            filter_values["size_clamped_to_min"] = True
            filter_values["pre_clamp_size_usd"] = round(selected.size_usd, 6)
            selected = _DecisionCandidate(
                action=selected.action,
                size_usd=MIN_ORDER_SIZE_USD,
                reason=selected.reason,
                source=selected.source,
                order_price=selected.order_price,
                inventory_cap_usd=selected.inventory_cap_usd,
                filter_values=dict(selected.filter_values),
            )
        if selected.size_usd < MIN_ORDER_SIZE_USD:
            self._log_trade_skipped("size_below_min_after_filter", candidate=selected, extra={"filter_values": filter_values})
            return DecisionOutcome(
                action="NONE",
                reason=selected.reason,
                source=selected.source,
                order_price=selected.order_price,
                inventory_cap_usd=selected.inventory_cap_usd,
                overridden_signals=[value for value in overridden_signals if value],
                block_reason="size_below_min_after_filter",
                filter_values=filter_values,
            )
        if filter_result is not None and not filter_result.allow_trade:
            if filter_result.block_reason == "loss_streak_pause":
                filter_values["loss_streak_pause_soft_gate"] = True
            else:
                self._log_trade_skipped(filter_result.block_reason, candidate=selected, extra={"filter_values": filter_values})
                return DecisionOutcome(
                    action="NONE",
                    reason=selected.reason,
                    source=selected.source,
                    order_price=selected.order_price,
                    inventory_cap_usd=selected.inventory_cap_usd,
                    overridden_signals=[value for value in overridden_signals if value],
                    block_reason=filter_result.block_reason,
                    filter_values=filter_values,
                )

        return DecisionOutcome(
            action=selected.action,
            size_usd=selected.size_usd,
            reason=selected.reason,
            source=selected.source,
            order_price=selected.order_price,
            inventory_cap_usd=selected.inventory_cap_usd,
            overridden_signals=[value for value in overridden_signals if value],
            allow_trade=True,
            filter_values=filter_values,
        )
