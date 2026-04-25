from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskState:
    consecutive_losses: int = 0
    cooling_down_until_cycle: int = -1
    no_trade: bool = False
    last_reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    allow_trade: bool
    reason: str
    allowed_leverage: float


class RiskManager:
    def __init__(
        self,
        *,
        max_daily_loss_ratio: float,
        max_trade_loss_ratio: float,
        max_position_size_usd: float,
        max_consecutive_losses: int,
        cooldown_cycles_after_loss: int,
        no_trade_drawdown_ratio: float,
        max_leverage: float,
        enable_leverage: bool,
    ) -> None:
        self.max_daily_loss_ratio = max(max_daily_loss_ratio, 0.0)
        self.max_trade_loss_ratio = max(max_trade_loss_ratio, 0.0)
        self.max_position_size_usd = max(max_position_size_usd, 0.0)
        self.max_consecutive_losses = max(max_consecutive_losses, 0)
        self.cooldown_cycles_after_loss = max(cooldown_cycles_after_loss, 0)
        self.no_trade_drawdown_ratio = max(no_trade_drawdown_ratio, 0.0)
        self.max_leverage = min(max(max_leverage, 1.0), 3.0)
        self.enable_leverage = bool(enable_leverage)

    def on_trade_closed(self, state: RiskState, *, pnl_usd: float, cycle_index: int) -> None:
        if pnl_usd < 0:
            state.consecutive_losses += 1
            if self.cooldown_cycles_after_loss > 0:
                state.cooling_down_until_cycle = cycle_index + self.cooldown_cycles_after_loss
        else:
            state.consecutive_losses = 0
            state.cooling_down_until_cycle = -1

    def assess(
        self,
        state: RiskState,
        *,
        cycle_index: int,
        daily_pnl_ratio: float,
        drawdown_ratio: float,
        trade_risk_ratio: float,
        proposed_position_usd: float,
    ) -> RiskDecision:
        if state.no_trade:
            return RiskDecision(False, state.last_reason or "no_trade_lock", self._allowed_leverage())

        if self.no_trade_drawdown_ratio > 0 and drawdown_ratio >= self.no_trade_drawdown_ratio:
            state.no_trade = True
            state.last_reason = "drawdown_lock"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        if self.max_daily_loss_ratio > 0 and daily_pnl_ratio <= -self.max_daily_loss_ratio:
            state.last_reason = "max_daily_loss_reached"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        if self.max_trade_loss_ratio > 0 and trade_risk_ratio > self.max_trade_loss_ratio:
            state.last_reason = "max_trade_loss_exceeded"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        if self.max_position_size_usd > 0 and proposed_position_usd > self.max_position_size_usd:
            state.last_reason = "max_position_size_exceeded"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        if self.max_consecutive_losses > 0 and state.consecutive_losses >= self.max_consecutive_losses:
            state.last_reason = "max_consecutive_losses_reached"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        if state.cooling_down_until_cycle >= cycle_index:
            state.last_reason = "cooldown_after_loss"
            return RiskDecision(False, state.last_reason, self._allowed_leverage())

        state.last_reason = "risk_ok"
        return RiskDecision(True, state.last_reason, self._allowed_leverage())

    def _allowed_leverage(self) -> float:
        if not self.enable_leverage:
            return 1.0
        return self.max_leverage
