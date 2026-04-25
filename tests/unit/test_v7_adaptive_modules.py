from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from regime_detector import AdaptiveRegimeInput, V7AdaptiveRegimeDetector
from risk_manager import RiskManager, RiskState
from strategy_selector import StrategySelector


def test_v7_regime_detector_detects_trend_up() -> None:
    detector = V7AdaptiveRegimeDetector()
    result = detector.detect(
        AdaptiveRegimeInput(
            price=103.5,
            ema20=104,
            ema50=102,
            ema200=100,
            vwap=103,
            rsi=62,
            atr_pct=0.01,
            volume_change=0.25,
            btc_trend=1.0,
            eth_btc_ratio_change=0.02,
        )
    )
    assert result.regime == "TREND_UP"


def test_strategy_selector_confidence_gate() -> None:
    selector = StrategySelector()
    result = selector.select("RANGE", confidence=0.45, min_confidence=0.65)
    assert result.regime == "NO_TRADE"
    assert result.strategy_name == "stay_flat"


def test_risk_manager_enforces_daily_loss() -> None:
    manager = RiskManager(
        max_daily_loss_ratio=0.03,
        max_trade_loss_ratio=0.01,
        max_position_size_usd=100.0,
        max_consecutive_losses=3,
        cooldown_cycles_after_loss=2,
        no_trade_drawdown_ratio=0.06,
        max_leverage=2.0,
        enable_leverage=False,
    )
    state = RiskState()
    decision = manager.assess(
        state,
        cycle_index=10,
        daily_pnl_ratio=-0.05,
        drawdown_ratio=0.02,
        trade_risk_ratio=0.005,
        proposed_position_usd=50.0,
    )
    assert not decision.allow_trade
    assert decision.reason == "max_daily_loss_reached"
