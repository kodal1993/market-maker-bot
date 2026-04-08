from dataclasses import dataclass, field
from enum import Enum


class StrategyState(str, Enum):
    IDLE = "IDLE"
    WAIT_REENTRY = "WAIT_REENTRY"
    ACCUMULATING = "ACCUMULATING"
    DISTRIBUTING = "DISTRIBUTING"
    COOLDOWN = "COOLDOWN"


class ExecutionMode(str, Enum):
    PRIVATE_TX = "private_tx"
    COW_INTENT = "cow_intent"
    GUARDED_PUBLIC = "guarded_public"
    SKIP = "skip"


@dataclass
class PaperOrder:
    side: str
    price: float
    size_base: float
    size_usd: float
    fee_bps: float
    mode: str
    execution_type: str = "maker"
    slippage_bps: float = 0.0
    trade_reason: str = ""
    expected_profit_pct: float = 0.0


@dataclass
class FillResult:
    filled: bool
    side: str
    price: float
    size_base: float
    size_usd: float
    fee_usd: float
    reason: str
    execution_type: str = "maker"
    slippage_bps: float = 0.0
    trade_reason: str = ""


@dataclass
class Quote:
    bid: float
    ask: float
    mid: float
    spread_bps: float
    mode: str


@dataclass
class ReentryState:
    active: bool = False
    last_sell_price: float | None = None
    last_sell_size_usd: float = 0.0
    last_sell_cycle: int | None = None
    buy_zones: tuple[float, float, float] = (0.0, 0.0, 0.0)
    executed_buy_levels: list[str] = field(default_factory=list)
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    timeout_cycle: int | None = None
    timeout_triggered: bool = False
    runaway_triggered: bool = False
    max_miss_triggered: bool = False
    highest_price_since_sell: float | None = None
    lowest_price_since_sell: float | None = None


@dataclass
class ProfitLockState:
    anchor_price: float | None = None
    highest_price: float | None = None
    level_one_armed: bool = False
    level_two_armed: bool = False
    level_one_executed: bool = False
    level_two_executed: bool = False


@dataclass
class TradeFilterResult:
    allow_trade: bool
    block_reason: str = ""
    size_multiplier: float = 1.0
    filter_values: dict[str, object] = field(default_factory=dict)


@dataclass
class InventoryProfile:
    regime_label: str
    lower_bound: float
    upper_bound: float
    inventory_ratio: float
    inventory_usd: float
    equity_usd: float
    allow_buy: bool
    allow_sell: bool
    max_buy_usd: float
    max_sell_usd: float
    soft_limit_usd: float = 0.0
    hard_limit_usd: float = 0.0
    force_limit_usd: float = 0.0
    soft_limit_hit: bool = False
    hard_limit_hit: bool = False
    force_limit_hit: bool = False
    reduction_only: bool = False


@dataclass
class ExecutionDecision:
    allow_trade: bool
    block_reason: str
    order_price: float
    quoted_price: float
    fee_bps: float
    execution_type: str
    slippage_bps: float
    expected_profit_pct: float
    trade_reason: str


@dataclass
class ReentryPlan:
    allow_trade: bool
    trade_reason: str = ""
    size_usd: float = 0.0


@dataclass
class StateMachineContext:
    current_state: StrategyState = StrategyState.IDLE
    previous_state: str = ""
    entered_cycle: int = 0
    transition_reason: str = "boot"
    last_transition: str = "boot"
    last_transition_cycle: int = 0
    cooldown_until_cycle: int | None = None


@dataclass
class DecisionOutcome:
    action: str = "NONE"
    size_usd: float = 0.0
    reason: str = ""
    source: str = ""
    order_price: float = 0.0
    inventory_cap_usd: float = 0.0
    overridden_signals: list[str] = field(default_factory=list)
    block_reason: str = ""
    allow_trade: bool = False
    filter_values: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketRegimeAssessment:
    market_regime: str
    regime_confidence: float
    range_width_pct: float
    net_move_pct: float
    direction_consistency: float
    volatility_score: float
    execution_regime: str = "RANGE"
    trend_direction: str = "neutral"
    range_location: str = "middle"
    bounce_count: int = 0
    range_touch_count: int = 0
    sign_flip_ratio: float = 0.0
    noise_ratio: float = 0.0
    body_to_wick_ratio: float = 0.0
    ema_deviation_pct: float = 0.0
    mean_reversion_distance_pct: float = 0.0
    window_high: float = 0.0
    window_low: float = 0.0
    window_mean: float = 0.0
    price_position_pct: float = 0.5
    shock_active: bool = False


@dataclass(frozen=True)
class EdgeAssessment:
    expected_edge_usd: float
    expected_edge_bps: float
    cost_estimate_usd: float
    edge_score: float
    edge_pass: bool
    edge_reject_reason: str = ""
    expected_capture_usd: float = 0.0
    expected_capture_bps: float = 0.0
    fee_estimate_usd: float = 0.0
    slippage_estimate_bps: float = 0.0
    slippage_estimate_usd: float = 0.0
    gas_estimate_usd: float = 0.0
    mev_risk_score: float = 0.0
    sandwich_risk: float = 0.0
    mev_penalty_usd: float = 0.0
    regime_penalty_usd: float = 0.0
    loss_penalty_usd: float = 0.0
    reentry_penalty_usd: float = 0.0
    inventory_adjustment_usd: float = 0.0
    pullback_depth_pct: float = 0.0
    edge_bucket: str = "bad"
    size_multiplier: float = 1.0
    spread_multiplier: float = 1.0
    inventory_skew_multiplier: float = 1.0
    cooldown_multiplier: float = 1.0
    aggressive_enabled: bool = False


@dataclass(frozen=True)
class SignalGateDecision:
    allow_trade: bool
    approved_mode: str
    blocked_reason: str = ""
    gate_details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionSignal:
    side: str
    size_usd: float
    limit_price: float
    trade_reason: str
    mode: str
    source: str = ""
    pair: str = "WETH/USDC"
    router: str = "uniswap_v3"
    inventory_cap_usd: float = 0.0
    allow_partial: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionContext:
    pair: str
    router: str
    mid_price: float
    quote_bid: float
    quote_ask: float
    router_price: float
    backup_price: float = 0.0
    onchain_ref_price: float = 0.0
    twap_price: float = 0.0
    spread_bps: float = 0.0
    volatility: float = 0.0
    liquidity_usd: float = 0.0
    gas_price_gwei: float = 0.0
    block_number: int = 0
    recent_blocks_since_trade: int = 0
    portfolio_usdc: float = 0.0
    portfolio_eth: float = 0.0
    market_mode: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class QuoteValidationResult:
    is_valid: bool
    router_price: float
    reference_price: float
    quote_deviation_bps: float
    twap_deviation_bps: float
    block_reason: str = ""
    quotes: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SlippageEstimate:
    is_valid: bool
    expected_slippage_bps: float
    allowed_slippage_bps: float
    price_impact_bps: float
    size_ratio: float
    block_reason: str = ""


@dataclass(frozen=True)
class MevRiskAssessment:
    mev_risk_score: float
    sandwich_risk: float
    execution_window_score: float
    recommended_execution_mode: str
    public_swap_allowed: bool
    block_reason: str = ""


@dataclass(frozen=True)
class ExecutionSlice:
    index: int
    size_usd: float
    delay_ms: int
    expected_slippage_bps: float


@dataclass(frozen=True)
class SimulationResult:
    success: bool
    estimated_price: float
    realized_slippage_bps: float
    estimated_cost_usd: float
    block_reason: str = ""
    notes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionPolicy:
    profile: str
    allow_private_tx: bool
    allow_cow: bool
    allow_guarded_public: bool
    public_swap_max_risk: float
    mev_risk_threshold_block: float
    max_quote_deviation_bps: float
    max_twap_deviation_bps: float
    max_price_impact_bps: float
    max_slippage_bps: float
    max_gas_spike_gwei: float
    max_single_swap_usd: float
    slice_count_max: int
    slice_delay_ms: int
    cow_min_notional_usd: float
    cow_supported: bool
    liquidity_hint_usd: float = 0.0
    preferred_mode: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    allow_trade: bool
    execution_mode: str
    private_tx_used: bool
    cow_used: bool
    quoted_price: float
    order_price: float
    size_usd: float
    fee_bps: float
    execution_type: str
    mev_risk_score: float
    sandwich_risk: float
    execution_window_score: float
    expected_slippage_bps: float
    realized_slippage_bps: float
    price_impact_bps: float
    quote_deviation_bps: float
    trade_blocked_reason: str = ""
    slices: list[ExecutionSlice] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class ExecutionAnalyticsRecord:
    execution_mode: str = ""
    private_tx_used: bool = False
    cow_used: bool = False
    trade_size_usd: float = 0.0
    mev_risk_score: float = 0.0
    sandwich_risk: float = 0.0
    execution_window_score: float = 0.0
    expected_slippage_bps: float = 0.0
    realized_slippage_bps: float = 0.0
    price_impact_bps: float = 0.0
    quote_deviation_bps: float = 0.0
    trade_blocked_reason: str = ""
    slice_count: int = 0
    policy_profile: str = ""
