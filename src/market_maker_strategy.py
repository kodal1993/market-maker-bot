from __future__ import annotations


def allow_market_making(*, regime: str, volatility_score: float, shock_active: bool, news_risk: float = 0.0) -> tuple[bool, str]:
    normalized_regime = (regime or "").upper()
    if normalized_regime != "RANGE":
        return False, f"mm_disabled_regime:{normalized_regime or 'UNKNOWN'}"
    if shock_active:
        return False, "mm_disabled_shock_active"
    if volatility_score >= 80.0:
        return False, "mm_disabled_high_volatility"
    if news_risk >= 0.65:
        return False, "mm_disabled_news_risk"
    return True, "mm_enabled_range_regime"
