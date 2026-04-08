from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FeedItem:
    title: str
    summary: str
    published_at: datetime | None
    source: str
    link: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class SignalScore:
    score: float = 0.0
    confidence: float = 0.0
    freshness_seconds: float | None = None
    item_count: int = 0
    summary: str = "neutral"
    status: str = "neutral"
    blocked: bool = False


@dataclass
class MarketState:
    regime: str
    volatility_state: str
    short_ma: float
    long_ma: float
    volatility: float
    trend_strength: float
    market_score: float


@dataclass
class AdaptiveState:
    performance_score: float
    inventory_multiplier: float
    trade_size_multiplier: float
    spread_multiplier: float
    threshold_multiplier: float


@dataclass
class IntelligenceSnapshot:
    mode: str
    current_mode: str
    reason: str
    feed_state: str
    regime: str
    volatility_state: str
    short_ma: float
    long_ma: float
    volatility: float
    trend_strength: float
    market_score: float
    feed_score: float
    news_score: float
    macro_score: float
    onchain_score: float
    adaptive_score: float
    signal_score: float
    risk_score: float
    confidence: float
    buy_enabled: bool
    sell_enabled: bool
    spread_multiplier: float
    max_inventory_multiplier: float
    trade_size_multiplier: float
    target_inventory_pct: float
    trend_threshold_multiplier: float
    max_chase_bps_multiplier: float
    inventory_skew_multiplier: float
    directional_bias: float
    active_regime: str = "RANGE"
    trend_direction: str = "neutral"
    activity_state: str = "normal"
    min_edge_multiplier: float = 1.0
    entry_trigger_multiplier: float = 1.0
    mm_mode: str = "base_mm"
    strategy_mode: str = "RANGE_MAKER"
    activity_boost: float = 0.0
    quote_enabled: bool = True
    aggressive_enabled: bool = False
    freeze_recovery_mode: bool = False
    minutes_since_last_fill: float = 0.0
    trades_last_60m: int = 0
    fill_quality_tier: str = "normal"
    fill_quality_score: float = 1.0
    cooldown_multiplier: float = 1.0
    blockers: list[str] = field(default_factory=list)
