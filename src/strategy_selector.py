from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySelection:
    regime: str
    strategy_name: str
    confidence: float
    reason: str = ""


class StrategySelector:
    """Maps detected regimes to an executable strategy label for V7 adaptive flow."""

    STRATEGY_MAP = {
        "TREND_UP": "trend_long_strategy",
        "TREND_DOWN": "trend_short_or_hedge_strategy",
        "RANGE": "market_making_strategy",
        "BREAKOUT": "breakout_scalp_strategy",
        "NO_TRADE": "stay_flat",
    }

    def select(self, regime: str, confidence: float, *, min_confidence: float = 0.0) -> StrategySelection:
        normalized_regime = (regime or "NO_TRADE").upper().strip()
        selected_regime = normalized_regime if normalized_regime in self.STRATEGY_MAP else "NO_TRADE"

        if confidence < max(min_confidence, 0.0):
            return StrategySelection(
                regime="NO_TRADE",
                strategy_name=self.STRATEGY_MAP["NO_TRADE"],
                confidence=max(confidence, 0.0),
                reason=f"regime_confidence_below_threshold:{confidence:.3f}",
            )

        return StrategySelection(
            regime=selected_regime,
            strategy_name=self.STRATEGY_MAP[selected_regime],
            confidence=max(confidence, 0.0),
            reason="regime_selected",
        )
