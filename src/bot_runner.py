import json
import math
from collections import deque
from dataclasses import dataclass, field

from config import (
    BOT_CONFIG_PROFILE,
    BOT_MODE,
    CONFIRMATION_TIMEFRAME_SECONDS,
    DECISION_ENGINE_ENABLED,
    EMA_RANGE_BAND_BPS,
    ENABLE_EXECUTION_CONFIRMATION,
    ENABLE_TREND_TIMEFRAME_FILTER,
    ETH_ACCUMULATION_REINVEST_PCT,
    ETH_PRESERVATION_FLOOR_MULTIPLIER,
    EXECUTION_TIMEFRAME_SECONDS,
    EXECUTION_ENGINE_ENABLED,
    EXECUTION_REQUOTE_INTERVAL_MS,
    EXECUTION_STALE_QUOTE_TIMEOUT_MS,
    FORCE_TRADE_SIZE_FRACTION,
    INACTIVITY_FORCE_ENTRY_MINUTES,
    INACTIVITY_FORCE_SIZE_MULTIPLIER,
    INVENTORY_FORCED_REDUCE_AGGRESSION_BPS,
    INVENTORY_NEUTRAL_BAND_PCT,
    INVENTORY_SKEW_ACCELERATION,
    INVENTORY_SKEW_STRENGTH,
    INVENTORY_MANAGER_ENABLED,
    INTELLIGENCE_WARMUP_ROWS,
    KILL_SWITCH_USD,
    LONG_MA_WINDOW,
    LOOP_SECONDS,
    MAKER_FEE_BPS,
    MAX_EXIT_PREMIUM_BPS,
    MAX_CONSECUTIVE_LOSSES_BEFORE_PAUSE,
    MAX_DAILY_LOSS_USD,
    MAX_EXPOSURE_USD,
    MAX_INVENTORY_USD,
    MAX_TRADE_SIZE_USD,
    MAX_TREND_CHASE_BPS,
    MAX_TREND_PULLBACK_BPS,
    MIN_ORDER_SIZE_USD,
    MIN_SELL_PROFIT_BPS,
    PARTIAL_RESET_BUY_FRACTION,
    PARTIAL_RESET_USDC_THRESHOLD_PCT,
    PROFIT_LOCK_LEVEL_1_BPS,
    PROFIT_LOCK_LEVEL_1_SELL_FRACTION,
    PROFIT_LOCK_LEVEL_2_BPS,
    PROFIT_LOCK_LEVEL_2_SELL_FRACTION,
    RANGE_ENTRY_MAX_POSITION_PCT,
    RANGE_EXIT_MIN_POSITION_PCT,
    RANGE_MEAN_REVERSION_EXIT_POSITION_PCT,
    RANGE_MIN_EDGE_BPS,
    REGIME_MAX_WICK_TO_BODY_RATIO,
    REENTRY_ENGINE_ENABLED,
    REENTRY_INVENTORY_BUFFER_PCT,
    REENTRY_MAX_MISS_BUY_FRACTION,
    REENTRY_MAX_MISS_PCT,
    REENTRY_MOMENTUM_LOOKBACK,
    REENTRY_RSI_BUY_THRESHOLD,
    REENTRY_RSI_PERIOD,
    REENTRY_RSI_TURN_MARGIN,
    REENTRY_RUNAWAY_BUY_FRACTION,
    REENTRY_TIMEOUT_BUY_FRACTION,
    REENTRY_TIMEOUT_MINUTES,
    REENTRY_ZONE_1_BUY_FRACTION,
    REENTRY_ZONE_1_MULTIPLIER,
    REENTRY_ZONE_2_BUY_FRACTION,
    REENTRY_ZONE_2_MULTIPLIER,
    REENTRY_ZONE_3_BUY_FRACTION,
    REENTRY_ZONE_3_MULTIPLIER,
    START_ETH,
    START_ETH_USD,
    START_USDC,
    STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES,
    STATE_MACHINE_ENABLED,
    SIDE_FLIP_COOLDOWN_CYCLES,
    SIDE_FLIP_MIN_BPS,
    SHORT_MA_WINDOW,
    STOP_LOSS_PCT,
    TAKER_FEE_BPS,
    TRADE_FILTER_ENABLED,
    TRADE_FILTER_DEBUG_MODE,
    TRADE_FILTER_FORCE_TRADE_MINUTES,
    TRADE_SIZE_USD,
    TREND_FILTER_TIMEFRAME_SECONDS,
    TREND_BUY_MIN_CONFIDENCE,
    TREND_BUY_MIN_LONG_BUFFER_BPS,
    TREND_BUY_MIN_MARKET_SCORE,
    TREND_BUY_MIN_SIGNAL_SCORE,
    TREND_BUY_MIN_STRENGTH_MULTIPLIER,
    TREND_BUY_REQUOTE_BPS,
    TREND_BUY_TARGET_PCT,
    TREND_SELL_SPREAD_FACTOR,
    VOL_WINDOW,
    WAIT_REENTRY_PULLBACK_PCT,
)
from decision_engine import DecisionEngine
from edge_filter import EdgeFilter
from engine import PaperEngine
from execution_analytics import analytics_from_result, as_filter_values, blank_execution_analytics, update_block_reason
from execution_engine import ExecutionEngine
from execution_router import ExecutionRouter
from intelligence import IntelligenceEngine
from intelligence import drawdown_stage_priority, resolve_drawdown_stage
from intelligence_utils import ema
from inventory_manager import InventoryManager
from logger import log, log_trade_record
from multi_timeframe import build_timeframe_snapshot, required_bootstrap_price_rows
from performance import PerformanceTracker, log_performance_summary
from portfolio import Portfolio
from regime_detector import RegimeDetector
from reentry_engine import ReentryEngine
import runtime_execution as execution_helpers
import runtime_logging as logging_helpers
import runtime_risk as risk_helpers
import runtime_strategy as strategy_helpers
from signal_gate import SignalGate, capped_loss_pause_minutes, loss_pause_cycles, loss_pause_remaining_minutes
from sizing_engine import SizingSnapshot, as_log_fields as sizing_log_fields, build_sizing_snapshot
from state_machine import StateMachineEngine
from telegram_notifier import TelegramNotifier
from strategy_profile import (
    resolve_effective_entry_threshold_bps,
    resolve_effective_min_edge_bps,
    resolve_logging_zone,
)
from strategy import (
    build_quotes,
    calculate_buy_zones,
    calculate_rsi,
    calculate_spread,
    choose_trade_size_usd,
    detect_momentum_slowing,
    requote_trend_buy_price,
    requote_trend_sell_price,
    should_place_trend_buy,
)
from trade_filter import TradeFilter, calculate_recent_momentum_bps
from types_bot import (
    DecisionOutcome,
    EdgeAssessment,
    ExecutionAnalyticsRecord,
    ExecutionContext,
    ExecutionSignal,
    MarketRegimeAssessment,
    ProfitLockState,
    Quote,
    ReentryState,
    SignalGateDecision,
    StateMachineContext,
    StrategyState,
)

PRICE_WINDOW_SIZE = max(INTELLIGENCE_WARMUP_ROWS, LONG_MA_WINDOW, VOL_WINDOW + 1)
REENTRY_ZONE_LEVELS = (
    ("zone_1", REENTRY_ZONE_1_BUY_FRACTION),
    ("zone_2", REENTRY_ZONE_2_BUY_FRACTION),
    ("zone_3", REENTRY_ZONE_3_BUY_FRACTION),
)
FORCED_SELL_REASONS = {
    "failsafe_sell",
    "inventory_force_reduce",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
    "force_trade_sell",
}
PRIORITY_SELL_REASONS = {
    "failsafe_sell",
    "inventory_force_reduce",
    "time_exit_sell",
    "stop_loss_sell",
    "profit_lock_level_1",
    "profit_lock_level_2",
}
INVENTORY_DRIFT_GUARD_EXEMPT_REASONS = FORCED_SELL_REASONS | {"inventory_correction"}


def trade_log_headers() -> list[str]:
    return logging_helpers.trade_log_headers()


def equity_log_headers() -> list[str]:
    return logging_helpers.equity_log_headers()


@dataclass
class BotRuntime:
    portfolio: Portfolio
    engine: PaperEngine
    performance: PerformanceTracker
    intelligence: IntelligenceEngine
    decision_engine: DecisionEngine
    regime_detector: RegimeDetector
    edge_filter: EdgeFilter
    signal_gate: SignalGate
    execution_engine: ExecutionEngine
    execution_router: ExecutionRouter
    reentry_engine: ReentryEngine
    state_machine: StateMachineEngine
    trade_filter: TradeFilter
    inventory_manager: InventoryManager
    raw_prices: list[float]
    prices: list[float]
    trend_prices: list[float]
    confirmation_prices: list[float]
    cycle_seconds: float
    execution_timeframe_seconds: float
    trend_timeframe_seconds: float
    confirmation_timeframe_seconds: float
    enable_trend_timeframe_filter: bool
    enable_confirmation_filter: bool
    enable_reentry_engine: bool
    enable_decision_engine: bool
    enable_execution_engine: bool
    enable_trade_filter: bool
    enable_inventory_manager: bool
    enable_state_machine: bool
    start_usdc: float
    start_eth: float
    telegram_notifier: TelegramNotifier | None = None
    current_sizing: SizingSnapshot | None = None
    current_regime_assessment: MarketRegimeAssessment | None = None
    current_edge_assessment: EdgeAssessment | None = None
    current_signal_gate_decision: SignalGateDecision | None = None
    current_market_mode: str = ""
    current_trend_short_ma: float = 0.0
    current_trend_long_ma: float = 0.0
    current_trend_bias: str = "range"
    current_confirmation_momentum_bps: float = 0.0
    current_confirmation_slowing: bool = False
    current_execution_bucket_count: int = 0
    current_trend_bucket_count: int = 0
    current_confirmation_bucket_count: int = 0
    recent_equities: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    start_eq: float | None = None
    equity_peak: float | None = None
    max_pnl: float | None = None
    min_pnl: float | None = None
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    inventory_min: float | None = None
    inventory_max: float | None = None
    inventory_ratio_min: float | None = None
    inventory_ratio_max: float | None = None
    last_fill_cycle: int | None = None
    last_fill_side: str | None = None
    last_fill_price: float | None = None
    last_trade_cycle_any: int | None = None
    last_trade_price_any: float | None = None
    last_execution_price: float = 0.0
    last_execution_type: str = ""
    last_slippage_bps: float = 0.0
    last_execution_pair: str = ""
    last_execution_gas_gwei: float = 0.0
    last_execution_tx_hash: str = ""
    last_execution_metadata: dict[str, object] = field(default_factory=dict)
    last_decision_source: str = ""
    last_decision_reason: str = ""
    last_decision_size_usd: float = 0.0
    last_final_action: str = "NONE"
    last_overridden_signals: str = ""
    last_decision_block_reason: str = ""
    last_allow_trade: bool = False
    last_filter_values: str = ""
    last_trade_reason: str = ""
    last_trade_reason_detail: str = ""
    last_raw_signal: str = ""
    last_profit_pct: float | None = None
    last_buy_debug_reason: str = ""
    last_sell_debug_reason: str = ""
    current_inventory_state: str = "balanced"
    current_active_regime: str = "RANGE"
    current_detected_regime: str = "RANGE"
    current_trend_direction: str = "neutral"
    current_range_location: str = "middle"
    current_zone: str = "mid"
    current_volatility_bucket: str = "NORMAL"
    current_activity_state: str = "normal"
    current_inactivity_fallback_active: bool = False
    current_signal_block_reason: str = ""
    current_inventory_limit_state: str = "normal"
    current_soft_inventory_limit_usd: float = 0.0
    current_hard_inventory_limit_usd: float = 0.0
    current_entry_threshold_bps: float = 0.0
    current_min_edge_bps: float = 0.0
    current_inventory_drift_pct: float = 0.0
    current_entry_edge_bps: float = 0.0
    current_entry_edge_usd: float = 0.0
    current_hold_minutes: float = 0.0
    open_position_cycle: int | None = None
    open_position_reason: str = ""
    open_position_regime: str = ""
    open_position_price: float = 0.0
    open_position_entry_edge_bps: float = 0.0
    open_position_entry_edge_usd: float = 0.0
    open_position_used_fallback: bool = False
    current_drawdown_pct: float = 0.0
    drawdown_guard_stage: str = "normal"
    daily_reset_date: str = ""
    daily_start_equity: float | None = None
    daily_start_realized_pnl: float = 0.0
    daily_pnl_usd: float = 0.0
    daily_trade_count: int = 0
    loss_pause_until_cycle: int | None = None
    loss_pause_remaining_minutes: float = 0.0
    risk_stop_active: bool = False
    risk_stop_reason: str = ""
    risk_stop_message: str = ""
    risk_alert_sent_reasons: set[str] = field(default_factory=set)
    last_execution_analytics: ExecutionAnalyticsRecord = field(default_factory=blank_execution_analytics)
    maker_count: int = 0
    taker_count: int = 0
    total_slippage_bps: float = 0.0
    loss_streak: int = 0
    last_loss_cycle: int | None = None
    last_loss_trade_reason: str = ""
    last_trade_execution_bucket: int | None = None
    last_mid: float = 0.0
    reentry_state: ReentryState = field(default_factory=ReentryState)
    profit_lock_state: ProfitLockState = field(default_factory=ProfitLockState)
    state_context: StateMachineContext = field(default_factory=StateMachineContext)
    gross_profit_usd: float = 0.0
    gross_loss_usd: float = 0.0
    realized_trade_pnls: list[float] = field(default_factory=list)
    max_loss_streak: int = 0
    cumulative_hold_minutes: float = 0.0
    closed_hold_count: int = 0
    cumulative_inventory_drift: float = 0.0
    max_inventory_drift: float = 0.0
    inventory_drift_samples: int = 0
    last_rejection_cycle: int | None = None
    mode_counts: dict[str, int] = field(
        default_factory=lambda: {
            "TREND_UP": 0,
            "RANGE_MAKER": 0,
            "OVERWEIGHT_EXIT": 0,
            "NO_TRADE": 0,
        }
    )
    feed_state_counts: dict[str, int] = field(
        default_factory=lambda: {
            "NORMAL": 0,
            "CAUTION": 0,
            "BLOCK": 0,
        }
    )
    mode_trade_counts: dict[str, int] = field(
        default_factory=lambda: {
            "TREND_UP": 0,
            "RANGE_MAKER": 0,
            "OVERWEIGHT_EXIT": 0,
            "NO_TRADE": 0,
        }
    )
    mode_buy_counts: dict[str, int] = field(
        default_factory=lambda: {
            "TREND_UP": 0,
            "RANGE_MAKER": 0,
            "OVERWEIGHT_EXIT": 0,
            "NO_TRADE": 0,
        }
    )
    mode_sell_counts: dict[str, int] = field(
        default_factory=lambda: {
            "TREND_UP": 0,
            "RANGE_MAKER": 0,
            "OVERWEIGHT_EXIT": 0,
            "NO_TRADE": 0,
        }
    )
    mode_trade_notional_usd: dict[str, float] = field(
        default_factory=lambda: {
            "TREND_UP": 0.0,
            "RANGE_MAKER": 0.0,
            "OVERWEIGHT_EXIT": 0.0,
            "NO_TRADE": 0.0,
        }
    )
    mode_realized_pnl_usd: dict[str, float] = field(
        default_factory=lambda: {
            "TREND_UP": 0.0,
            "RANGE_MAKER": 0.0,
            "OVERWEIGHT_EXIT": 0.0,
            "NO_TRADE": 0.0,
        }
    )
    regime_trade_counts: dict[str, int] = field(
        default_factory=lambda: {
            "TREND": 0,
            "RANGE": 0,
            "NO_TRADE": 0,
        }
    )
    regime_realized_pnl_usd: dict[str, float] = field(
        default_factory=lambda: {
            "TREND": 0.0,
            "RANGE": 0.0,
            "NO_TRADE": 0.0,
        }
    )
    rejection_reason_counts: dict[str, int] = field(default_factory=dict)
    state_counts: dict[str, int] = field(
        default_factory=lambda: {
            StrategyState.IDLE.value: 0,
            StrategyState.WAIT_REENTRY.value: 0,
            StrategyState.ACCUMULATING.value: 0,
            StrategyState.DISTRIBUTING.value: 0,
            StrategyState.COOLDOWN.value: 0,
        }
    )


def resolve_start_balances(
    reference_price: float,
    start_usdc: float | None = None,
    start_eth: float | None = None,
    start_eth_usd: float | None = None,
) -> tuple[float, float]:
    resolved_usdc = START_USDC if start_usdc is None else start_usdc
    resolved_eth = START_ETH if start_eth is None else start_eth
    resolved_eth_usd = START_ETH_USD if start_eth_usd is None else start_eth_usd

    if resolved_eth_usd > 0:
        if reference_price <= 0:
            raise ValueError("reference_price must be positive when START_ETH_USD is enabled")
        resolved_eth += resolved_eth_usd / reference_price

    return resolved_usdc, resolved_eth


def create_runtime(
    bootstrap_prices: list[float] | None = None,
    reference_price: float | None = None,
    start_usdc: float | None = None,
    start_eth: float | None = None,
    start_eth_usd: float | None = None,
    cycle_seconds: float | None = None,
    enable_reentry_engine: bool | None = None,
    enable_decision_engine: bool | None = None,
    enable_execution_engine: bool | None = None,
    enable_trade_filter: bool | None = None,
    enable_inventory_manager: bool | None = None,
    enable_state_machine: bool | None = None,
    execution_timeframe_seconds: float | None = None,
    trend_timeframe_seconds: float | None = None,
    confirmation_timeframe_seconds: float | None = None,
    enable_trend_timeframe_filter: bool | None = None,
    enable_confirmation_filter: bool | None = None,
    telegram_notifier: TelegramNotifier | None = None,
) -> BotRuntime:
    resolved_start_eth_usd = START_ETH_USD if start_eth_usd is None else start_eth_usd

    if reference_price is None and bootstrap_prices:
        reference_price = bootstrap_prices[-1]

    if reference_price is None:
        if resolved_start_eth_usd > 0:
            raise ValueError("reference_price is required when START_ETH_USD is enabled")
        reference_price = 1.0

    resolved_start_usdc, resolved_start_eth = resolve_start_balances(
        reference_price=reference_price,
        start_usdc=start_usdc,
        start_eth=start_eth,
        start_eth_usd=resolved_start_eth_usd,
    )
    resolved_cycle_seconds = LOOP_SECONDS if cycle_seconds is None or cycle_seconds <= 0 else cycle_seconds
    resolved_enable_reentry = (
        REENTRY_ENGINE_ENABLED if enable_reentry_engine is None else enable_reentry_engine
    )
    resolved_enable_decision = (
        DECISION_ENGINE_ENABLED if enable_decision_engine is None else enable_decision_engine
    )
    resolved_enable_execution = (
        EXECUTION_ENGINE_ENABLED if enable_execution_engine is None else enable_execution_engine
    )
    resolved_enable_trade_filter = (
        TRADE_FILTER_ENABLED if enable_trade_filter is None else enable_trade_filter
    )
    resolved_enable_inventory_manager = (
        INVENTORY_MANAGER_ENABLED if enable_inventory_manager is None else enable_inventory_manager
    )
    resolved_enable_state_machine = STATE_MACHINE_ENABLED if enable_state_machine is None else enable_state_machine
    resolved_execution_timeframe_seconds = (
        EXECUTION_TIMEFRAME_SECONDS
        if execution_timeframe_seconds is None or execution_timeframe_seconds <= 0
        else execution_timeframe_seconds
    )
    resolved_trend_timeframe_seconds = (
        TREND_FILTER_TIMEFRAME_SECONDS
        if trend_timeframe_seconds is None or trend_timeframe_seconds <= 0
        else trend_timeframe_seconds
    )
    resolved_confirmation_timeframe_seconds = (
        CONFIRMATION_TIMEFRAME_SECONDS
        if confirmation_timeframe_seconds is None or confirmation_timeframe_seconds <= 0
        else confirmation_timeframe_seconds
    )
    resolved_enable_trend_timeframe_filter = (
        ENABLE_TREND_TIMEFRAME_FILTER
        if enable_trend_timeframe_filter is None
        else enable_trend_timeframe_filter
    )
    resolved_enable_confirmation_filter = (
        ENABLE_EXECUTION_CONFIRMATION
        if enable_confirmation_filter is None
        else enable_confirmation_filter
    )
    portfolio = Portfolio(resolved_start_usdc, resolved_start_eth)
    performance = PerformanceTracker(
        start_usdc=resolved_start_usdc,
        start_eth=resolved_start_eth,
        start_price=reference_price,
    )
    engine = PaperEngine(portfolio, MAKER_FEE_BPS, TAKER_FEE_BPS)
    execution_engine = ExecutionEngine(MAKER_FEE_BPS, TAKER_FEE_BPS, TRADE_SIZE_USD)
    execution_router = ExecutionRouter()
    regime_detector = RegimeDetector()
    edge_filter = EdgeFilter(
        policy_engine=execution_router.policy_engine,
        quote_validator=execution_router.quote_validator,
        slippage_guard=execution_router.slippage_guard,
        mev_risk_engine=execution_router.mev_risk_engine,
    )
    signal_gate = SignalGate()
    reentry_engine = ReentryEngine(resolved_cycle_seconds)
    state_machine = StateMachineEngine(resolved_cycle_seconds)
    trade_filter = TradeFilter(resolved_cycle_seconds)
    inventory_manager = InventoryManager()
    decision_engine = DecisionEngine(trade_filter, reentry_engine, inventory_manager)
    raw_prices = list(bootstrap_prices or [])
    timeframe_snapshot = build_timeframe_snapshot(
        raw_prices,
        cycle_seconds=resolved_cycle_seconds,
        execution_timeframe_seconds=resolved_execution_timeframe_seconds,
        trend_timeframe_seconds=resolved_trend_timeframe_seconds,
        confirmation_timeframe_seconds=resolved_confirmation_timeframe_seconds,
        enable_trend_filter=resolved_enable_trend_timeframe_filter,
        enable_confirmation=resolved_enable_confirmation_filter,
    )
    prices = list(timeframe_snapshot.execution_prices)
    trend_prices = list(timeframe_snapshot.trend_prices or prices)
    confirmation_prices = list(timeframe_snapshot.confirmation_prices or prices)
    _trim_price_history(prices)
    return BotRuntime(
        portfolio=portfolio,
        engine=engine,
        performance=performance,
        intelligence=IntelligenceEngine(),
        decision_engine=decision_engine,
        regime_detector=regime_detector,
        edge_filter=edge_filter,
        signal_gate=signal_gate,
        execution_engine=execution_engine,
        execution_router=execution_router,
        reentry_engine=reentry_engine,
        state_machine=state_machine,
        trade_filter=trade_filter,
        inventory_manager=inventory_manager,
        raw_prices=raw_prices,
        prices=prices,
        trend_prices=trend_prices,
        confirmation_prices=confirmation_prices,
        cycle_seconds=resolved_cycle_seconds,
        execution_timeframe_seconds=resolved_execution_timeframe_seconds,
        trend_timeframe_seconds=resolved_trend_timeframe_seconds,
        confirmation_timeframe_seconds=resolved_confirmation_timeframe_seconds,
        enable_trend_timeframe_filter=resolved_enable_trend_timeframe_filter,
        enable_confirmation_filter=resolved_enable_confirmation_filter,
        enable_reentry_engine=resolved_enable_reentry,
        enable_decision_engine=resolved_enable_decision,
        enable_execution_engine=resolved_enable_execution,
        enable_trade_filter=resolved_enable_trade_filter,
        enable_inventory_manager=resolved_enable_inventory_manager,
        enable_state_machine=resolved_enable_state_machine,
        start_usdc=resolved_start_usdc,
        start_eth=resolved_start_eth,
        telegram_notifier=telegram_notifier,
        current_execution_bucket_count=timeframe_snapshot.execution_bucket_count,
        current_trend_bucket_count=timeframe_snapshot.trend_bucket_count,
        current_confirmation_bucket_count=timeframe_snapshot.confirmation_bucket_count,
        last_mid=reference_price,
    )


def _trim_price_history(prices: list[float]) -> None:
    risk_helpers.trim_price_history(prices)


def _strategy_mid(runtime: BotRuntime, fallback_mid: float) -> float:
    if runtime.prices:
        return runtime.prices[-1]
    return fallback_mid


def _regime_prices(runtime: BotRuntime) -> list[float]:
    if runtime.enable_trend_timeframe_filter:
        return runtime.trend_prices
    return runtime.prices


def _refresh_timeframe_prices(runtime: BotRuntime, mid: float) -> None:
    runtime.last_mid = mid
    runtime.raw_prices.append(mid)
    raw_window_rows = required_bootstrap_price_rows(
        cycle_seconds=runtime.cycle_seconds,
        configured_rows=PRICE_WINDOW_SIZE,
        execution_timeframe_seconds=runtime.execution_timeframe_seconds,
        trend_timeframe_seconds=runtime.trend_timeframe_seconds,
        confirmation_timeframe_seconds=runtime.confirmation_timeframe_seconds,
        enable_trend_filter=runtime.enable_trend_timeframe_filter,
        enable_confirmation=runtime.enable_confirmation_filter,
    ) + 4
    overflow = len(runtime.raw_prices) - raw_window_rows
    if overflow > 0:
        del runtime.raw_prices[:overflow]

    timeframe_snapshot = build_timeframe_snapshot(
        runtime.raw_prices,
        cycle_seconds=runtime.cycle_seconds,
        execution_timeframe_seconds=runtime.execution_timeframe_seconds,
        trend_timeframe_seconds=runtime.trend_timeframe_seconds,
        confirmation_timeframe_seconds=runtime.confirmation_timeframe_seconds,
        enable_trend_filter=runtime.enable_trend_timeframe_filter,
        enable_confirmation=runtime.enable_confirmation_filter,
    )
    runtime.prices = list(timeframe_snapshot.execution_prices)
    runtime.trend_prices = list(timeframe_snapshot.trend_prices or runtime.prices)
    runtime.confirmation_prices = list(timeframe_snapshot.confirmation_prices or runtime.prices)
    _trim_price_history(runtime.prices)
    runtime.current_execution_bucket_count = timeframe_snapshot.execution_bucket_count
    runtime.current_trend_bucket_count = timeframe_snapshot.trend_bucket_count
    runtime.current_confirmation_bucket_count = timeframe_snapshot.confirmation_bucket_count


def _refresh_timeframe_signals(runtime: BotRuntime) -> None:
    trend_prices = runtime.trend_prices if runtime.enable_trend_timeframe_filter else runtime.prices
    runtime.current_trend_short_ma = ema(trend_prices, SHORT_MA_WINDOW) if trend_prices else 0.0
    runtime.current_trend_long_ma = ema(trend_prices, LONG_MA_WINDOW) if trend_prices else 0.0
    gap_bps = 0.0
    if runtime.current_trend_long_ma > 0:
        gap_bps = ((runtime.current_trend_short_ma - runtime.current_trend_long_ma) / runtime.current_trend_long_ma) * 10000.0
    if gap_bps > max(EMA_RANGE_BAND_BPS, 0.5):
        runtime.current_trend_bias = "buy_only"
    elif gap_bps < -max(EMA_RANGE_BAND_BPS, 0.5):
        runtime.current_trend_bias = "sell_only"
    else:
        runtime.current_trend_bias = "range"

    runtime.current_confirmation_momentum_bps = calculate_recent_momentum_bps(runtime.confirmation_prices)
    runtime.current_confirmation_slowing = detect_momentum_slowing(runtime.confirmation_prices)


def _trend_filter_allows_buy(runtime: BotRuntime) -> bool:
    if not runtime.enable_trend_timeframe_filter:
        return True
    return runtime.current_trend_bias in {"buy_only", "range"}


def _trend_filter_allows_sell(runtime: BotRuntime) -> bool:
    if not runtime.enable_trend_timeframe_filter:
        return True
    return runtime.current_trend_bias in {"sell_only", "range"}


def _account_state(runtime: BotRuntime, mid: float) -> tuple[float, float, float]:
    return risk_helpers.account_state(runtime, mid)


def _track_runtime_state(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    equity_usd: float,
    pnl_usd: float,
    record_equity: bool = False,
) -> None:
    risk_helpers.track_runtime_state(
        runtime,
        cycle_index,
        mid,
        inventory_usd,
        equity_usd,
        pnl_usd,
        record_equity=record_equity,
    )


def _track_inventory_ratio(runtime: BotRuntime, inventory_ratio: float) -> None:
    risk_helpers.track_inventory_ratio(runtime, inventory_ratio)


def _cycles_for_minutes(runtime: BotRuntime, minutes: float) -> int:
    return risk_helpers.cycles_for_minutes(runtime, minutes)


def _reentry_budget_usd(
    runtime: BotRuntime,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    fallback_trade_size_usd: float,
) -> float:
    return risk_helpers.reentry_budget_usd(
        runtime,
        inventory_usd,
        effective_max_inventory_usd,
        fallback_trade_size_usd,
    )


def _activate_reentry_state(
    runtime: BotRuntime,
    cycle_index: int,
    sell_price: float,
    sell_size_usd: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    fallback_trade_size_usd: float,
) -> None:
    risk_helpers.activate_reentry_state(
        runtime,
        cycle_index,
        sell_price,
        sell_size_usd,
        inventory_usd,
        effective_max_inventory_usd,
        fallback_trade_size_usd,
    )


def _clear_profit_lock_state(runtime: BotRuntime) -> None:
    risk_helpers.clear_profit_lock_state(runtime)


def _reset_profit_lock_state(runtime: BotRuntime, anchor_price: float) -> None:
    risk_helpers.reset_profit_lock_state(runtime, anchor_price)


def _update_reentry_state(runtime: BotRuntime, mid: float) -> None:
    risk_helpers.update_reentry_state(runtime, mid)


def _update_profit_lock_state(runtime: BotRuntime, mid: float) -> None:
    risk_helpers.update_profit_lock_state(runtime, mid)


def _profit_lock_anchor_price(runtime: BotRuntime) -> float | None:
    return risk_helpers.profit_lock_anchor_price(runtime)


def _current_profit_pct(runtime: BotRuntime, mid: float) -> float | None:
    return risk_helpers.current_profit_pct(runtime, mid)


def _position_hold_minutes(runtime: BotRuntime, cycle_index: int) -> float:
    return risk_helpers.position_hold_minutes(runtime, cycle_index)


def _time_in_state_seconds(runtime: BotRuntime, cycle_index: int) -> float:
    return risk_helpers.time_in_state_seconds(runtime, cycle_index)


def _last_transition(runtime: BotRuntime) -> str:
    return risk_helpers.last_transition(runtime)


def _sync_daily_risk_state(runtime: BotRuntime, equity_usd: float) -> float:
    return risk_helpers.sync_daily_risk_state(runtime, equity_usd)


def _evaluate_runtime_limits(runtime: BotRuntime, inventory_usd: float, equity_usd: float):
    return risk_helpers.evaluate_runtime_limits(
        runtime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )


def _evaluate_trade_limits(
    runtime: BotRuntime,
    *,
    side: str,
    trade_size_usd: float,
    inventory_usd: float,
    equity_usd: float,
):
    return risk_helpers.evaluate_trade_limits(
        runtime,
        side=side,
        trade_size_usd=trade_size_usd,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )


def _risk_limit_filter_values(
    runtime: BotRuntime,
    *,
    inventory_usd: float,
    equity_usd: float,
    side: str = "",
    trade_size_usd: float = 0.0,
) -> dict[str, object]:
    return risk_helpers.risk_limit_filter_values(
        runtime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
        side=side,
        trade_size_usd=trade_size_usd,
    )


def _notify_risk_limit_stop(
    runtime: BotRuntime,
    *,
    cycle_index: int,
    reason: str,
    details: str,
) -> None:
    runtime.risk_stop_active = True
    runtime.risk_stop_reason = reason
    runtime.risk_stop_message = details
    log(f"RISK LIMIT STOP | cycle {cycle_index} | reason {reason} | {details}")

    if reason in runtime.risk_alert_sent_reasons:
        return

    runtime.risk_alert_sent_reasons.add(reason)
    notifier = runtime.telegram_notifier
    if notifier is None:
        return

    try:
        if hasattr(notifier, "notify_risk_limit"):
            notifier.notify_risk_limit(
                reason=reason,
                details=details,
                runtime=runtime,
            )
        else:
            notifier.notify_error("risk_limit", f"{reason} | {details}")
    except Exception as exc:  # noqa: BLE001 - notifications must never break shutdown
        log(f"Telegram risk limit notify failed | cycle {cycle_index} | error {exc}")


def _update_drawdown_guard(
    runtime: BotRuntime,
    *,
    cycle_index: int,
    equity_usd: float,
) -> None:
    equity_peak = runtime.equity_peak if runtime.equity_peak is not None else equity_usd
    if equity_peak > 0:
        runtime.current_drawdown_pct = max((equity_peak - equity_usd) / equity_peak, 0.0)
    else:
        runtime.current_drawdown_pct = 0.0

    next_stage = resolve_drawdown_stage(runtime.current_drawdown_pct)
    previous_stage = runtime.drawdown_guard_stage
    runtime.drawdown_guard_stage = next_stage

    if drawdown_stage_priority(next_stage) <= drawdown_stage_priority(previous_stage):
        return
    if next_stage == "normal":
        return

    log(
        f"DRAWDOWN GUARD | cycle {cycle_index} | stage {next_stage} | "
        f"drawdown {runtime.current_drawdown_pct:.2%}"
    )

    if next_stage != "pause":
        return

    notifier = runtime.telegram_notifier
    if notifier is None:
        return

    try:
        if hasattr(notifier, "notify_drawdown_alert"):
            notifier.notify_drawdown_alert(
                stage=next_stage,
                drawdown_pct=runtime.current_drawdown_pct,
                runtime=runtime,
            )
        else:
            notifier.notify_error("drawdown_guard", f"{next_stage} | {runtime.current_drawdown_pct:.2%}")
    except Exception as exc:  # noqa: BLE001 - notifications must never break the bot
        log(f"Telegram drawdown alert failed | cycle {cycle_index} | error {exc}")


def _stop_for_risk_limit(
    runtime: BotRuntime,
    *,
    cycle_index: int,
    reason: str,
    details: str,
    inventory_usd: float,
    equity_usd: float,
    side: str = "",
    trade_size_usd: float = 0.0,
    mode: str,
    intelligence,
    mid: float,
    source: str,
    spread: float,
    pnl_usd: float,
    equity_row_kwargs: dict[str, object],
    log_progress: bool,
) -> bool:
    risk_filter_values = _risk_limit_filter_values(
        runtime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
        side=side,
        trade_size_usd=trade_size_usd,
    )
    _record_trade_gate(
        runtime,
        allow_trade=False,
        block_reason=reason,
        filter_values=risk_filter_values,
    )
    runtime.last_buy_debug_reason = reason
    runtime.last_sell_debug_reason = reason
    _notify_risk_limit_stop(runtime, cycle_index=cycle_index, reason=reason, details=details)
    equity_row_kwargs.update(
        {
            "allow_trade": runtime.last_allow_trade,
            "block_reason": runtime.last_decision_block_reason,
            "filter_values": runtime.last_filter_values,
            "buy_debug_reason": runtime.last_buy_debug_reason,
            "sell_debug_reason": runtime.last_sell_debug_reason,
        }
    )
    if log_progress:
        _log_cycle(runtime, cycle_index, mode, intelligence, mid, source, equity_usd, pnl_usd, spread, inventory_usd)
        _log_trade_intent(runtime, cycle_index)
        _log_execution_decision(runtime, cycle_index)
    _append_equity_row(**equity_row_kwargs)
    return False


def _build_accumulating_failsafe_sell_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
) -> tuple[str | None, float]:
    return risk_helpers.build_accumulating_failsafe_sell_plan(runtime, cycle_index, mid)


def _reentry_pullback_price(last_sell_price: float | None) -> float | None:
    return strategy_helpers.reentry_pullback_price(last_sell_price)


def _base_sell_debug_reason(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    sell_enabled: bool,
    sell_state_allowed: bool,
    in_cooldown: bool,
    state_requires_reentry_only: bool,
) -> str:
    return strategy_helpers.base_sell_debug_reason(
        runtime,
        cycle_index,
        mid,
        sell_enabled=sell_enabled,
        sell_state_allowed=sell_state_allowed,
        in_cooldown=in_cooldown,
        state_requires_reentry_only=state_requires_reentry_only,
    )


def _finalize_sell_debug_reason(
    base_reason: str,
    action: str,
    sell_reason: str,
    selected_reason: str,
    allow_trade: bool,
    block_reason: str,
    sell_fill,
) -> str:
    return strategy_helpers.finalize_sell_debug_reason(
        base_reason=base_reason,
        action=action,
        sell_reason=sell_reason,
        selected_reason=selected_reason,
        allow_trade=allow_trade,
        block_reason=block_reason,
        sell_fill=sell_fill,
    )


def _base_buy_debug_reason(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    buy_enabled: bool,
    buy_state_allowed: bool,
    in_cooldown: bool,
    reentry_plan,
    partial_reset_reason: str | None,
    force_trade_candidate: DecisionOutcome | None,
    trend_signal_allows_buy: bool,
) -> str:
    return strategy_helpers.base_buy_debug_reason(
        runtime,
        cycle_index,
        mid,
        buy_enabled=buy_enabled,
        buy_state_allowed=buy_state_allowed,
        in_cooldown=in_cooldown,
        reentry_plan=reentry_plan,
        partial_reset_reason=partial_reset_reason,
        force_trade_candidate=force_trade_candidate,
        trend_signal_allows_buy=trend_signal_allows_buy,
    )


def _finalize_buy_debug_reason(
    base_reason: str,
    action: str,
    buy_reason: str,
    selected_reason: str,
    allow_trade: bool,
    block_reason: str,
    buy_fill,
) -> str:
    return strategy_helpers.finalize_buy_debug_reason(
        base_reason=base_reason,
        action=action,
        buy_reason=buy_reason,
        selected_reason=selected_reason,
        allow_trade=allow_trade,
        block_reason=block_reason,
        buy_fill=buy_fill,
    )


def _serialize_buy_zones(state: ReentryState) -> str:
    return logging_helpers.serialize_buy_zones(state)


def _serialize_profit_lock_state(state: ProfitLockState) -> str:
    return logging_helpers.serialize_profit_lock_state(state)


def _trade_reason_category(mode: str, trade_reason: str) -> str:
    return strategy_helpers.trade_reason_category(mode, trade_reason)


def _record_decision(runtime: BotRuntime, decision: DecisionOutcome) -> None:
    runtime.last_decision_source = decision.source
    runtime.last_decision_reason = decision.reason
    runtime.last_decision_size_usd = decision.size_usd
    runtime.last_final_action = decision.action
    runtime.last_overridden_signals = json.dumps(decision.overridden_signals, separators=(",", ":"))
    runtime.last_decision_block_reason = decision.block_reason


def _serialize_filter_values(filter_values: dict[str, object] | None) -> str:
    return logging_helpers.serialize_filter_values(filter_values)


def _record_trade_gate(
    runtime: BotRuntime,
    allow_trade: bool,
    block_reason: str = "",
    filter_values: dict[str, object] | None = None,
) -> None:
    signal_block_reason = ""
    gate = runtime.current_signal_gate_decision
    if not allow_trade:
        signal_block_reason = (
            block_reason
            or (gate.blocked_reason if gate is not None and not gate.allow_trade else "")
            or runtime.last_execution_analytics.trade_blocked_reason
            or runtime.last_decision_reason
            or "trade_blocked"
        )
    trade_blocked_reason = ""
    if not allow_trade:
        trade_blocked_reason = (
            signal_block_reason
            or runtime.last_execution_analytics.trade_blocked_reason
            or runtime.last_decision_reason
            or "trade_blocked"
        )
    trade_gate_reason = (
        runtime.last_decision_reason
        if allow_trade and runtime.last_final_action in {"BUY", "SELL"}
        else (trade_blocked_reason or runtime.last_decision_reason or "trade_blocked")
    )
    normalized_filter_values = _merge_filter_values(
        filter_values,
        **_sizing_filter_values(runtime),
        **_signal_filter_values(runtime),
        decision_action=runtime.last_final_action,
        decision_reason=runtime.last_decision_reason,
        decision_source=runtime.last_decision_source,
        decision_size_usd=round(runtime.last_decision_size_usd, 6),
        trade_gate="allow" if allow_trade else "blocked",
        trade_gate_reason=trade_gate_reason,
    )
    normalized_filter_values = _merge_filter_values(
        normalized_filter_values,
        signal_block_reason=signal_block_reason,
        trade_blocked_reason=trade_blocked_reason,
    )
    runtime.last_allow_trade = allow_trade
    runtime.current_signal_block_reason = signal_block_reason
    runtime.last_decision_block_reason = block_reason
    runtime.last_filter_values = _serialize_filter_values(normalized_filter_values)


def _merge_filter_values(
    base: dict[str, object] | None,
    **updates: object,
) -> dict[str, object]:
    return logging_helpers.merge_filter_values(base, **updates)


def _current_sizing(runtime: BotRuntime) -> SizingSnapshot | None:
    return runtime.current_sizing


def _sizing_filter_values(runtime: BotRuntime, *, use_force: bool = False) -> dict[str, object]:
    sizing = _current_sizing(runtime)
    if sizing is None:
        return {}
    return sizing_log_fields(sizing, use_force=use_force)


def _signal_filter_values(runtime: BotRuntime) -> dict[str, object]:
    trade_blocked_reason = ""
    gate = runtime.current_signal_gate_decision
    if gate is not None and not gate.allow_trade:
        trade_blocked_reason = gate.blocked_reason
    elif runtime.last_execution_analytics.trade_blocked_reason:
        trade_blocked_reason = runtime.last_execution_analytics.trade_blocked_reason
    elif runtime.last_decision_block_reason and runtime.last_final_action not in {"BUY", "SELL"}:
        trade_blocked_reason = runtime.last_decision_block_reason

    filter_values: dict[str, object] = {
        "consecutive_losses": runtime.loss_streak,
        "loss_pause_remaining": round(runtime.loss_pause_remaining_minutes, 6),
        "detected_regime": runtime.current_detected_regime,
        "inventory_state": runtime.current_inventory_state,
        "active_regime": runtime.current_active_regime,
        "trend_direction": runtime.current_trend_direction,
        "zone": runtime.current_zone,
        "range_location": runtime.current_range_location,
        "volatility_bucket": runtime.current_volatility_bucket,
        "activity_state": runtime.current_activity_state,
        "inventory_limit_state": runtime.current_inventory_limit_state,
        "soft_inventory_limit_usd": round(runtime.current_soft_inventory_limit_usd, 6),
        "hard_inventory_limit_usd": round(runtime.current_hard_inventory_limit_usd, 6),
        "entry_threshold_bps": round(runtime.current_entry_threshold_bps, 6),
        "min_edge_bps": round(runtime.current_min_edge_bps, 6),
        "inventory_drift_pct": round(runtime.current_inventory_drift_pct, 6),
        "signal_block_reason": runtime.current_signal_block_reason,
        "inactivity_fallback_active": runtime.current_inactivity_fallback_active,
        "hold_minutes": round(runtime.current_hold_minutes, 6),
        "raw_signal": runtime.last_raw_signal,
        "trade_blocked_reason": trade_blocked_reason,
        "execution_timeframe_seconds": round(runtime.execution_timeframe_seconds, 6),
        "trend_timeframe_seconds": round(runtime.trend_timeframe_seconds, 6),
        "confirmation_timeframe_seconds": round(runtime.confirmation_timeframe_seconds, 6),
        "trend_filter_enabled": runtime.enable_trend_timeframe_filter,
        "confirmation_filter_enabled": runtime.enable_confirmation_filter,
        "upper_tf_short_ma": round(runtime.current_trend_short_ma, 6),
        "upper_tf_long_ma": round(runtime.current_trend_long_ma, 6),
        "upper_tf_bias": runtime.current_trend_bias,
        "confirmation_momentum_bps": round(runtime.current_confirmation_momentum_bps, 6),
        "confirmation_slowing": runtime.current_confirmation_slowing,
        "execution_bucket_count": runtime.current_execution_bucket_count,
        "trend_bucket_count": runtime.current_trend_bucket_count,
        "confirmation_bucket_count": runtime.current_confirmation_bucket_count,
    }
    regime = runtime.current_regime_assessment
    if regime is not None:
        filter_values.update(
            {
                "market_regime": regime.market_regime,
                "execution_regime": regime.execution_regime,
                "trend_direction_detail": regime.trend_direction,
                "range_location_detail": regime.range_location,
                "regime_confidence": round(regime.regime_confidence, 6),
                "range_width_pct": round(regime.range_width_pct, 6),
                "net_move_pct": round(regime.net_move_pct, 6),
                "direction_consistency": round(regime.direction_consistency, 6),
                "volatility_score": round(regime.volatility_score, 6),
                "price_position_pct": round(regime.price_position_pct, 6),
                "mean_reversion_distance_pct": round(regime.mean_reversion_distance_pct, 6),
            }
        )
    edge = runtime.current_edge_assessment
    if edge is not None:
        filter_values.update(
            {
                "edge_score": round(edge.edge_score, 6),
                "expected_edge_usd": round(edge.expected_edge_usd, 6),
                "expected_edge_bps": round(edge.expected_edge_bps, 6),
                "cost_estimate_usd": round(edge.cost_estimate_usd, 6),
                "slippage_estimate_bps": round(edge.slippage_estimate_bps, 6),
                "mev_risk_score": round(edge.mev_risk_score, 6),
                "entry_edge_bps": round(edge.expected_edge_bps, 6),
                "entry_edge_usd": round(edge.expected_edge_usd, 6),
            }
        )
    if gate is not None:
        filter_values.update(
            {
                "gate_decision": "allow" if gate.allow_trade else "reject",
                "approved_mode": gate.approved_mode,
                "gate_blocked_reason": gate.blocked_reason,
            }
        )
    return filter_values


def _paper_mode_enabled() -> bool:
    return BOT_MODE.strip().lower().startswith("paper")


def _intelligence_active_regime(intelligence) -> str:
    active_regime = getattr(intelligence, "active_regime", "")
    if active_regime:
        return active_regime
    mode = getattr(intelligence, "mode", "")
    regime = getattr(intelligence, "regime", "")
    if mode == "NO_TRADE" or regime == "RISK_OFF":
        return "NO_TRADE"
    if mode == "TREND_UP" or regime == "TREND":
        return "TREND"
    return "RANGE"


def _intelligence_trend_direction(intelligence, regime_assessment: MarketRegimeAssessment | None = None) -> str:
    trend_direction = getattr(intelligence, "trend_direction", "")
    if trend_direction:
        return trend_direction
    current_mode = getattr(intelligence, "current_mode", getattr(intelligence, "mode", ""))
    if current_mode == "TREND_DOWN":
        return "down"
    if current_mode == "TREND_UP":
        return "up"
    if regime_assessment is not None:
        return regime_assessment.trend_direction
    return "neutral"


def _recent_trade_cycles(runtime: BotRuntime) -> list[int]:
    return [trade.cycle_index for trade in runtime.performance.trade_history]


def _inventory_neutral_band_ratio() -> float:
    return max(INVENTORY_NEUTRAL_BAND_PCT, 0.0) / 100.0


def _apply_inventory_drift_state(
    runtime: BotRuntime,
    *,
    inventory_ratio: float,
    target_inventory_pct: float,
) -> float:
    inventory_delta = inventory_ratio - target_inventory_pct
    neutral_band_ratio = _inventory_neutral_band_ratio()
    if inventory_delta >= neutral_band_ratio:
        runtime.current_inventory_state = "base_heavy"
    elif inventory_delta <= -neutral_band_ratio:
        runtime.current_inventory_state = "quote_heavy"
    else:
        runtime.current_inventory_state = "balanced"
    runtime.current_inventory_drift_pct = inventory_delta * 100.0
    return inventory_delta


def _inventory_drift_block_reason(runtime: BotRuntime, action: str, trade_reason: str) -> str:
    if action not in {"BUY", "SELL"}:
        return ""
    if trade_reason in INVENTORY_DRIFT_GUARD_EXEMPT_REASONS:
        return ""
    neutral_band_pct = _inventory_neutral_band_ratio() * 100.0
    drift_pct = runtime.current_inventory_drift_pct
    if action == "BUY" and drift_pct >= neutral_band_pct:
        return "inventory_drift_same_side_buy_blocked"
    if action == "SELL" and drift_pct <= -neutral_band_pct:
        return "inventory_drift_same_side_sell_blocked"
    return ""


def _apply_inventory_drift_guard(
    runtime: BotRuntime,
    candidate: DecisionOutcome | None,
) -> tuple[DecisionOutcome | None, str]:
    if candidate is None:
        return None, ""
    blocked_reason = _inventory_drift_block_reason(runtime, candidate.action, candidate.reason)
    if blocked_reason:
        return None, blocked_reason
    return candidate, ""


def _forced_inventory_reduce_sell_price(
    *,
    strategy_mid: float,
    strategy_sell_price: float,
    min_sell_price: float | None,
) -> float:
    aggressive_target = strategy_mid * (1.0 + (max(INVENTORY_FORCED_REDUCE_AGGRESSION_BPS, 0.0) / 10_000.0))
    forced_price = min(strategy_sell_price, aggressive_target)
    if min_sell_price is not None:
        forced_price = max(forced_price, min_sell_price)
    return forced_price


def _apply_runtime_regime_context(
    runtime: BotRuntime,
    *,
    intelligence,
    regime_assessment: MarketRegimeAssessment | None,
    inventory_profile,
    cycle_index: int,
) -> None:
    inventory_ratio = getattr(inventory_profile, "inventory_ratio", 0.0)
    target_inventory_pct = getattr(intelligence, "target_inventory_pct", 0.0)
    _apply_inventory_drift_state(
        runtime,
        inventory_ratio=inventory_ratio,
        target_inventory_pct=target_inventory_pct,
    )
    if regime_assessment is not None:
        runtime.current_detected_regime = regime_assessment.market_regime
        runtime.current_active_regime = _intelligence_active_regime(intelligence) or regime_assessment.execution_regime
        runtime.current_trend_direction = _intelligence_trend_direction(intelligence, regime_assessment)
        runtime.current_range_location = regime_assessment.range_location
    else:
        runtime.current_detected_regime = _intelligence_active_regime(intelligence)
        runtime.current_active_regime = _intelligence_active_regime(intelligence)
        runtime.current_trend_direction = _intelligence_trend_direction(intelligence, None)
        runtime.current_range_location = "middle"
    runtime.current_zone = resolve_logging_zone(runtime.current_range_location)
    runtime.current_volatility_bucket = getattr(intelligence, "volatility_state", "NORMAL")
    runtime.current_inactivity_fallback_active = _inactivity_fallback_allowed(
        runtime,
        intelligence=intelligence,
        regime_assessment=regime_assessment,
        inventory_profile=inventory_profile,
        cycle_index=cycle_index,
    )
    runtime.current_activity_state = "inactivity_fallback" if runtime.current_inactivity_fallback_active else getattr(
        intelligence,
        "activity_state",
        "normal",
    )
    runtime.current_entry_threshold_bps = resolve_effective_entry_threshold_bps(
        runtime.current_active_regime,
        runtime.current_volatility_bucket,
        runtime.current_activity_state,
    )
    runtime.current_min_edge_bps = resolve_effective_min_edge_bps(
        runtime.current_active_regime,
        runtime.current_activity_state,
    )
    runtime.current_soft_inventory_limit_usd = getattr(inventory_profile, "soft_limit_usd", 0.0)
    runtime.current_hard_inventory_limit_usd = getattr(inventory_profile, "hard_limit_usd", 0.0)
    if getattr(inventory_profile, "force_limit_hit", False):
        runtime.current_inventory_limit_state = "force_limit"
    elif getattr(inventory_profile, "hard_limit_hit", False):
        runtime.current_inventory_limit_state = "hard_limit"
    elif getattr(inventory_profile, "soft_limit_hit", False):
        runtime.current_inventory_limit_state = "soft_limit"
    else:
        runtime.current_inventory_limit_state = "normal"
    runtime.current_hold_minutes = _position_hold_minutes(runtime, cycle_index)
    inventory_drift = abs(inventory_ratio - max(target_inventory_pct, 0.0))
    runtime.cumulative_inventory_drift += inventory_drift
    runtime.max_inventory_drift = max(runtime.max_inventory_drift, inventory_drift)
    runtime.inventory_drift_samples += 1


def _range_entry_distance_bps(regime_assessment: MarketRegimeAssessment | None) -> float:
    if regime_assessment is None:
        return 0.0
    return max(-(regime_assessment.mean_reversion_distance_pct * 100.0), 0.0)


def _inactivity_fallback_allowed(
    runtime: BotRuntime,
    *,
    intelligence,
    regime_assessment: MarketRegimeAssessment | None,
    inventory_profile,
    cycle_index: int,
) -> bool:
    if regime_assessment is None or inventory_profile is None:
        return False
    if INACTIVITY_FORCE_ENTRY_MINUTES <= 0:
        return False
    if _intelligence_active_regime(intelligence) != "RANGE":
        return False
    if regime_assessment.execution_regime != "RANGE":
        return False
    if _minutes_since_last_trade(runtime, cycle_index) < INACTIVITY_FORCE_ENTRY_MINUTES:
        return False
    if str(getattr(intelligence, "volatility_state", "")).upper() == "EXTREME":
        return False
    if regime_assessment.market_regime == "CHOP":
        return False
    if getattr(regime_assessment, "shock_active", False):
        return False
    if regime_assessment.body_to_wick_ratio > max(REGIME_MAX_WICK_TO_BODY_RATIO, 0.0):
        return False
    if regime_assessment.volatility_score >= 85.0:
        return False
    if getattr(inventory_profile, "hard_limit_hit", False):
        return False
    if getattr(inventory_profile, "reduction_only", False):
        return False
    return True


def _range_entry_signal_allowed(
    runtime: BotRuntime,
    *,
    intelligence,
    regime_assessment: MarketRegimeAssessment | None,
    buy_state_allowed: bool,
    state_requires_reentry_only: bool,
    in_cooldown: bool,
) -> bool:
    if regime_assessment is None:
        return False
    if _intelligence_active_regime(intelligence) != "RANGE":
        return False
    if regime_assessment.execution_regime != "RANGE":
        return False
    if regime_assessment.range_location not in {"bottom", "lower"}:
        return False
    if _range_entry_distance_bps(regime_assessment) < max(runtime.current_entry_threshold_bps, 0.0):
        return False
    if in_cooldown or state_requires_reentry_only or not buy_state_allowed:
        return False
    return bool(getattr(intelligence, "buy_enabled", False))


def _range_sell_signal_allowed(intelligence, regime_assessment: MarketRegimeAssessment | None) -> bool:
    if regime_assessment is None:
        return False
    return (
        _intelligence_active_regime(intelligence) == "RANGE"
        and regime_assessment.execution_regime == "RANGE"
        and regime_assessment.price_position_pct >= RANGE_EXIT_MIN_POSITION_PCT
        and bool(getattr(intelligence, "sell_enabled", False))
    )


def _trend_rally_sell_signal_allowed(intelligence, regime_assessment: MarketRegimeAssessment | None) -> bool:
    if regime_assessment is None:
        return False
    return (
        _intelligence_active_regime(intelligence) == "TREND"
        and _intelligence_trend_direction(intelligence, regime_assessment) == "down"
        and regime_assessment.price_position_pct >= RANGE_MEAN_REVERSION_EXIT_POSITION_PCT
        and bool(getattr(intelligence, "sell_enabled", False))
    )


def _inventory_skew_strength(intelligence, inventory_profile) -> float:
    skew_strength = INVENTORY_SKEW_STRENGTH * getattr(intelligence, "inventory_skew_multiplier", 1.0)
    if getattr(inventory_profile, "hard_limit_hit", False):
        skew_strength *= INVENTORY_SKEW_ACCELERATION * 1.15
    elif getattr(inventory_profile, "soft_limit_hit", False):
        skew_strength *= INVENTORY_SKEW_ACCELERATION
    elif getattr(inventory_profile, "inventory_ratio", 0.0) < getattr(inventory_profile, "lower_bound", 0.0):
        skew_strength *= 1.20
    return skew_strength


def _record_opportunity_rejection(runtime: BotRuntime, cycle_index: int) -> None:
    if runtime.last_allow_trade:
        return
    raw_signal = runtime.last_raw_signal or ""
    if not raw_signal or raw_signal.startswith("NONE:"):
        return
    if runtime.last_rejection_cycle == cycle_index:
        return
    reason = runtime.last_decision_block_reason or runtime.last_decision_reason or "trade_rejected"
    runtime.last_rejection_cycle = cycle_index
    runtime.rejection_reason_counts[reason] = runtime.rejection_reason_counts.get(reason, 0) + 1
    log(
        f"{cycle_index} | opportunity_rejected raw {raw_signal} | reason {reason} | "
        f"active_regime {runtime.current_active_regime} | vol {runtime.current_volatility_bucket} | "
        f"inventory {runtime.current_inventory_state}/{runtime.current_inventory_limit_state}"
    )


def _update_runtime_sizing(runtime: BotRuntime, *, equity_usd: float, mid: float) -> SizingSnapshot:
    sizing = build_sizing_snapshot(
        current_equity_usd=equity_usd,
        mid_price=mid,
        portfolio_usdc=runtime.portfolio.usdc,
        portfolio_eth=runtime.portfolio.eth,
    )
    runtime.current_sizing = sizing
    runtime.engine.min_eth_reserve = sizing.base_reserve_eth
    runtime.execution_engine.base_trade_size_usd = max(sizing.trade_size_usd, sizing.min_notional_usd, 1e-9)
    return sizing


def _refresh_loss_pause_state(runtime: BotRuntime, cycle_index: int) -> None:
    runtime.loss_pause_remaining_minutes = loss_pause_remaining_minutes(
        cycle_index,
        runtime.loss_pause_until_cycle,
        runtime.cycle_seconds,
    )
    if runtime.loss_pause_remaining_minutes <= 0:
        runtime.loss_pause_until_cycle = None
        runtime.loss_pause_remaining_minutes = 0.0


def _raw_signal_label(decision: DecisionOutcome) -> str:
    action = decision.action or "NONE"
    source = decision.source or "-"
    reason = decision.reason or decision.block_reason or "-"
    return f"{action}:{source}:{reason}"


def _apply_signal_pipeline(
    runtime: BotRuntime,
    *,
    cycle_index: int,
    decision: DecisionOutcome,
    strategy_mode: str,
    intelligence,
    quote: Quote,
    mid: float,
    spread_bps: float,
    source: str,
    effective_max_inventory_usd: float,
    inventory_profile,
    inventory_usd: float,
) -> DecisionOutcome:
    runtime.last_raw_signal = _raw_signal_label(decision)
    _refresh_loss_pause_state(runtime, cycle_index)
    regime_assessment = runtime.current_regime_assessment or runtime.regime_detector.assess(_regime_prices(runtime))
    runtime.current_regime_assessment = regime_assessment
    runtime.current_edge_assessment = None
    runtime.current_signal_gate_decision = None

    if inventory_profile is not None:
        _apply_inventory_drift_state(
            runtime,
            inventory_ratio=inventory_profile.inventory_ratio,
            target_inventory_pct=getattr(intelligence, "target_inventory_pct", _current_sizing(runtime).target_base_pct),
        )

    if decision.action not in {"BUY", "SELL"} or decision.size_usd <= 0:
        runtime.current_signal_gate_decision = SignalGateDecision(
            allow_trade=False,
            approved_mode="skip",
            blocked_reason=decision.block_reason or "no_signal",
            gate_details={"gate_decision": "reject"},
        )
        decision.filter_values = _merge_filter_values(decision.filter_values, **_signal_filter_values(runtime))
        return decision

    execution_context = _build_execution_context(
        runtime=runtime,
        cycle_index=cycle_index,
        mode=strategy_mode,
        mid=mid,
        spread_bps=spread_bps,
        intelligence=intelligence,
        quote=quote,
        router_price=decision.order_price if decision.order_price > 0 else mid,
        source=source,
        effective_max_inventory_usd=effective_max_inventory_usd,
        size_usd=decision.size_usd,
    )
    edge_assessment = runtime.edge_filter.assess(
        signal=decision,
        context=execution_context,
        regime_assessment=regime_assessment,
        inventory_profile=inventory_profile,
        inventory_usd=inventory_usd,
        target_base_usd=_current_sizing(runtime).target_base_usd,
        consecutive_losses=runtime.loss_streak,
        last_loss_cycle=runtime.last_loss_cycle,
        last_loss_reason=runtime.last_loss_trade_reason,
        cycle_index=cycle_index,
        cycle_seconds=runtime.cycle_seconds,
        last_sell_price=runtime.reentry_state.last_sell_price,
        current_profit_pct=runtime.last_profit_pct,
        min_edge_multiplier=max(runtime.current_min_edge_bps / max(RANGE_MIN_EDGE_BPS, 0.5), 0.35),
    )
    runtime.current_edge_assessment = edge_assessment
    runtime.current_entry_edge_bps = edge_assessment.expected_edge_bps
    runtime.current_entry_edge_usd = edge_assessment.expected_edge_usd

    gate_decision = runtime.signal_gate.evaluate(
        signal=decision,
        strategy_mode=strategy_mode,
        regime_assessment=regime_assessment,
        edge_assessment=edge_assessment,
        inventory_ratio=inventory_profile.inventory_ratio,
        target_base_pct=_current_sizing(runtime).target_base_pct,
        consecutive_losses=runtime.loss_streak,
        loss_pause_remaining_minutes=runtime.loss_pause_remaining_minutes,
        short_ma=runtime.current_trend_short_ma,
        long_ma=runtime.current_trend_long_ma,
        momentum_bps=calculate_recent_momentum_bps(runtime.prices),
        confirmation_enabled=runtime.enable_confirmation_filter,
        confirmation_momentum_bps=runtime.current_confirmation_momentum_bps,
        confirmation_slowing=runtime.current_confirmation_slowing,
    )
    runtime.current_signal_gate_decision = gate_decision
    merged_filter_values = _merge_filter_values(decision.filter_values, **_signal_filter_values(runtime))
    merged_filter_values = _merge_filter_values(merged_filter_values, **gate_decision.gate_details)

    if (
        gate_decision.allow_trade
        and runtime.last_trade_execution_bucket is not None
        and runtime.current_execution_bucket_count > 0
        and runtime.last_trade_execution_bucket == runtime.current_execution_bucket_count
        and decision.reason not in FORCED_SELL_REASONS
    ):
        gate_decision = SignalGateDecision(
            allow_trade=False,
            approved_mode="skip",
            blocked_reason="already_traded_execution_candle",
            gate_details=_merge_filter_values(
                gate_decision.gate_details,
                gate_decision="reject",
                gate_blocked_reason="already_traded_execution_candle",
            ),
        )
        runtime.current_signal_gate_decision = gate_decision
        merged_filter_values = _merge_filter_values(merged_filter_values, **gate_decision.gate_details)

    if not gate_decision.allow_trade:
        return DecisionOutcome(
            action="NONE",
            size_usd=0.0,
            reason=decision.reason,
            source=decision.source,
            order_price=decision.order_price,
            inventory_cap_usd=decision.inventory_cap_usd,
            overridden_signals=decision.overridden_signals,
            block_reason=gate_decision.blocked_reason or decision.block_reason,
            allow_trade=False,
            filter_values=merged_filter_values,
        )

    return DecisionOutcome(
        action=decision.action,
        size_usd=decision.size_usd,
        reason=decision.reason,
        source=decision.source,
        order_price=decision.order_price,
        inventory_cap_usd=decision.inventory_cap_usd,
        overridden_signals=decision.overridden_signals,
        block_reason=decision.block_reason,
        allow_trade=True,
        filter_values=merged_filter_values,
    )


def _execution_pair() -> str:
    return execution_helpers.execution_pair()


def _estimate_execution_gas_gwei(volatility: float, spread_bps: float) -> float:
    return execution_helpers.estimate_execution_gas_gwei(volatility, spread_bps)


def _estimate_execution_liquidity_usd(
    effective_max_inventory_usd: float,
    size_usd: float,
    mid: float,
) -> float:
    return execution_helpers.estimate_execution_liquidity_usd(
        effective_max_inventory_usd,
        size_usd,
        mid,
    )


def _build_execution_context(
    runtime: BotRuntime,
    cycle_index: int,
    mode: str,
    mid: float,
    spread_bps: float,
    intelligence,
    quote: Quote,
    router_price: float,
    source: str,
    effective_max_inventory_usd: float,
    size_usd: float,
) -> ExecutionContext:
    return execution_helpers.build_execution_context(
        runtime,
        cycle_index,
        mode,
        mid,
        spread_bps,
        intelligence,
        quote,
        router_price,
        source,
        effective_max_inventory_usd,
        size_usd,
    )


def _route_execution_signal(
    runtime: BotRuntime,
    signal: ExecutionSignal,
    context: ExecutionContext,
) -> tuple[object | None, dict[str, object]]:
    return execution_helpers.route_execution_signal(runtime, signal, context)


def _apply_execution_result_to_order(order, execution_result) -> None:
    execution_helpers.apply_execution_result_to_order(order, execution_result)


def _build_execution_signal_metadata(
    runtime: BotRuntime,
    *,
    side: str,
    quote: Quote,
    size_usd: float,
    mode: str,
    trade_reason: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    metadata = dict(extra or {})
    if size_usd <= 0:
        return metadata

    execution_preview = runtime.execution_engine.build_decision(
        side=side,
        quote=quote,
        size_usd=size_usd,
        mode=mode,
        trade_reason=trade_reason,
        portfolio=runtime.portfolio,
        reentry_state=runtime.reentry_state,
    )
    metadata["expected_profit_pct"] = round(execution_preview.expected_profit_pct, 6)
    metadata["expected_profit_usd"] = round(max(size_usd * (execution_preview.expected_profit_pct / 100.0), 0.0), 6)
    metadata["execution_preview_type"] = execution_preview.execution_type
    return metadata


def _execution_metadata_filter_values(metadata: dict[str, object] | None) -> dict[str, object]:
    filter_values: dict[str, object] = {}
    for key in (
        "gas_price_gwei",
        "estimated_gas_cost_usd",
        "expected_profit_pct",
        "expected_profit_usd",
        "gas_to_profit_ratio",
    ):
        if not metadata or key not in metadata:
            continue
        value = metadata.get(key)
        if value is None or value == "":
            continue
        filter_values[key] = round(float(value), 6) if isinstance(value, (int, float)) else value
    return filter_values


def _build_chunk_sizes(runtime: BotRuntime, total_size_usd: float) -> list[float]:
    sizing = _current_sizing(runtime)
    max_trade_size_usd = max(sizing.max_trade_size_usd if sizing is not None else MAX_TRADE_SIZE_USD, 0.0)
    min_notional_usd = sizing.min_notional_usd if sizing is not None else MIN_ORDER_SIZE_USD
    if total_size_usd <= 0 or max_trade_size_usd <= 0 or total_size_usd <= max_trade_size_usd:
        return [max(total_size_usd, 0.0)]

    chunk_count = max(int(math.ceil(total_size_usd / max_trade_size_usd)), 1)
    equal_chunk_size = total_size_usd / chunk_count
    if equal_chunk_size < min_notional_usd:
        return [max_trade_size_usd]

    chunk_sizes = [round(equal_chunk_size, 6) for _ in range(max(chunk_count - 1, 0))]
    allocated = sum(chunk_sizes)
    chunk_sizes.append(max(total_size_usd - allocated, 0.0))
    return [size for size in chunk_sizes if size > 0]


def _apply_trade_size_guard(
    runtime: BotRuntime,
    order,
    *,
    cycle_index: int,
) -> tuple[list[float], dict[str, object]]:
    sizing = _current_sizing(runtime)
    max_trade_size_usd = max(sizing.max_trade_size_usd if sizing is not None else MAX_TRADE_SIZE_USD, 0.0)
    min_notional_usd = sizing.min_notional_usd if sizing is not None else MIN_ORDER_SIZE_USD
    if max_trade_size_usd <= 0 or order.size_usd <= 0 or order.size_usd <= max_trade_size_usd:
        return [max(order.size_usd, 0.0)], {}

    requested_size_usd = order.size_usd
    guard_values: dict[str, object] = {
        "risk_stop_size_exceeded": True,
        "requested_trade_size_usd": round(requested_size_usd, 6),
        "max_trade_size_usd": round(max_trade_size_usd, 6),
    }
    log(
        f"risk_stop_size_exceeded | cycle {cycle_index} | side {order.side} | "
        f"requested {requested_size_usd:.2f} | limit {max_trade_size_usd:.2f} | "
        f"reason {order.trade_reason or '-'}"
    )

    if order.side == "sell":
        chunk_sizes = _build_chunk_sizes(runtime, requested_size_usd)
        first_chunk_size = min(chunk_sizes[0], max_trade_size_usd)
        order.size_usd = first_chunk_size
        order.size_base = order.size_usd / order.price if order.price > 0 else 0.0
        guard_values.update(
            {
                "chunked_exit": len(chunk_sizes) > 1,
                "chunk_count": len(chunk_sizes),
                "reduced_trade_size_usd": round(first_chunk_size, 6),
                "size_clamped": True,
                "clamp_reason": "max_trade_size_pct",
            }
        )
        return chunk_sizes, guard_values

    order.size_usd = max_trade_size_usd
    order.size_base = order.size_usd / order.price if order.price > 0 else 0.0
    guard_values.update(
        {
            "trade_size_reduced": True,
            "reduced_trade_size_usd": round(order.size_usd, 6),
            "size_clamped": order.size_usd >= min_notional_usd,
            "clamp_reason": "max_trade_size_pct",
        }
    )
    return [order.size_usd], guard_values


def _notify_chunk_exit(
    runtime: BotRuntime,
    *,
    event: str,
    cycle_index: int,
    trade_reason: str,
    total_size_usd: float,
    completed_size_usd: float,
    chunk_index: int = 0,
    chunk_count: int = 0,
    chunk_size_usd: float = 0.0,
) -> None:
    log(
        f"{event} | cycle {cycle_index} | reason {trade_reason or '-'} | total {total_size_usd:.2f} | "
        f"completed {completed_size_usd:.2f} | chunk {chunk_index}/{chunk_count} | size {chunk_size_usd:.2f}"
    )
    notifier = runtime.telegram_notifier
    if notifier is None or not hasattr(notifier, "notify_chunk_exit"):
        return

    try:
        notifier.notify_chunk_exit(
            event=event,
            cycle_index=cycle_index,
            trade_reason=trade_reason,
            total_size_usd=total_size_usd,
            completed_size_usd=completed_size_usd,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            chunk_size_usd=chunk_size_usd,
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 - notifications must never break trading
        log(f"Telegram chunk exit notify failed | cycle {cycle_index} | error {exc}")


def _execute_sell_chunks(
    runtime: BotRuntime,
    *,
    cycle_index: int,
    mode: str,
    mid: float,
    sell_order,
    chunk_sizes: list[float],
    trade_logger,
    effective_max_inventory_usd: float,
    fallback_trade_size_usd: float,
):
    sizing = _current_sizing(runtime)
    min_notional_usd = sizing.min_notional_usd if sizing is not None else MIN_ORDER_SIZE_USD
    if not chunk_sizes:
        return None

    total_size_usd = sum(chunk_sizes)
    if len(chunk_sizes) > 1:
        _notify_chunk_exit(
            runtime,
            event="chunk_exit_started",
            cycle_index=cycle_index,
            trade_reason=sell_order.trade_reason,
            total_size_usd=total_size_usd,
            completed_size_usd=0.0,
            chunk_index=0,
            chunk_count=len(chunk_sizes),
            chunk_size_usd=0.0,
        )

    realized_pnl_before = runtime.portfolio.realized_pnl_usd
    completed_size_usd = 0.0
    last_fill = None
    for chunk_index, chunk_size_usd in enumerate(chunk_sizes, start=1):
        chunk_order = runtime.engine.create_order_from_decision(
            "SELL",
            sell_order.price,
            chunk_size_usd,
            mode,
        )
        chunk_order.fee_bps = sell_order.fee_bps
        chunk_order.execution_type = sell_order.execution_type
        chunk_order.slippage_bps = sell_order.slippage_bps
        chunk_order.trade_reason = sell_order.trade_reason
        _cap_inventory_preserving_sell_order(runtime, chunk_order, chunk_order.trade_reason)
        if chunk_order.size_usd < min_notional_usd:
            break
        if not runtime.engine.can_place_sell(chunk_order, mode):
            break

        fill = runtime.engine.simulate_fill(chunk_order, mid)
        realized_delta = runtime.portfolio.realized_pnl_usd - realized_pnl_before
        if fill.filled:
            post_sell_inventory_usd = runtime.portfolio.inventory_usd(mid)
            if _should_activate_reentry_after_sell(runtime, runtime.portfolio.eth, mid):
                runtime.reentry_engine.activate_after_sell(
                    state=runtime.reentry_state,
                    cycle_index=cycle_index,
                    sell_price=fill.price,
                    sell_size_usd=fill.size_usd,
                    budget_usd=_reentry_budget_usd(
                        runtime=runtime,
                        inventory_usd=post_sell_inventory_usd,
                        effective_max_inventory_usd=effective_max_inventory_usd,
                        fallback_trade_size_usd=fallback_trade_size_usd,
                    ),
                )
        trade_analysis = _record_fill(runtime, cycle_index, mode, fill, realized_delta)
        if fill.filled and runtime.enable_state_machine:
            runtime.state_machine.handle_sell_fill(
                context=runtime.state_context,
                cycle_index=cycle_index,
                trade_reason=sell_order.trade_reason,
                reentry_state=runtime.reentry_state,
                portfolio_eth=runtime.portfolio.eth,
                min_eth_reserve=runtime.engine.min_eth_reserve,
            )
            runtime.state_machine.maybe_enter_cooldown(runtime.state_context, cycle_index, runtime.loss_streak)
        _append_trade_row(
            trade_logger,
            cycle_index,
            runtime.state_context.current_state.value,
            mode,
            runtime.last_decision_source,
            runtime.last_final_action,
            runtime.last_overridden_signals,
            runtime.last_allow_trade,
            runtime.last_decision_block_reason,
            runtime.last_filter_values,
            fill,
            runtime.portfolio,
            runtime.last_execution_analytics,
            trade_analysis=trade_analysis,
        )
        if fill.filled:
            completed_size_usd += fill.size_usd
        last_fill = fill
        realized_pnl_before = runtime.portfolio.realized_pnl_usd

        if len(chunk_sizes) > 1:
            _notify_chunk_exit(
                runtime,
                event="chunk_exit_progress",
                cycle_index=cycle_index,
                trade_reason=sell_order.trade_reason,
                total_size_usd=total_size_usd,
                completed_size_usd=completed_size_usd,
                chunk_index=chunk_index,
                chunk_count=len(chunk_sizes),
                chunk_size_usd=chunk_size_usd,
            )
        if not fill.filled:
            break

    if len(chunk_sizes) > 1:
        _notify_chunk_exit(
            runtime,
            event="chunk_exit_completed",
            cycle_index=cycle_index,
            trade_reason=sell_order.trade_reason,
            total_size_usd=total_size_usd,
            completed_size_usd=completed_size_usd,
            chunk_index=min(len(chunk_sizes), len(chunk_sizes)),
            chunk_count=len(chunk_sizes),
            chunk_size_usd=last_fill.size_usd if last_fill and last_fill.filled else 0.0,
        )

    return last_fill


def _reentry_timeout_remaining(runtime: BotRuntime, cycle_index: int) -> int:
    return risk_helpers.reentry_timeout_remaining(runtime, cycle_index)


def _minutes_since_last_trade(runtime: BotRuntime, cycle_index: int) -> float:
    return risk_helpers.minutes_since_last_trade(runtime, cycle_index)


def _force_trade_due(runtime: BotRuntime, cycle_index: int) -> bool:
    return risk_helpers.force_trade_due(runtime, cycle_index)


def _force_trade_size_usd(runtime: BotRuntime, base_trade_size_usd: float) -> float:
    return risk_helpers.force_trade_size_usd(runtime, base_trade_size_usd)


def _buy_confirmation(prices: list[float]) -> tuple[bool, float, float, bool]:
    return strategy_helpers.buy_confirmation(prices)


def _remaining_reentry_budget(state: ReentryState) -> float:
    return risk_helpers.remaining_reentry_budget(state)


def _reentry_buy_inventory_cap(
    runtime: BotRuntime,
    inventory_usd: float,
    effective_max_inventory_usd: float,
) -> float:
    return risk_helpers.reentry_buy_inventory_cap(runtime, inventory_usd, effective_max_inventory_usd)


def _should_activate_reentry_after_sell(runtime: BotRuntime, portfolio_eth: float, mid: float) -> bool:
    return risk_helpers.should_activate_reentry_after_sell(runtime, portfolio_eth, mid)


def _available_buy_room_usd(inventory_usd: float, effective_max_inventory_usd: float) -> float:
    return risk_helpers.available_buy_room_usd(inventory_usd, effective_max_inventory_usd)


def _build_reentry_buy_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    trend_buy_allowed: bool,
    buy_confirmation: bool,
) -> tuple[str | None, float]:
    return risk_helpers.build_reentry_buy_plan(
        runtime,
        cycle_index,
        mid,
        inventory_usd,
        effective_max_inventory_usd,
        trend_buy_allowed,
        buy_confirmation,
    )


def _build_partial_reset_buy_plan(
    runtime: BotRuntime,
    equity_usd: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    base_trade_size_usd: float,
    trend_buy_allowed: bool,
    buy_confirmation: bool,
    target_inventory_pct: float,
) -> tuple[str | None, float]:
    return risk_helpers.build_partial_reset_buy_plan(
        runtime,
        equity_usd,
        inventory_usd,
        effective_max_inventory_usd,
        base_trade_size_usd,
        trend_buy_allowed,
        buy_confirmation,
        target_inventory_pct,
    )


def _build_force_trade_candidate(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    inventory_usd: float,
    effective_max_inventory_usd: float,
    buy_state_allowed: bool,
    sell_state_allowed: bool,
    state_requires_reentry_only: bool,
    in_cooldown: bool,
    base_trade_size_usd: float,
) -> DecisionOutcome | None:
    return risk_helpers.build_force_trade_candidate(
        runtime,
        cycle_index,
        mid,
        inventory_usd,
        effective_max_inventory_usd,
        buy_state_allowed,
        sell_state_allowed,
        state_requires_reentry_only,
        in_cooldown,
        base_trade_size_usd,
    )


def _build_profit_lock_sell_plan(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
) -> tuple[str | None, float]:
    return risk_helpers.build_profit_lock_sell_plan(runtime, cycle_index, mid)


def _should_delay_regular_sell(runtime: BotRuntime, mode: str) -> bool:
    return risk_helpers.should_delay_regular_sell(runtime, mode)


def _cap_inventory_preserving_sell_order(runtime: BotRuntime, sell_order, sell_reason: str) -> None:
    risk_helpers.cap_inventory_preserving_sell_order(runtime, sell_order, sell_reason)


def _log_cycle(
    runtime: BotRuntime,
    cycle_index: int,
    mode: str,
    intelligence,
    mid: float,
    source: str,
    equity_usd: float,
    pnl_usd: float,
    spread: float,
    inventory_usd: float,
) -> None:
    logging_helpers.log_cycle(
        runtime,
        cycle_index,
        mode,
        intelligence,
        mid,
        source,
        equity_usd,
        pnl_usd,
        spread,
        inventory_usd,
        time_in_state_sec=_time_in_state_seconds(runtime, cycle_index),
        last_transition=_last_transition(runtime) or "-",
    )


def _log_execution_decision(runtime: BotRuntime, cycle_index: int) -> None:
    logging_helpers.log_execution_decision(runtime, cycle_index)


def _log_trade_intent(runtime: BotRuntime, cycle_index: int) -> None:
    logging_helpers.log_trade_intent(runtime, cycle_index)


def _kill_switch_allows_continue(pnl_usd: float, log_progress: bool) -> bool:
    return logging_helpers.kill_switch_allows_continue(pnl_usd, log_progress)


def _append_equity_row(
    equity_logger,
    cycle_index: int,
    state: str,
    mode: str,
    decision_source: str,
    final_action: str,
    overridden_signals: str,
    allow_trade: bool,
    block_reason: str,
    filter_values: str,
    feed_state: str,
    regime: str,
    volatility_state: str,
    mid: float,
    source: str,
    short_ma: float,
    long_ma: float,
    volatility: float,
    spread: float,
    signal_score: float,
    feed_score: float,
    risk_score: float,
    news_score: float,
    macro_score: float,
    onchain_score: float,
    adaptive_score: float,
    confidence: float,
    buy_enabled: bool,
    sell_enabled: bool,
    max_inventory_usd: float,
    target_inventory_pct: float,
    trade_size_multiplier: float,
    spread_multiplier: float,
    trade_size_usd: float,
    inventory_usd: float,
    inventory_ratio: float,
    equity_usd: float,
    pnl_usd: float,
    trade_count: int,
    execution_price: float,
    reentry_state: str,
    state_context: str,
    time_in_state_sec: float,
    last_transition: str,
    last_sell_price: float | None,
    reentry_levels: str,
    buy_zones: str,
    executed_buy_levels: str,
    reentry_active: bool,
    reentry_timeout: int,
    cooldown_remaining: int,
    profit_lock_state: str,
    current_profit_pct: float | None,
    buy_debug_reason: str,
    sell_debug_reason: str,
    last_execution_type: str,
    execution_analytics: ExecutionAnalyticsRecord,
    last_slippage_bps: float,
    last_trade_reason: str,
) -> None:
    logging_helpers.append_equity_row(
        equity_logger,
        cycle_index,
        state,
        mode,
        decision_source,
        final_action,
        overridden_signals,
        allow_trade,
        block_reason,
        filter_values,
        feed_state,
        regime,
        volatility_state,
        mid,
        source,
        short_ma,
        long_ma,
        volatility,
        spread,
        signal_score,
        feed_score,
        risk_score,
        news_score,
        macro_score,
        onchain_score,
        adaptive_score,
        confidence,
        buy_enabled,
        sell_enabled,
        max_inventory_usd,
        target_inventory_pct,
        trade_size_multiplier,
        spread_multiplier,
        trade_size_usd,
        inventory_usd,
        inventory_ratio,
        equity_usd,
        pnl_usd,
        trade_count,
        execution_price,
        reentry_state,
        state_context,
        time_in_state_sec,
        last_transition,
        last_sell_price,
        reentry_levels,
        buy_zones,
        executed_buy_levels,
        reentry_active,
        reentry_timeout,
        cooldown_remaining,
        profit_lock_state,
        current_profit_pct,
        buy_debug_reason,
        sell_debug_reason,
        last_execution_type,
        execution_analytics,
        last_slippage_bps,
        last_trade_reason,
    )


def _append_trade_row(
    trade_logger,
    cycle_index: int,
    state: str,
    mode: str,
    decision_source: str,
    final_action: str,
    overridden_signals: str,
    allow_trade: bool,
    block_reason: str,
    filter_values: str,
    fill,
    portfolio: Portfolio,
    execution_analytics: ExecutionAnalyticsRecord,
    trade_analysis: dict[str, object] | None = None,
) -> None:
    trade_analysis = trade_analysis or {}
    logging_helpers.append_trade_row(
        trade_logger,
        cycle_index,
        state,
        mode,
        decision_source,
        final_action,
        overridden_signals,
        allow_trade,
        block_reason,
        filter_values,
        fill,
        portfolio,
        execution_analytics,
        trade_analysis.get("entry_price"),
        trade_analysis.get("exit_price"),
        trade_analysis.get("max_profit_during_trade"),
        str(trade_analysis.get("regime", "")),
        str(trade_analysis.get("active_regime", "")),
        str(trade_analysis.get("trend_direction", "")),
        str(trade_analysis.get("volatility_bucket", "")),
        str(trade_analysis.get("inventory_state", "")),
        str(trade_analysis.get("entry_reason", "")),
        str(trade_analysis.get("trigger_reason", "")),
        str(trade_analysis.get("exit_reason", "")),
        bool(trade_analysis.get("used_fallback_mode", False)),
        float(trade_analysis.get("entry_edge_bps") or 0.0),
        float(trade_analysis.get("entry_edge_usd") or 0.0),
        float(trade_analysis.get("hold_minutes") or 0.0),
        trade_reason_category=_trade_reason_category,
    )


def _blank_trade_analysis() -> dict[str, object]:
    return {
        "entry_price": None,
        "exit_price": None,
        "max_profit_during_trade": None,
        "active_regime": None,
        "regime": None,
        "trend_direction": None,
        "volatility_bucket": None,
        "inventory_state": None,
        "entry_reason": None,
        "trigger_reason": None,
        "exit_reason": None,
        "used_fallback_mode": False,
        "entry_edge_bps": None,
        "entry_edge_usd": None,
        "hold_minutes": None,
    }


def _resolve_trade_analysis(runtime: BotRuntime, cycle_index: int, fill) -> dict[str, object]:
    if not fill or not fill.filled:
        return _blank_trade_analysis()

    if fill.side == "buy":
        entry_price = runtime.portfolio.eth_cost_basis if runtime.portfolio.eth_cost_basis is not None else fill.price
        return {
            "entry_price": entry_price if entry_price and entry_price > 0 else fill.price,
            "exit_price": None,
            "max_profit_during_trade": 0.0,
            "active_regime": runtime.current_active_regime,
            "regime": runtime.current_active_regime,
            "trend_direction": runtime.current_trend_direction,
            "volatility_bucket": runtime.current_volatility_bucket,
            "inventory_state": runtime.current_inventory_state,
            "entry_reason": fill.trade_reason,
            "trigger_reason": fill.trade_reason,
            "exit_reason": "",
            "used_fallback_mode": runtime.current_inactivity_fallback_active,
            "entry_edge_bps": runtime.current_entry_edge_bps,
            "entry_edge_usd": runtime.current_entry_edge_usd,
            "hold_minutes": 0.0,
        }

    entry_price = _profit_lock_anchor_price(runtime)
    if entry_price is None or entry_price <= 0:
        entry_price = fill.price
    highest_price = runtime.profit_lock_state.highest_price
    if highest_price is None or highest_price <= 0:
        highest_price = fill.price
    max_profit_during_trade = 0.0
    if entry_price > 0 and highest_price > 0:
        max_profit_during_trade = max(((highest_price / entry_price) - 1.0) * 100.0, 0.0)
    return {
        "entry_price": entry_price,
        "exit_price": fill.price,
        "max_profit_during_trade": max_profit_during_trade,
        "active_regime": runtime.current_active_regime,
        "regime": runtime.open_position_regime or runtime.current_active_regime,
        "trend_direction": runtime.current_trend_direction,
        "volatility_bucket": runtime.current_volatility_bucket,
        "inventory_state": runtime.current_inventory_state,
        "entry_reason": runtime.open_position_reason or runtime.last_decision_reason,
        "trigger_reason": runtime.open_position_reason or runtime.last_decision_reason,
        "exit_reason": fill.trade_reason,
        "used_fallback_mode": runtime.open_position_used_fallback,
        "entry_edge_bps": runtime.open_position_entry_edge_bps,
        "entry_edge_usd": runtime.open_position_entry_edge_usd,
        "hold_minutes": _position_hold_minutes(runtime, cycle_index),
    }


def _record_fill(
    runtime: BotRuntime,
    cycle_index: int,
    mode: str,
    fill,
    realized_pnl_delta: float,
) -> dict[str, object]:
    if not fill or not fill.filled:
        return _blank_trade_analysis()

    trade_analysis = _resolve_trade_analysis(runtime, cycle_index, fill)

    runtime.last_fill_cycle = cycle_index
    runtime.last_fill_side = fill.side
    runtime.last_fill_price = fill.price
    runtime.last_trade_execution_bucket = runtime.current_execution_bucket_count
    runtime.last_trade_cycle_any = cycle_index
    runtime.last_trade_price_any = fill.price
    runtime.daily_trade_count += 1
    runtime.last_execution_price = fill.price
    runtime.last_execution_type = fill.execution_type
    runtime.last_slippage_bps = fill.slippage_bps
    runtime.last_execution_analytics.realized_slippage_bps = fill.slippage_bps
    runtime.last_execution_analytics.trade_blocked_reason = ""
    runtime.last_trade_reason = _trade_reason_category(mode, fill.trade_reason)
    runtime.last_trade_reason_detail = fill.trade_reason
    runtime.current_hold_minutes = float(trade_analysis.get("hold_minutes") or 0.0)
    log_trade_record(
        pair=runtime.last_execution_pair or "WETH/USDC",
        side=fill.side,
        size_usd=fill.size_usd,
        price=fill.price,
        pnl_usd=realized_pnl_delta,
        gas_gwei=runtime.last_execution_gas_gwei,
        tx_hash=runtime.last_execution_tx_hash,
        execution_mode=runtime.last_execution_analytics.execution_mode,
        execution_type=fill.execution_type,
        trade_reason=fill.trade_reason,
        state=runtime.state_context.current_state.value,
        mode=mode,
        fee_usd=fill.fee_usd,
        slippage_bps=fill.slippage_bps,
        mev_risk_score=runtime.last_execution_analytics.mev_risk_score,
        entry_price=trade_analysis["entry_price"] or 0.0,
        exit_price=trade_analysis["exit_price"] or 0.0,
        max_profit_during_trade=trade_analysis["max_profit_during_trade"] or 0.0,
        metadata={
            "cycle_index": cycle_index,
            "decision_source": runtime.last_decision_source,
            "detected_regime": runtime.current_detected_regime,
            "zone": runtime.current_zone,
            "trade_reason_category": runtime.last_trade_reason,
            "regime": trade_analysis.get("regime") or runtime.current_active_regime,
            "active_regime": trade_analysis.get("active_regime") or runtime.current_active_regime,
            "trend_direction": trade_analysis.get("trend_direction") or runtime.current_trend_direction,
            "volatility_bucket": trade_analysis.get("volatility_bucket") or runtime.current_volatility_bucket,
            "inventory_state": trade_analysis.get("inventory_state") or runtime.current_inventory_state,
            "entry_threshold_bps": runtime.current_entry_threshold_bps,
            "min_edge_bps": runtime.current_min_edge_bps,
            "inventory_drift_pct": runtime.current_inventory_drift_pct,
            "inactivity_fallback_active": runtime.current_inactivity_fallback_active,
            "used_fallback_mode": bool(trade_analysis.get("used_fallback_mode", False)),
            "entry_reason": trade_analysis.get("entry_reason") or fill.trade_reason,
            "trade_blocked_reason": runtime.last_decision_block_reason or runtime.last_execution_analytics.trade_blocked_reason,
            "trigger_reason": trade_analysis.get("trigger_reason") or fill.trade_reason,
            "exit_reason": trade_analysis.get("exit_reason") or "",
            "entry_edge_bps": float(trade_analysis.get("entry_edge_bps") or 0.0),
            "entry_edge_usd": float(trade_analysis.get("entry_edge_usd") or 0.0),
            "hold_minutes": float(trade_analysis.get("hold_minutes") or 0.0),
            "execution_metadata": runtime.last_execution_metadata,
        },
    )
    runtime.total_slippage_bps += fill.slippage_bps
    if fill.execution_type == "taker":
        runtime.taker_count += 1
    else:
        runtime.maker_count += 1
    runtime.mode_trade_counts[mode] = runtime.mode_trade_counts.get(mode, 0) + 1
    runtime.mode_trade_notional_usd[mode] = runtime.mode_trade_notional_usd.get(mode, 0.0) + fill.size_usd
    runtime.mode_realized_pnl_usd[mode] = runtime.mode_realized_pnl_usd.get(mode, 0.0) + realized_pnl_delta
    active_regime = runtime.current_active_regime or "RANGE"
    runtime.regime_trade_counts[active_regime] = runtime.regime_trade_counts.get(active_regime, 0) + 1
    runtime.regime_realized_pnl_usd[active_regime] = runtime.regime_realized_pnl_usd.get(active_regime, 0.0) + realized_pnl_delta
    if realized_pnl_delta > 0:
        runtime.gross_profit_usd += realized_pnl_delta
    elif realized_pnl_delta < 0:
        runtime.gross_loss_usd += abs(realized_pnl_delta)
    if abs(realized_pnl_delta) > 1e-9:
        runtime.realized_trade_pnls.append(realized_pnl_delta)
    runtime.performance.record_trade(
        cycle_index=cycle_index,
        side=fill.side,
        price=fill.price,
        size_usd=fill.size_usd,
        fee_usd=fill.fee_usd,
        realized_pnl=realized_pnl_delta,
        usdc_after=runtime.portfolio.usdc,
        eth_after=runtime.portfolio.eth,
        execution_type=fill.execution_type,
        slippage_bps=fill.slippage_bps,
        trade_reason=fill.trade_reason,
        entry_price=trade_analysis["entry_price"] or 0.0,
        exit_price=trade_analysis["exit_price"] or 0.0,
        max_profit_during_trade=trade_analysis["max_profit_during_trade"] or 0.0,
        active_regime=str(trade_analysis.get("active_regime") or runtime.current_active_regime),
        trend_direction=str(trade_analysis.get("trend_direction") or runtime.current_trend_direction),
        volatility_bucket=str(trade_analysis.get("volatility_bucket") or runtime.current_volatility_bucket),
        inventory_state=str(trade_analysis.get("inventory_state") or runtime.current_inventory_state),
        trigger_reason=str(trade_analysis.get("entry_reason") or trade_analysis.get("trigger_reason") or fill.trade_reason),
        exit_reason=str(trade_analysis.get("exit_reason") or ""),
        entry_edge_bps=float(trade_analysis.get("entry_edge_bps") or 0.0),
        entry_edge_usd=float(trade_analysis.get("entry_edge_usd") or 0.0),
        hold_minutes=float(trade_analysis.get("hold_minutes") or 0.0),
    )

    if fill.side == "buy":
        runtime.mode_buy_counts[mode] = runtime.mode_buy_counts.get(mode, 0) + 1
        runtime.open_position_cycle = cycle_index
        runtime.open_position_reason = fill.trade_reason
        runtime.open_position_regime = runtime.current_active_regime
        runtime.open_position_price = fill.price
        runtime.open_position_entry_edge_bps = runtime.current_entry_edge_bps
        runtime.open_position_entry_edge_usd = runtime.current_entry_edge_usd
        runtime.open_position_used_fallback = runtime.current_inactivity_fallback_active
        runtime.current_hold_minutes = 0.0
        sizing = _current_sizing(runtime)
        min_notional_usd = sizing.min_notional_usd if sizing is not None else MIN_ORDER_SIZE_USD
        if fill.trade_reason.startswith("reentry_"):
            runtime.reentry_state.spent_usd += fill.size_usd
            level_key = fill.trade_reason.removeprefix("reentry_")
            if level_key not in runtime.reentry_state.executed_buy_levels:
                runtime.reentry_state.executed_buy_levels.append(level_key)
            if runtime.reentry_state.spent_usd >= max(runtime.reentry_state.budget_usd * 0.98, min_notional_usd):
                runtime.reentry_state.active = False
        elif fill.trade_reason == "partial_reset":
            runtime.reentry_state.spent_usd += fill.size_usd
        anchor_price = runtime.portfolio.eth_cost_basis if runtime.portfolio.eth_cost_basis is not None else fill.price
        _reset_profit_lock_state(runtime, anchor_price)
    elif fill.side == "sell":
        runtime.mode_sell_counts[mode] = runtime.mode_sell_counts.get(mode, 0) + 1
        hold_minutes = float(trade_analysis.get("hold_minutes") or 0.0)
        if hold_minutes > 0:
            runtime.cumulative_hold_minutes += hold_minutes
            runtime.closed_hold_count += 1
        if fill.trade_reason == "profit_lock_level_1":
            runtime.profit_lock_state.level_one_executed = True
            runtime.profit_lock_state.level_one_armed = False
        elif fill.trade_reason == "profit_lock_level_2":
            runtime.profit_lock_state.level_two_executed = True
            runtime.profit_lock_state.level_two_armed = False
            _clear_profit_lock_state(runtime)
        else:
            _clear_profit_lock_state(runtime)
        if not fill.trade_reason.startswith("force_trade_"):
            if realized_pnl_delta < 0:
                runtime.loss_streak += 1
                runtime.max_loss_streak = max(runtime.max_loss_streak, runtime.loss_streak)
                runtime.last_loss_cycle = cycle_index
                runtime.last_loss_trade_reason = fill.trade_reason
                if runtime.loss_streak >= max(MAX_CONSECUTIVE_LOSSES_BEFORE_PAUSE, 1):
                    pause_cycles = loss_pause_cycles(capped_loss_pause_minutes(), runtime.cycle_seconds)
                    runtime.loss_pause_until_cycle = cycle_index + pause_cycles if pause_cycles > 0 else cycle_index
            else:
                runtime.loss_streak = 0
                runtime.loss_pause_until_cycle = None
                runtime.loss_pause_remaining_minutes = 0.0
        remaining_eth = max(runtime.portfolio.eth - runtime.engine.min_eth_reserve, 0.0)
        if remaining_eth * max(fill.price, 0.0) < MIN_ORDER_SIZE_USD:
            runtime.open_position_cycle = None
            runtime.open_position_reason = ""
            runtime.open_position_regime = ""
            runtime.open_position_price = 0.0
            runtime.open_position_entry_edge_bps = 0.0
            runtime.open_position_entry_edge_usd = 0.0
            runtime.open_position_used_fallback = False
            runtime.current_hold_minutes = 0.0

    notifier = runtime.telegram_notifier
    if notifier is not None:
        try:
            notifier.notify_trade(
                cycle_index=cycle_index,
                fill=fill,
                runtime=runtime,
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 - notifications must never break trading
            log(f"Telegram trade notify failed | cycle {cycle_index} | error {exc}")

    return trade_analysis


def _allows_opposite_side_trade(
    runtime: BotRuntime,
    cycle_index: int,
    side: str,
    order_price: float,
) -> bool:
    return risk_helpers.allows_opposite_side_trade(runtime, cycle_index, side, order_price)


def _cap_trend_sell_order(
    mode: str,
    sell_order,
    inventory_usd: float,
    equity_usd: float,
    effective_max_inventory_usd: float,
    target_inventory_pct: float,
    mid: float,
) -> None:
    strategy_helpers.cap_trend_sell_order(
        mode=mode,
        sell_order=sell_order,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
        effective_max_inventory_usd=effective_max_inventory_usd,
        target_inventory_pct=target_inventory_pct,
        mid=mid,
    )


def _process_price_tick_with_decision_engine(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    source: str,
    trade_logger=None,
    equity_logger=None,
    log_progress: bool = True,
) -> bool:
    if mid <= 0:
        if log_progress:
            log(f"{cycle_index} | invalid price from {source}")
        return True

    portfolio = runtime.portfolio
    engine = runtime.engine

    portfolio.ensure_cost_basis(mid)
    _refresh_timeframe_prices(runtime, mid)
    strategy_mid = _strategy_mid(runtime, mid)
    runtime.current_regime_assessment = runtime.regime_detector.assess(_regime_prices(runtime))

    inventory_usd, equity_usd, pnl_usd = _account_state(runtime, mid)
    _track_runtime_state(runtime, cycle_index, mid, inventory_usd, equity_usd, pnl_usd, record_equity=True)
    _sync_daily_risk_state(runtime, equity_usd)

    sizing = _update_runtime_sizing(runtime, equity_usd=equity_usd, mid=mid)

    intelligence = runtime.intelligence.build_snapshot(
        prices=runtime.prices,
        current_equity=equity_usd,
        equity_peak=runtime.equity_peak if runtime.equity_peak is not None else equity_usd,
        recent_equities=list(runtime.recent_equities),
        inventory_usd=inventory_usd,
        regime_assessment=runtime.current_regime_assessment,
        cycle_index=cycle_index,
        cycle_seconds=runtime.cycle_seconds,
        recent_trade_cycles=_recent_trade_cycles(runtime),
        paper_mode=_paper_mode_enabled(),
    )
    runtime.current_market_mode = _intelligence_active_regime(intelligence)
    _refresh_timeframe_signals(runtime)
    _update_drawdown_guard(runtime, cycle_index=cycle_index, equity_usd=equity_usd)
    runtime.reentry_engine.update_state(runtime.reentry_state, strategy_mid)
    _update_profit_lock_state(runtime, strategy_mid)
    inventory_profile = runtime.inventory_manager.build_profile(
        regime=intelligence.regime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )
    _track_inventory_ratio(runtime, inventory_profile.inventory_ratio)
    _apply_runtime_regime_context(
        runtime,
        intelligence=intelligence,
        regime_assessment=runtime.current_regime_assessment,
        inventory_profile=inventory_profile,
        cycle_index=cycle_index,
    )
    if runtime.enable_state_machine:
        runtime.state_machine.sync_cycle(
            context=runtime.state_context,
            cycle_index=cycle_index,
            reentry_state=runtime.reentry_state,
            portfolio_eth=portfolio.eth,
            min_eth_reserve=engine.min_eth_reserve,
        )
    current_state = runtime.state_context.current_state.value
    runtime.state_counts[current_state] = runtime.state_counts.get(current_state, 0) + 1
    runtime.last_execution_analytics = blank_execution_analytics()
    execution_helpers.reset_execution_observation(runtime)
    runtime.current_edge_assessment = None
    runtime.current_signal_gate_decision = None
    runtime.current_entry_edge_bps = 0.0
    runtime.current_entry_edge_usd = 0.0
    runtime.current_signal_block_reason = ""

    min_notional_usd = sizing.min_notional_usd
    available_quote_usd = sizing.available_quote_to_trade_usd
    base_trade_size_usd = sizing.trade_size_usd
    effective_max_inventory_usd = max(sizing.max_position_usd * intelligence.max_inventory_multiplier, 0.0)
    spread = calculate_spread(intelligence.volatility, intelligence.spread_multiplier)
    min_sell_price = portfolio.min_profitable_sell_price(MAKER_FEE_BPS, MIN_SELL_PROFIT_BPS)
    mode = intelligence.mode
    runtime.mode_counts[mode] = runtime.mode_counts.get(mode, 0) + 1
    runtime.feed_state_counts[intelligence.feed_state] = runtime.feed_state_counts.get(intelligence.feed_state, 0) + 1

    trade_size_usd = choose_trade_size_usd(
        mode=mode,
        base_size=base_trade_size_usd,
        inventory_usd=inventory_usd,
        max_inventory_usd=effective_max_inventory_usd,
    ) * intelligence.trade_size_multiplier
    trade_size_usd = min(max(trade_size_usd, 0.0), base_trade_size_usd)
    buy_confirmation, current_rsi, _, _ = _buy_confirmation(runtime.prices)
    momentum_bps = calculate_recent_momentum_bps(runtime.prices)
    cooldown_remaining = (
        runtime.state_machine.cooldown_remaining_cycles(runtime.state_context, cycle_index)
        if runtime.enable_state_machine
        else 0
    )
    state_requires_reentry_only = runtime.enable_state_machine and runtime.state_machine.requires_reentry_only(
        runtime.state_context
    )
    buy_state_allowed = not runtime.enable_state_machine or runtime.state_machine.allow_buy(runtime.state_context)
    sell_state_allowed = not runtime.enable_state_machine or runtime.state_machine.allow_sell(runtime.state_context)
    in_cooldown = runtime.enable_state_machine and runtime.state_machine.in_cooldown(runtime.state_context)
    trend_filter_buy_allowed = _trend_filter_allows_buy(runtime)
    trend_filter_sell_allowed = _trend_filter_allows_sell(runtime)
    adjusted_buy_enabled = intelligence.buy_enabled and trend_filter_buy_allowed
    adjusted_sell_enabled = intelligence.sell_enabled and trend_filter_sell_allowed
    runtime.last_profit_pct = _current_profit_pct(runtime, strategy_mid)
    base_sell_debug_reason = _base_sell_debug_reason(
        runtime=runtime,
        cycle_index=cycle_index,
        mid=strategy_mid,
        sell_enabled=adjusted_sell_enabled,
        sell_state_allowed=sell_state_allowed,
        in_cooldown=in_cooldown,
        state_requires_reentry_only=state_requires_reentry_only,
    )
    runtime.last_sell_debug_reason = base_sell_debug_reason

    quote = build_quotes(
        mid=strategy_mid,
        spread_bps=spread,
        inventory_usd=inventory_usd,
        max_inventory_usd=effective_max_inventory_usd,
        inventory_skew_strength=_inventory_skew_strength(intelligence, inventory_profile),
        directional_bias=intelligence.directional_bias,
    )

    trend_buy_target_pct = min(TREND_BUY_TARGET_PCT, intelligence.target_inventory_pct)
    trend_trigger_multiplier = max(getattr(intelligence, "entry_trigger_multiplier", 1.0), 0.50)
    trend_signal_allows_buy = (
        not in_cooldown
        and adjusted_buy_enabled
        and _intelligence_active_regime(intelligence) == "TREND"
        and _intelligence_trend_direction(intelligence, runtime.current_regime_assessment) == "up"
        and should_place_trend_buy(
            mid=strategy_mid,
            short_ma=intelligence.short_ma,
            long_ma=intelligence.long_ma,
            trend_strength=intelligence.trend_strength,
            market_score=intelligence.market_score,
            signal_score=intelligence.signal_score,
            confidence=intelligence.confidence,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            max_inventory_usd=effective_max_inventory_usd,
            trend_buy_target_pct=trend_buy_target_pct,
            max_trend_chase_bps=(MAX_TREND_CHASE_BPS * intelligence.max_chase_bps_multiplier) / trend_trigger_multiplier,
            max_trend_pullback_bps=MAX_TREND_PULLBACK_BPS / trend_trigger_multiplier,
            trend_buy_min_market_score=TREND_BUY_MIN_MARKET_SCORE * trend_trigger_multiplier,
            trend_buy_min_signal_score=TREND_BUY_MIN_SIGNAL_SCORE * trend_trigger_multiplier,
            trend_buy_min_confidence=TREND_BUY_MIN_CONFIDENCE * trend_trigger_multiplier,
            trend_buy_min_long_buffer_bps=TREND_BUY_MIN_LONG_BUFFER_BPS * trend_trigger_multiplier,
            trend_buy_min_strength_multiplier=TREND_BUY_MIN_STRENGTH_MULTIPLIER * trend_trigger_multiplier,
        )
    )

    strategy_buy_price = quote.bid
    if trend_signal_allows_buy and mode == "TREND_UP":
        strategy_buy_price = requote_trend_buy_price(
            current_bid=strategy_buy_price,
            mid=strategy_mid,
            trend_buy_requote_bps=TREND_BUY_REQUOTE_BPS,
        )

    strategy_sell_price = quote.ask
    if mode == "TREND_UP":
        strategy_sell_price = requote_trend_sell_price(
            current_ask=strategy_sell_price,
            mid=strategy_mid,
            trend_sell_spread_factor=TREND_SELL_SPREAD_FACTOR,
        )

    max_workable_sell_price = strategy_mid * (1.0 + (MAX_EXIT_PREMIUM_BPS / 10000.0))
    if mode == "OVERWEIGHT_EXIT":
        if min_sell_price is None:
            strategy_sell_price = min(strategy_sell_price, max_workable_sell_price)
        elif min_sell_price <= max_workable_sell_price:
            strategy_sell_price = max(strategy_sell_price, min_sell_price)
        else:
            strategy_sell_price = max_workable_sell_price
    elif min_sell_price is not None and strategy_sell_price < min_sell_price:
        strategy_sell_price = min_sell_price

    default_buy_inventory_cap = effective_max_inventory_usd
    reentry_buy_inventory_cap = _reentry_buy_inventory_cap(runtime, inventory_usd, effective_max_inventory_usd)
    reentry_plan = None
    partial_reset_reason = None
    partial_reset_size_usd = 0.0
    buy_drift_block_reason = ""
    sell_drift_block_reason = ""
    inventory_drift_guard_reasons: list[str] = []

    strategy_buy_candidate = None
    range_entry_allowed = _range_entry_signal_allowed(
        runtime,
        intelligence=intelligence,
        regime_assessment=runtime.current_regime_assessment,
        buy_state_allowed=buy_state_allowed,
        state_requires_reentry_only=state_requires_reentry_only,
        in_cooldown=in_cooldown,
    )
    if (
        range_entry_allowed
        and not getattr(inventory_profile, "reduction_only", False)
        and runtime.current_regime_assessment is not None
    ):
        range_buy_size_usd = trade_size_usd
        range_buy_reason = "range_buy"
        if runtime.current_inactivity_fallback_active:
            range_buy_size_usd *= max(min(INACTIVITY_FORCE_SIZE_MULTIPLIER, 1.0), 0.0)
            range_buy_reason = "inactivity_range_buy"
        if range_buy_size_usd < min_notional_usd:
            range_buy_size_usd = 0.0
        range_buy_price = min(
            strategy_mid,
            max(strategy_buy_price, runtime.current_regime_assessment.window_low * 1.0008),
        )
        if range_buy_size_usd >= min_notional_usd:
            strategy_buy_candidate = DecisionOutcome(
                action="BUY",
                size_usd=range_buy_size_usd,
                reason=range_buy_reason,
                source="strategy",
                order_price=range_buy_price,
                inventory_cap_usd=default_buy_inventory_cap,
            )
    elif buy_state_allowed and not state_requires_reentry_only and trend_signal_allows_buy and not getattr(inventory_profile, "reduction_only", False):
        strategy_buy_candidate = DecisionOutcome(
            action="BUY",
            size_usd=trade_size_usd,
            reason="trend_buy",
            source="strategy",
            order_price=strategy_buy_price,
            inventory_cap_usd=default_buy_inventory_cap,
        )
    strategy_buy_candidate, blocked_reason = _apply_inventory_drift_guard(runtime, strategy_buy_candidate)
    if blocked_reason:
        buy_drift_block_reason = blocked_reason
        inventory_drift_guard_reasons.append(blocked_reason)

    strategy_sell_candidate = None
    if not in_cooldown and sell_state_allowed and not state_requires_reentry_only:
        profit_lock_allowed = not runtime.enable_state_machine or runtime.state_context.current_state in {
            StrategyState.ACCUMULATING,
            StrategyState.DISTRIBUTING,
        }
        profit_lock_reason = None
        profit_lock_size_usd = 0.0
        if profit_lock_allowed:
            profit_lock_reason, profit_lock_size_usd = _build_profit_lock_sell_plan(
                runtime=runtime,
                cycle_index=cycle_index,
                mid=strategy_mid,
            )
        if profit_lock_reason and profit_lock_size_usd >= min_notional_usd:
            strategy_sell_candidate = DecisionOutcome(
                action="SELL",
                size_usd=profit_lock_size_usd,
                reason=profit_lock_reason,
                source="strategy",
                order_price=strategy_mid,
                inventory_cap_usd=default_buy_inventory_cap,
            )
        elif _trend_rally_sell_signal_allowed(intelligence, runtime.current_regime_assessment):
            strategy_sell_size_usd = min(trade_size_usd, max(inventory_profile.max_sell_usd, 0.0))
            if strategy_sell_size_usd >= min_notional_usd:
                strategy_sell_candidate = DecisionOutcome(
                    action="SELL",
                    size_usd=strategy_sell_size_usd,
                    reason="trend_rally_sell",
                    source="strategy",
                    order_price=strategy_sell_price,
                    inventory_cap_usd=default_buy_inventory_cap,
                )
        elif (
            _intelligence_active_regime(intelligence) != "RANGE"
            and not _should_delay_regular_sell(runtime, mode)
            and adjusted_sell_enabled
            and mode in {"TREND_UP", "RANGE_MAKER"}
        ):
            strategy_sell_size_usd = trade_size_usd
            if mode == "TREND_UP":
                target_inventory_usd = equity_usd * min(max(trend_buy_target_pct, 0.0), 1.0)
                strategy_sell_size_usd = min(strategy_sell_size_usd, max(inventory_usd - target_inventory_usd, 0.0))
            if strategy_sell_size_usd >= min_notional_usd:
                strategy_sell_candidate = DecisionOutcome(
                    action="SELL",
                    size_usd=strategy_sell_size_usd,
                    reason="quoted_sell",
                    source="strategy",
                    order_price=strategy_sell_price,
                    inventory_cap_usd=default_buy_inventory_cap,
                )
        if getattr(inventory_profile, "force_limit_hit", False):
            forced_reduce_size_usd = min(max(trade_size_usd, min_notional_usd), inventory_profile.max_sell_usd)
            if forced_reduce_size_usd >= min_notional_usd:
                strategy_sell_candidate = DecisionOutcome(
                    action="SELL",
                    size_usd=forced_reduce_size_usd,
                    reason="inventory_force_reduce",
                    source="inventory",
                    order_price=_forced_inventory_reduce_sell_price(
                        strategy_mid=strategy_mid,
                        strategy_sell_price=strategy_sell_price,
                        min_sell_price=min_sell_price,
                    ),
                    inventory_cap_usd=default_buy_inventory_cap,
                )
    strategy_sell_candidate, blocked_reason = _apply_inventory_drift_guard(runtime, strategy_sell_candidate)
    if blocked_reason:
        sell_drift_block_reason = blocked_reason
        inventory_drift_guard_reasons.append(blocked_reason)

    reentry_candidate = None
    if not in_cooldown and runtime.enable_reentry_engine and runtime.reentry_state.active:
        reentry_plan = runtime.reentry_engine.build_scale_in_plan(
            state=runtime.reentry_state,
            cycle_index=cycle_index,
            mid=strategy_mid,
            room_usd=available_quote_usd,
            trend_buy_allowed=trend_signal_allows_buy,
            buy_confirmation=buy_confirmation,
        )
        if reentry_plan.allow_trade and reentry_plan.size_usd >= min_notional_usd:
            reentry_candidate = DecisionOutcome(
                action="BUY",
                size_usd=min(reentry_plan.size_usd, available_quote_usd),
                reason=reentry_plan.trade_reason,
                source="reentry",
                order_price=quote.bid if quote.bid > 0 else strategy_mid,
                inventory_cap_usd=reentry_buy_inventory_cap,
            )
    reentry_candidate, blocked_reason = _apply_inventory_drift_guard(runtime, reentry_candidate)
    if blocked_reason:
        buy_drift_block_reason = buy_drift_block_reason or blocked_reason
        inventory_drift_guard_reasons.append(blocked_reason)

    inventory_candidate = None
    if not in_cooldown:
        if sell_state_allowed and inventory_profile.inventory_ratio > inventory_profile.upper_bound:
            inventory_sell_size = min(max(trade_size_usd, min_notional_usd), inventory_profile.max_sell_usd)
            if inventory_sell_size >= min_notional_usd:
                inventory_candidate = DecisionOutcome(
                    action="SELL",
                    size_usd=inventory_sell_size,
                    reason="inventory_correction",
                    source="inventory",
                    order_price=strategy_sell_price,
                    inventory_cap_usd=default_buy_inventory_cap,
                )
        elif (
            buy_state_allowed
            and not runtime.reentry_state.active
            and inventory_profile.inventory_ratio < inventory_profile.lower_bound
            and not getattr(inventory_profile, "reduction_only", False)
        ):
            partial_reset_reason, partial_reset_size_usd = _build_partial_reset_buy_plan(
                runtime=runtime,
                equity_usd=equity_usd,
                inventory_usd=inventory_usd,
                effective_max_inventory_usd=effective_max_inventory_usd,
                base_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
                trend_buy_allowed=trend_signal_allows_buy,
                buy_confirmation=buy_confirmation,
                target_inventory_pct=intelligence.target_inventory_pct,
            )
            if partial_reset_reason and partial_reset_size_usd >= min_notional_usd:
                inventory_candidate = DecisionOutcome(
                    action="BUY",
                    size_usd=min(partial_reset_size_usd, available_quote_usd),
                    reason=partial_reset_reason,
                    source="inventory",
                    order_price=strategy_mid,
                    inventory_cap_usd=reentry_buy_inventory_cap,
                )
            elif buy_confirmation or trend_signal_allows_buy:
                inventory_buy_size = min(
                    max(trade_size_usd * PARTIAL_RESET_BUY_FRACTION, min_notional_usd),
                    inventory_profile.max_buy_usd,
                    available_quote_usd,
                )
                if inventory_buy_size >= min_notional_usd:
                    inventory_candidate = DecisionOutcome(
                        action="BUY",
                        size_usd=inventory_buy_size,
                        reason="inventory_rebalance",
                        source="inventory",
                        order_price=strategy_mid,
                        inventory_cap_usd=reentry_buy_inventory_cap,
                    )

    if mode == "OVERWEIGHT_EXIT" and sell_state_allowed and inventory_candidate is None:
        overweight_sell_size = min(max(trade_size_usd, min_notional_usd), inventory_profile.max_sell_usd)
        if overweight_sell_size >= min_notional_usd:
            inventory_candidate = DecisionOutcome(
                action="SELL",
                size_usd=overweight_sell_size,
                reason="inventory_correction",
                source="inventory",
                order_price=strategy_sell_price,
                inventory_cap_usd=default_buy_inventory_cap,
            )
    inventory_candidate, blocked_reason = _apply_inventory_drift_guard(runtime, inventory_candidate)
    if blocked_reason:
        if "buy" in blocked_reason:
            buy_drift_block_reason = buy_drift_block_reason or blocked_reason
        else:
            sell_drift_block_reason = sell_drift_block_reason or blocked_reason
        inventory_drift_guard_reasons.append(blocked_reason)

    force_trade_candidate = None
    if (
        _intelligence_active_regime(intelligence) != "NO_TRADE"
        and
        strategy_buy_candidate is None
        and strategy_sell_candidate is None
        and reentry_candidate is None
        and inventory_candidate is None
        and not getattr(inventory_profile, "reduction_only", False)
    ):
        force_trade_candidate = _build_force_trade_candidate(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=strategy_mid,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=effective_max_inventory_usd,
            buy_state_allowed=buy_state_allowed,
            sell_state_allowed=sell_state_allowed,
            state_requires_reentry_only=state_requires_reentry_only,
            in_cooldown=in_cooldown,
            base_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
        )
        if force_trade_candidate is not None:
            force_trade_candidate, blocked_reason = _apply_inventory_drift_guard(runtime, force_trade_candidate)
            if blocked_reason:
                if "buy" in blocked_reason:
                    buy_drift_block_reason = buy_drift_block_reason or blocked_reason
                else:
                    sell_drift_block_reason = sell_drift_block_reason or blocked_reason
                inventory_drift_guard_reasons.append(blocked_reason)
            elif force_trade_candidate.action == "BUY":
                strategy_buy_candidate = force_trade_candidate
            elif force_trade_candidate.action == "SELL":
                strategy_sell_candidate = force_trade_candidate

    runtime.last_sell_debug_reason = sell_drift_block_reason or base_sell_debug_reason

    runtime.last_buy_debug_reason = _base_buy_debug_reason(
        runtime=runtime,
        cycle_index=cycle_index,
        mid=strategy_mid,
        buy_enabled=adjusted_buy_enabled,
        buy_state_allowed=buy_state_allowed,
        in_cooldown=in_cooldown,
        reentry_plan=reentry_plan,
        partial_reset_reason=partial_reset_reason,
        force_trade_candidate=force_trade_candidate,
        trend_signal_allows_buy=trend_signal_allows_buy,
    )
    if buy_drift_block_reason:
        runtime.last_buy_debug_reason = buy_drift_block_reason

    if (
        strategy_buy_candidate is None
        and strategy_sell_candidate is None
        and reentry_candidate is None
        and inventory_candidate is None
        and inventory_drift_guard_reasons
    ):
        blocked_reason = inventory_drift_guard_reasons[0]
        decision = DecisionOutcome(
            action="NONE",
            reason=blocked_reason,
            source="inventory_guard",
            block_reason=blocked_reason,
            filter_values={"inventory_drift_guard_active": True},
        )
    else:
        decision = runtime.decision_engine.decide(
            cycle_index=cycle_index,
            reentry_active=runtime.reentry_state.active,
            reentry_candidate=reentry_candidate,
            inventory_candidate=inventory_candidate,
            strategy_buy_candidate=strategy_buy_candidate,
            strategy_sell_candidate=strategy_sell_candidate,
            inventory_profile=inventory_profile,
            available_usdc=available_quote_usd,
            inventory_manager_enabled=runtime.enable_inventory_manager,
            trade_filter_enabled=runtime.enable_trade_filter,
            last_trade_cycle=runtime.last_trade_cycle_any,
            last_trade_price=runtime.last_trade_price_any,
            loss_streak=runtime.loss_streak,
            rsi_value=current_rsi,
            momentum_bps=momentum_bps,
            regime=intelligence.regime,
            market_score=intelligence.market_score,
            volatility_state=intelligence.volatility_state,
            trade_count=engine.trade_count,
            daily_trade_count=runtime.daily_trade_count,
        )
    decision = _apply_signal_pipeline(
        runtime,
        cycle_index=cycle_index,
        decision=decision,
        strategy_mode=mode,
        intelligence=intelligence,
        quote=quote,
        mid=mid,
        spread_bps=spread,
        source=source,
        effective_max_inventory_usd=effective_max_inventory_usd,
        inventory_profile=inventory_profile,
        inventory_usd=inventory_usd,
    )
    _record_decision(runtime, decision)
    _record_trade_gate(
        runtime,
        allow_trade=decision.allow_trade and decision.action in {"BUY", "SELL"},
        block_reason=decision.block_reason,
        filter_values=decision.filter_values,
    )

    if (
        decision.action == "NONE"
        and runtime.enable_state_machine
        and runtime.state_context.current_state == StrategyState.DISTRIBUTING
        and runtime.reentry_state.active
    ):
        runtime.state_machine.transition(
            runtime.state_context,
            StrategyState.WAIT_REENTRY,
            cycle_index,
            "distribution_complete",
        )

    equity_row_kwargs = {
        "equity_logger": equity_logger,
        "cycle_index": cycle_index,
        "state": runtime.state_context.current_state.value,
        "mode": mode,
        "decision_source": runtime.last_decision_source,
        "final_action": runtime.last_final_action,
        "overridden_signals": runtime.last_overridden_signals,
        "allow_trade": runtime.last_allow_trade,
        "block_reason": runtime.last_decision_block_reason,
        "filter_values": runtime.last_filter_values,
        "feed_state": intelligence.feed_state,
        "regime": intelligence.regime,
        "volatility_state": intelligence.volatility_state,
        "mid": mid,
        "source": source,
        "short_ma": intelligence.short_ma,
        "long_ma": intelligence.long_ma,
        "volatility": intelligence.volatility,
        "spread": spread,
        "signal_score": intelligence.signal_score,
        "feed_score": intelligence.feed_score,
        "risk_score": intelligence.risk_score,
        "news_score": intelligence.news_score,
        "macro_score": intelligence.macro_score,
        "onchain_score": intelligence.onchain_score,
        "adaptive_score": intelligence.adaptive_score,
        "confidence": intelligence.confidence,
        "buy_enabled": adjusted_buy_enabled,
        "sell_enabled": adjusted_sell_enabled,
        "max_inventory_usd": effective_max_inventory_usd,
        "target_inventory_pct": intelligence.target_inventory_pct,
        "trade_size_multiplier": intelligence.trade_size_multiplier,
        "spread_multiplier": intelligence.spread_multiplier,
        "trade_size_usd": trade_size_usd,
        "inventory_usd": inventory_usd,
        "inventory_ratio": inventory_profile.inventory_ratio,
        "equity_usd": equity_usd,
        "pnl_usd": pnl_usd,
        "trade_count": engine.trade_count,
        "execution_price": runtime.last_execution_price,
        "reentry_state": runtime.reentry_engine.serialize_state(runtime.reentry_state),
        "state_context": runtime.state_machine.serialize(runtime.state_context),
        "time_in_state_sec": _time_in_state_seconds(runtime, cycle_index),
        "last_transition": _last_transition(runtime),
        "last_sell_price": runtime.reentry_state.last_sell_price,
        "reentry_levels": _serialize_buy_zones(runtime.reentry_state),
        "buy_zones": _serialize_buy_zones(runtime.reentry_state),
        "executed_buy_levels": json.dumps(runtime.reentry_state.executed_buy_levels, separators=(",", ":")),
        "reentry_active": runtime.reentry_state.active,
        "reentry_timeout": runtime.reentry_engine.timeout_remaining_cycles(runtime.reentry_state, cycle_index),
        "cooldown_remaining": cooldown_remaining,
        "profit_lock_state": _serialize_profit_lock_state(runtime.profit_lock_state),
        "current_profit_pct": runtime.last_profit_pct,
        "buy_debug_reason": runtime.last_buy_debug_reason,
        "sell_debug_reason": runtime.last_sell_debug_reason,
        "last_execution_type": runtime.last_execution_type,
        "execution_analytics": runtime.last_execution_analytics,
        "last_slippage_bps": runtime.last_slippage_bps,
        "last_trade_reason": runtime.last_trade_reason,
    }

    current_limit_decision = _evaluate_runtime_limits(runtime, inventory_usd, equity_usd)
    if current_limit_decision.stop_trading:
        return _stop_for_risk_limit(
            runtime,
            cycle_index=cycle_index,
            reason=current_limit_decision.reason,
            details=current_limit_decision.details,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            mode=mode,
            intelligence=intelligence,
            mid=mid,
            source=source,
            spread=spread,
            pnl_usd=pnl_usd,
            equity_row_kwargs=equity_row_kwargs,
            log_progress=log_progress,
        )

    if mode == "NO_TRADE":
        blockers = getattr(intelligence, "blockers", [])
        no_trade_reason = "drawdown_pause" if "drawdown_pause" in blockers else "no_trade"
        _record_decision(runtime, DecisionOutcome(action="NONE", reason=no_trade_reason, source="strategy"))
        _record_trade_gate(runtime, allow_trade=False, block_reason=no_trade_reason, filter_values={"mode": mode})
        runtime.last_buy_debug_reason = no_trade_reason
        runtime.last_sell_debug_reason = no_trade_reason
        equity_row_kwargs.update(
            {
                "decision_source": runtime.last_decision_source,
                "final_action": runtime.last_final_action,
                "overridden_signals": runtime.last_overridden_signals,
                "allow_trade": runtime.last_allow_trade,
                "block_reason": runtime.last_decision_block_reason,
                "filter_values": runtime.last_filter_values,
                "buy_debug_reason": runtime.last_buy_debug_reason,
                "sell_debug_reason": runtime.last_sell_debug_reason,
            }
        )
        if log_progress:
            _log_cycle(runtime, cycle_index, mode, intelligence, mid, source, equity_usd, pnl_usd, spread, inventory_usd)
        _append_equity_row(**equity_row_kwargs)
        return _kill_switch_allows_continue(pnl_usd, log_progress)

    fill = None
    if decision.action in {"BUY", "SELL"}:
        decision_filter_values = dict(decision.filter_values)
        order = engine.create_order_from_decision(
            decision.action,
            decision.order_price,
            decision.size_usd,
            mode,
        )
        if order.side == "sell":
            _cap_inventory_preserving_sell_order(runtime, order, decision.reason)

        order.trade_reason = decision.reason
        chunk_sizes, size_guard_values = _apply_trade_size_guard(
            runtime,
            order,
            cycle_index=cycle_index,
        )
        if size_guard_values:
            decision_filter_values = _merge_filter_values(decision_filter_values, **size_guard_values)

        execution_result = None
        if runtime.enable_execution_engine:
            execution_signal = ExecutionSignal(
                side=order.side,
                size_usd=order.size_usd,
                limit_price=order.price,
                trade_reason=decision.reason,
                mode=mode,
                source=decision.source,
                pair=_execution_pair(),
                router="uniswap_v3",
                inventory_cap_usd=decision.inventory_cap_usd or effective_max_inventory_usd,
                metadata=_build_execution_signal_metadata(
                    runtime,
                    side=order.side,
                    quote=quote,
                    size_usd=order.size_usd,
                    mode=mode,
                    trade_reason=decision.reason,
                    extra={"decision_source": decision.source},
                ),
            )
            execution_context = _build_execution_context(
                runtime=runtime,
                cycle_index=cycle_index,
                mode=mode,
                mid=mid,
                spread_bps=spread,
                intelligence=intelligence,
                quote=quote,
                router_price=order.price,
                source=source,
                effective_max_inventory_usd=effective_max_inventory_usd,
                size_usd=order.size_usd,
            )
            execution_result, execution_filter_values = _route_execution_signal(
                runtime,
                execution_signal,
                execution_context,
            )
            decision_filter_values = _merge_filter_values(decision_filter_values, **execution_filter_values)

        if execution_result and execution_result.allow_trade:
            _apply_execution_result_to_order(order, execution_result)
            if order.side == "sell":
                _cap_inventory_preserving_sell_order(runtime, order, order.trade_reason)
        elif execution_result:
            order.size_usd = 0.0
            order.size_base = 0.0
            _record_trade_gate(
                runtime,
                allow_trade=False,
                block_reason=execution_result.trade_blocked_reason,
                filter_values=decision_filter_values,
            )

        if order.size_usd > 0:
            trade_limit_decision = _evaluate_trade_limits(
                runtime,
                side=order.side,
                trade_size_usd=order.size_usd,
                inventory_usd=inventory_usd,
                equity_usd=equity_usd,
            )
            if trade_limit_decision.reason and not trade_limit_decision.stop_trading:
                decision_filter_values = _merge_filter_values(
                    decision_filter_values,
                    risk_stop_size_exceeded=True,
                    risk_stop_details=trade_limit_decision.details,
                )
            if trade_limit_decision.stop_trading:
                return _stop_for_risk_limit(
                    runtime,
                    cycle_index=cycle_index,
                    reason=trade_limit_decision.reason,
                    details=trade_limit_decision.details,
                    inventory_usd=inventory_usd,
                    equity_usd=equity_usd,
                    side=order.side,
                    trade_size_usd=order.size_usd,
                    mode=mode,
                    intelligence=intelligence,
                    mid=mid,
                    source=source,
                    spread=spread,
                    pnl_usd=pnl_usd,
                    equity_row_kwargs=equity_row_kwargs,
                    log_progress=log_progress,
                )

        if order.side == "sell" and runtime.enable_state_machine and order.size_usd >= min_notional_usd:
            runtime.state_machine.prepare_distribution(runtime.state_context, cycle_index, decision.reason)

        can_execute = False
        can_execute_reason = ""
        if order.side == "buy":
            if order.size_usd < min_notional_usd:
                can_execute_reason = runtime.last_decision_block_reason or "min_order_size"
            elif not engine.can_place_buy(
                decision.inventory_cap_usd or effective_max_inventory_usd,
                mid,
                order.size_usd,
                mode,
            ):
                can_execute_reason = "buy_inventory_cap"
            elif not _allows_opposite_side_trade(runtime, cycle_index, "buy", order.price):
                can_execute_reason = "side_flip_cooldown"
            else:
                can_execute = True
        else:
            if order.size_usd < min_notional_usd:
                can_execute_reason = runtime.last_decision_block_reason or "min_order_size"
            elif order.trade_reason not in FORCED_SELL_REASONS and not intelligence.sell_enabled:
                can_execute_reason = "sell_disabled"
            elif not engine.can_place_sell(order, mode):
                can_execute_reason = "protect_eth"
            elif not _allows_opposite_side_trade(runtime, cycle_index, "sell", order.price):
                can_execute_reason = "side_flip_cooldown"
            else:
                can_execute = True

        if can_execute:
            _record_trade_gate(runtime, allow_trade=True, filter_values=decision_filter_values)
        else:
            runtime.last_execution_analytics = update_block_reason(
                runtime.last_execution_analytics,
                can_execute_reason or runtime.last_decision_block_reason or "trade_blocked",
            )
            decision_filter_values = _merge_filter_values(
                decision_filter_values,
                **as_filter_values(runtime.last_execution_analytics),
            )
            _record_trade_gate(
                runtime,
                allow_trade=False,
                block_reason=can_execute_reason or runtime.last_decision_block_reason or "trade_blocked",
                filter_values=decision_filter_values,
            )

        if can_execute:
            if order.side == "buy":
                realized_pnl_before = portfolio.realized_pnl_usd
                fill = engine.simulate_fill(order, mid)
                realized_delta = portfolio.realized_pnl_usd - realized_pnl_before
                trade_analysis = _record_fill(runtime, cycle_index, mode, fill, realized_delta)
            else:
                trade_analysis = None
                fill = _execute_sell_chunks(
                    runtime,
                    cycle_index=cycle_index,
                    mode=mode,
                    mid=mid,
                    sell_order=order,
                    chunk_sizes=chunk_sizes,
                    trade_logger=trade_logger,
                    effective_max_inventory_usd=effective_max_inventory_usd,
                    fallback_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
                )
            if fill and fill.filled and order.side == "buy":
                if decision.reason == "reentry_timeout":
                    runtime.reentry_state.timeout_triggered = True
                elif decision.reason == "reentry_runaway":
                    runtime.reentry_state.runaway_triggered = True
                elif decision.reason == "reentry_max_miss":
                    runtime.reentry_state.max_miss_triggered = True
                if runtime.enable_state_machine:
                    runtime.state_machine.handle_buy_fill(runtime.state_context, cycle_index, decision.reason)
            if order.side == "buy":
                _append_trade_row(
                    trade_logger,
                    cycle_index,
                    runtime.state_context.current_state.value,
                    mode,
                    runtime.last_decision_source,
                    runtime.last_final_action,
                    runtime.last_overridden_signals,
                    runtime.last_allow_trade,
                    runtime.last_decision_block_reason,
                    runtime.last_filter_values,
                    fill,
                    portfolio,
                    runtime.last_execution_analytics,
                    trade_analysis=trade_analysis,
                )

    if runtime.enable_state_machine:
        runtime.state_machine.sync_cycle(
            context=runtime.state_context,
            cycle_index=cycle_index,
            reentry_state=runtime.reentry_state,
            portfolio_eth=portfolio.eth,
            min_eth_reserve=engine.min_eth_reserve,
        )

    inventory_usd = portfolio.inventory_usd(mid)
    equity_usd = portfolio.total_equity_usd(mid)
    pnl_usd = equity_usd - runtime.start_eq
    sizing = _update_runtime_sizing(runtime, equity_usd=equity_usd, mid=mid)
    min_notional_usd = sizing.min_notional_usd
    inventory_profile = runtime.inventory_manager.build_profile(
        regime=intelligence.regime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )

    _track_runtime_state(runtime, cycle_index, mid, inventory_usd, equity_usd, pnl_usd)
    _track_inventory_ratio(runtime, inventory_profile.inventory_ratio)
    runtime.last_profit_pct = _current_profit_pct(runtime, strategy_mid)
    runtime.last_buy_debug_reason = _finalize_buy_debug_reason(
        base_reason=runtime.last_buy_debug_reason,
        action=decision.action,
        buy_reason=decision.reason if decision.action == "BUY" else "",
        selected_reason=decision.reason,
        allow_trade=runtime.last_allow_trade,
        block_reason=runtime.last_decision_block_reason,
        buy_fill=fill if fill and fill.side == "buy" else None,
    )
    runtime.last_sell_debug_reason = _finalize_sell_debug_reason(
        base_reason=base_sell_debug_reason,
        action=decision.action,
        sell_reason=decision.reason if decision.action == "SELL" else "",
        selected_reason=decision.reason,
        allow_trade=runtime.last_allow_trade,
        block_reason=runtime.last_decision_block_reason,
        sell_fill=fill if fill and fill.side == "sell" else None,
    )
    equity_row_kwargs.update(
        {
            "state": runtime.state_context.current_state.value,
            "decision_source": runtime.last_decision_source,
            "final_action": runtime.last_final_action,
            "overridden_signals": runtime.last_overridden_signals,
            "allow_trade": runtime.last_allow_trade,
            "block_reason": runtime.last_decision_block_reason,
            "filter_values": runtime.last_filter_values,
            "inventory_usd": inventory_usd,
            "inventory_ratio": inventory_profile.inventory_ratio,
            "equity_usd": equity_usd,
            "pnl_usd": pnl_usd,
            "trade_count": engine.trade_count,
            "execution_price": runtime.last_execution_price,
            "reentry_state": runtime.reentry_engine.serialize_state(runtime.reentry_state),
            "state_context": runtime.state_machine.serialize(runtime.state_context),
            "time_in_state_sec": _time_in_state_seconds(runtime, cycle_index),
            "last_transition": _last_transition(runtime),
            "last_sell_price": runtime.reentry_state.last_sell_price,
            "reentry_levels": _serialize_buy_zones(runtime.reentry_state),
            "buy_zones": _serialize_buy_zones(runtime.reentry_state),
            "executed_buy_levels": json.dumps(runtime.reentry_state.executed_buy_levels, separators=(",", ":")),
            "reentry_active": runtime.reentry_state.active,
            "reentry_timeout": runtime.reentry_engine.timeout_remaining_cycles(runtime.reentry_state, cycle_index),
            "cooldown_remaining": (
                runtime.state_machine.cooldown_remaining_cycles(runtime.state_context, cycle_index)
                if runtime.enable_state_machine
                else 0
            ),
            "profit_lock_state": _serialize_profit_lock_state(runtime.profit_lock_state),
            "current_profit_pct": runtime.last_profit_pct,
            "buy_debug_reason": runtime.last_buy_debug_reason,
            "sell_debug_reason": runtime.last_sell_debug_reason,
            "last_execution_type": runtime.last_execution_type,
            "execution_analytics": runtime.last_execution_analytics,
            "last_slippage_bps": runtime.last_slippage_bps,
            "last_trade_reason": runtime.last_trade_reason,
        }
    )

    if log_progress:
        _log_cycle(runtime, cycle_index, mode, intelligence, mid, source, equity_usd, pnl_usd, spread, inventory_usd)
        _log_trade_intent(runtime, cycle_index)
        _log_execution_decision(runtime, cycle_index)
        if fill and fill.filled:
            log(
                f"{runtime.last_final_action} fill | {fill.size_usd:.2f} USD | "
                f"{runtime.last_decision_source}:{runtime.last_trade_reason}"
            )

    post_cycle_limit_decision = _evaluate_runtime_limits(runtime, inventory_usd, equity_usd)
    if post_cycle_limit_decision.stop_trading:
        _notify_risk_limit_stop(
            runtime,
            cycle_index=cycle_index,
            reason=post_cycle_limit_decision.reason,
            details=post_cycle_limit_decision.details,
        )

    _append_equity_row(**equity_row_kwargs)
    if post_cycle_limit_decision.stop_trading:
        return False
    return _kill_switch_allows_continue(pnl_usd, log_progress)


def process_price_tick(
    runtime: BotRuntime,
    cycle_index: int,
    mid: float,
    source: str,
    trade_logger=None,
    equity_logger=None,
    log_progress: bool = True,
) -> bool:
    if runtime.enable_decision_engine:
        return _process_price_tick_with_decision_engine(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=mid,
            source=source,
            trade_logger=trade_logger,
            equity_logger=equity_logger,
            log_progress=log_progress,
        )

    if mid <= 0:
        if log_progress:
            log(f"{cycle_index} | invalid price from {source}")
        return True

    portfolio = runtime.portfolio
    engine = runtime.engine

    portfolio.ensure_cost_basis(mid)
    _refresh_timeframe_prices(runtime, mid)
    strategy_mid = _strategy_mid(runtime, mid)
    runtime.current_regime_assessment = runtime.regime_detector.assess(_regime_prices(runtime))

    inventory_usd, equity_usd, pnl_usd = _account_state(runtime, mid)
    _track_runtime_state(runtime, cycle_index, mid, inventory_usd, equity_usd, pnl_usd, record_equity=True)
    _sync_daily_risk_state(runtime, equity_usd)
    sizing = _update_runtime_sizing(runtime, equity_usd=equity_usd, mid=mid)

    intelligence = runtime.intelligence.build_snapshot(
        prices=runtime.prices,
        current_equity=equity_usd,
        equity_peak=runtime.equity_peak if runtime.equity_peak is not None else equity_usd,
        recent_equities=list(runtime.recent_equities),
        inventory_usd=inventory_usd,
        regime_assessment=runtime.current_regime_assessment,
        cycle_index=cycle_index,
        cycle_seconds=runtime.cycle_seconds,
        recent_trade_cycles=_recent_trade_cycles(runtime),
        paper_mode=_paper_mode_enabled(),
    )
    runtime.current_market_mode = _intelligence_active_regime(intelligence)
    _refresh_timeframe_signals(runtime)
    _update_drawdown_guard(runtime, cycle_index=cycle_index, equity_usd=equity_usd)
    runtime.reentry_engine.update_state(runtime.reentry_state, strategy_mid)
    _update_profit_lock_state(runtime, strategy_mid)
    inventory_profile = runtime.inventory_manager.build_profile(
        regime=intelligence.regime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )
    _track_inventory_ratio(runtime, inventory_profile.inventory_ratio)
    _apply_runtime_regime_context(
        runtime,
        intelligence=intelligence,
        regime_assessment=runtime.current_regime_assessment,
        inventory_profile=inventory_profile,
        cycle_index=cycle_index,
    )
    if runtime.enable_state_machine:
        runtime.state_machine.sync_cycle(
            context=runtime.state_context,
            cycle_index=cycle_index,
            reentry_state=runtime.reentry_state,
            portfolio_eth=portfolio.eth,
            min_eth_reserve=engine.min_eth_reserve,
        )
    current_state = runtime.state_context.current_state.value
    runtime.state_counts[current_state] = runtime.state_counts.get(current_state, 0) + 1
    runtime.last_execution_analytics = blank_execution_analytics()
    execution_helpers.reset_execution_observation(runtime)
    runtime.current_edge_assessment = None
    runtime.current_signal_gate_decision = None
    runtime.current_entry_edge_bps = 0.0
    runtime.current_entry_edge_usd = 0.0
    runtime.current_signal_block_reason = ""

    min_notional_usd = sizing.min_notional_usd
    available_quote_usd = sizing.available_quote_to_trade_usd
    base_trade_size_usd = sizing.trade_size_usd
    effective_max_inventory_usd = max(sizing.max_position_usd * intelligence.max_inventory_multiplier, 0.0)
    spread = calculate_spread(intelligence.volatility, intelligence.spread_multiplier)
    min_sell_price = portfolio.min_profitable_sell_price(MAKER_FEE_BPS, MIN_SELL_PROFIT_BPS)
    mode = intelligence.mode
    runtime.mode_counts[mode] = runtime.mode_counts.get(mode, 0) + 1
    runtime.feed_state_counts[intelligence.feed_state] = runtime.feed_state_counts.get(intelligence.feed_state, 0) + 1

    trade_size_usd = choose_trade_size_usd(
        mode=mode,
        base_size=base_trade_size_usd,
        inventory_usd=inventory_usd,
        max_inventory_usd=effective_max_inventory_usd,
    ) * intelligence.trade_size_multiplier
    trade_size_usd = min(max(trade_size_usd, 0.0), base_trade_size_usd)
    buy_confirmation, current_rsi, _, _ = _buy_confirmation(runtime.prices)
    momentum_bps = calculate_recent_momentum_bps(runtime.prices)
    cooldown_remaining = (
        runtime.state_machine.cooldown_remaining_cycles(runtime.state_context, cycle_index)
        if runtime.enable_state_machine
        else 0
    )
    state_requires_reentry_only = runtime.enable_state_machine and runtime.state_machine.requires_reentry_only(
        runtime.state_context
    )
    buy_state_allowed = not runtime.enable_state_machine or runtime.state_machine.allow_buy(runtime.state_context)
    sell_state_allowed = not runtime.enable_state_machine or runtime.state_machine.allow_sell(runtime.state_context)
    in_cooldown = runtime.enable_state_machine and runtime.state_machine.in_cooldown(runtime.state_context)
    trend_filter_buy_allowed = _trend_filter_allows_buy(runtime)
    trend_filter_sell_allowed = _trend_filter_allows_sell(runtime)
    adjusted_buy_enabled = intelligence.buy_enabled and trend_filter_buy_allowed
    adjusted_sell_enabled = intelligence.sell_enabled and trend_filter_sell_allowed
    runtime.last_profit_pct = _current_profit_pct(runtime, strategy_mid)
    base_sell_debug_reason = _base_sell_debug_reason(
        runtime=runtime,
        cycle_index=cycle_index,
        mid=strategy_mid,
        sell_enabled=adjusted_sell_enabled,
        sell_state_allowed=sell_state_allowed,
        in_cooldown=in_cooldown,
        state_requires_reentry_only=state_requires_reentry_only,
    )
    runtime.last_sell_debug_reason = base_sell_debug_reason

    equity_row_kwargs = {
        "equity_logger": equity_logger,
        "cycle_index": cycle_index,
        "state": current_state,
        "mode": mode,
        "decision_source": runtime.last_decision_source,
        "final_action": runtime.last_final_action,
        "overridden_signals": runtime.last_overridden_signals,
        "allow_trade": runtime.last_allow_trade,
        "block_reason": runtime.last_decision_block_reason,
        "filter_values": runtime.last_filter_values,
        "feed_state": intelligence.feed_state,
        "regime": intelligence.regime,
        "volatility_state": intelligence.volatility_state,
        "mid": mid,
        "source": source,
        "short_ma": intelligence.short_ma,
        "long_ma": intelligence.long_ma,
        "volatility": intelligence.volatility,
        "spread": spread,
        "signal_score": intelligence.signal_score,
        "feed_score": intelligence.feed_score,
        "risk_score": intelligence.risk_score,
        "news_score": intelligence.news_score,
        "macro_score": intelligence.macro_score,
        "onchain_score": intelligence.onchain_score,
        "adaptive_score": intelligence.adaptive_score,
        "confidence": intelligence.confidence,
        "buy_enabled": adjusted_buy_enabled,
        "sell_enabled": adjusted_sell_enabled,
        "max_inventory_usd": effective_max_inventory_usd,
        "target_inventory_pct": intelligence.target_inventory_pct,
        "trade_size_multiplier": intelligence.trade_size_multiplier,
        "spread_multiplier": intelligence.spread_multiplier,
        "trade_size_usd": trade_size_usd,
        "inventory_usd": inventory_usd,
        "inventory_ratio": inventory_profile.inventory_ratio,
        "equity_usd": equity_usd,
        "pnl_usd": pnl_usd,
        "trade_count": engine.trade_count,
        "execution_price": runtime.last_execution_price,
        "reentry_state": runtime.reentry_engine.serialize_state(runtime.reentry_state),
        "state_context": runtime.state_machine.serialize(runtime.state_context),
        "time_in_state_sec": _time_in_state_seconds(runtime, cycle_index),
        "last_transition": _last_transition(runtime),
        "last_sell_price": runtime.reentry_state.last_sell_price,
        "reentry_levels": _serialize_buy_zones(runtime.reentry_state),
        "buy_zones": _serialize_buy_zones(runtime.reentry_state),
        "executed_buy_levels": json.dumps(runtime.reentry_state.executed_buy_levels, separators=(",", ":")),
        "reentry_active": runtime.reentry_state.active,
        "reentry_timeout": runtime.reentry_engine.timeout_remaining_cycles(runtime.reentry_state, cycle_index),
        "cooldown_remaining": cooldown_remaining,
        "profit_lock_state": _serialize_profit_lock_state(runtime.profit_lock_state),
        "current_profit_pct": runtime.last_profit_pct,
        "buy_debug_reason": runtime.last_buy_debug_reason,
        "sell_debug_reason": runtime.last_sell_debug_reason,
        "last_execution_type": runtime.last_execution_type,
        "execution_analytics": runtime.last_execution_analytics,
        "last_slippage_bps": runtime.last_slippage_bps,
        "last_trade_reason": runtime.last_trade_reason,
    }

    current_limit_decision = _evaluate_runtime_limits(runtime, inventory_usd, equity_usd)
    if current_limit_decision.stop_trading:
        return _stop_for_risk_limit(
            runtime,
            cycle_index=cycle_index,
            reason=current_limit_decision.reason,
            details=current_limit_decision.details,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            mode=mode,
            intelligence=intelligence,
            mid=mid,
            source=source,
            spread=spread,
            pnl_usd=pnl_usd,
            equity_row_kwargs=equity_row_kwargs,
            log_progress=log_progress,
        )

    if mode == "NO_TRADE":
        blockers = getattr(intelligence, "blockers", [])
        no_trade_reason = "drawdown_pause" if "drawdown_pause" in blockers else "no_trade"
        _record_decision(runtime, DecisionOutcome(action="NONE", reason=no_trade_reason, source="legacy"))
        _record_trade_gate(runtime, allow_trade=False, block_reason=no_trade_reason, filter_values={"mode": mode})
        runtime.last_buy_debug_reason = no_trade_reason
        runtime.last_sell_debug_reason = no_trade_reason
        equity_row_kwargs.update(
            {
                "decision_source": runtime.last_decision_source,
                "final_action": runtime.last_final_action,
                "overridden_signals": runtime.last_overridden_signals,
                "allow_trade": runtime.last_allow_trade,
                "block_reason": runtime.last_decision_block_reason,
                "filter_values": runtime.last_filter_values,
                "buy_debug_reason": runtime.last_buy_debug_reason,
                "sell_debug_reason": runtime.last_sell_debug_reason,
            }
        )
        if log_progress:
            _log_cycle(runtime, cycle_index, mode, intelligence, mid, source, equity_usd, pnl_usd, spread, inventory_usd)

        _append_equity_row(**equity_row_kwargs)

        return _kill_switch_allows_continue(pnl_usd, log_progress)

    quote = build_quotes(
        mid=strategy_mid,
        spread_bps=spread,
        inventory_usd=inventory_usd,
        max_inventory_usd=effective_max_inventory_usd,
        inventory_skew_strength=INVENTORY_SKEW_STRENGTH * intelligence.inventory_skew_multiplier,
        directional_bias=intelligence.directional_bias,
    )
    buy_order, sell_order = engine.create_orders(quote.bid, quote.ask, trade_size_usd, mode)

    trend_buy_target_pct = min(TREND_BUY_TARGET_PCT, intelligence.target_inventory_pct)
    trend_signal_allows_buy = (
        not in_cooldown
        and adjusted_buy_enabled
        and should_place_trend_buy(
            mid=strategy_mid,
            short_ma=intelligence.short_ma,
            long_ma=intelligence.long_ma,
            trend_strength=intelligence.trend_strength,
            market_score=intelligence.market_score,
            signal_score=intelligence.signal_score,
            confidence=intelligence.confidence,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            max_inventory_usd=effective_max_inventory_usd,
            trend_buy_target_pct=trend_buy_target_pct,
            max_trend_chase_bps=MAX_TREND_CHASE_BPS * intelligence.max_chase_bps_multiplier,
            max_trend_pullback_bps=MAX_TREND_PULLBACK_BPS,
            trend_buy_min_market_score=TREND_BUY_MIN_MARKET_SCORE,
            trend_buy_min_signal_score=TREND_BUY_MIN_SIGNAL_SCORE,
            trend_buy_min_confidence=TREND_BUY_MIN_CONFIDENCE,
            trend_buy_min_long_buffer_bps=TREND_BUY_MIN_LONG_BUFFER_BPS,
            trend_buy_min_strength_multiplier=TREND_BUY_MIN_STRENGTH_MULTIPLIER,
        )
    )
    trend_buy_allowed = buy_state_allowed and not state_requires_reentry_only and trend_signal_allows_buy
    if trend_buy_allowed and mode == "TREND_UP":
        buy_order.price = requote_trend_buy_price(
            current_bid=buy_order.price,
            mid=strategy_mid,
            trend_buy_requote_bps=TREND_BUY_REQUOTE_BPS,
        )
        buy_order.size_base = buy_order.size_usd / buy_order.price

    if mode == "TREND_UP":
        sell_order.price = requote_trend_sell_price(
            current_ask=sell_order.price,
            mid=strategy_mid,
            trend_sell_spread_factor=TREND_SELL_SPREAD_FACTOR,
        )
        sell_order.size_base = sell_order.size_usd / sell_order.price
        _cap_trend_sell_order(
            mode=mode,
            sell_order=sell_order,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
            effective_max_inventory_usd=effective_max_inventory_usd,
            target_inventory_pct=trend_buy_target_pct,
            mid=strategy_mid,
        )

    max_workable_sell_price = strategy_mid * (1.0 + (MAX_EXIT_PREMIUM_BPS / 10000.0))
    if mode == "OVERWEIGHT_EXIT":
        if min_sell_price is None:
            sell_order.price = min(sell_order.price, max_workable_sell_price)
        elif min_sell_price <= max_workable_sell_price:
            sell_order.price = max(sell_order.price, min_sell_price)
        else:
            sell_order.price = max_workable_sell_price
        sell_order.size_base = sell_order.size_usd / sell_order.price
    elif min_sell_price is not None and sell_order.price < min_sell_price:
        sell_order.price = min_sell_price
        sell_order.size_base = sell_order.size_usd / sell_order.price

    buy_reason = "trend_buy"
    buy_inventory_cap = effective_max_inventory_usd
    reentry_plan = None
    partial_reset_reason = None
    partial_reset_size_usd = 0.0
    buy_drift_block_reason = ""
    sell_drift_block_reason = ""

    if not in_cooldown and runtime.enable_reentry_engine:
        allow_reentry_plan = not runtime.enable_state_machine or state_requires_reentry_only
        if allow_reentry_plan:
            reentry_plan = runtime.reentry_engine.build_scale_in_plan(
                state=runtime.reentry_state,
                cycle_index=cycle_index,
                mid=strategy_mid,
                room_usd=available_quote_usd,
                trend_buy_allowed=trend_signal_allows_buy,
                buy_confirmation=buy_confirmation,
            )
    if (
        not in_cooldown
        and adjusted_buy_enabled
        and buy_state_allowed
        and not state_requires_reentry_only
    ):
        partial_reset_reason, partial_reset_size_usd = _build_partial_reset_buy_plan(
            runtime=runtime,
            equity_usd=equity_usd,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=effective_max_inventory_usd,
            base_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
            trend_buy_allowed=trend_signal_allows_buy,
            buy_confirmation=buy_confirmation,
            target_inventory_pct=intelligence.target_inventory_pct,
        )

    buy_enabled = trend_buy_allowed
    if reentry_plan and reentry_plan.allow_trade and reentry_plan.size_usd >= min_notional_usd:
        buy_reason = reentry_plan.trade_reason
        buy_enabled = True
        buy_inventory_cap = _reentry_buy_inventory_cap(runtime, inventory_usd, effective_max_inventory_usd)
        buy_order.size_usd = min(reentry_plan.size_usd, available_quote_usd)
        buy_order.size_base = buy_order.size_usd / buy_order.price if buy_order.price > 0 else 0.0
    elif partial_reset_reason and partial_reset_size_usd >= min_notional_usd:
        buy_reason = partial_reset_reason
        buy_enabled = True
        buy_inventory_cap = effective_max_inventory_usd * (1.0 + max(REENTRY_INVENTORY_BUFFER_PCT, 0.0))
        buy_order.price = strategy_mid
        buy_order.size_usd = min(partial_reset_size_usd, available_quote_usd)
        buy_order.size_base = buy_order.size_usd / buy_order.price if buy_order.price > 0 else 0.0
    elif not buy_state_allowed or in_cooldown:
        buy_enabled = False
        buy_order.size_usd = 0.0
        buy_order.size_base = 0.0

    sell_reason = "quoted_sell"
    profit_lock_reason = None
    profit_lock_size_usd = 0.0
    profit_lock_allowed = not runtime.enable_state_machine or runtime.state_context.current_state in {
        StrategyState.ACCUMULATING,
        StrategyState.DISTRIBUTING,
    }
    if not in_cooldown and sell_state_allowed and profit_lock_allowed:
        profit_lock_reason, profit_lock_size_usd = _build_profit_lock_sell_plan(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=strategy_mid,
        )
    if profit_lock_reason and profit_lock_size_usd >= min_notional_usd:
        sell_reason = profit_lock_reason
        sell_order.price = strategy_mid
        sell_order.size_usd = profit_lock_size_usd
        sell_order.size_base = sell_order.size_usd / sell_order.price if sell_order.price > 0 else 0.0
    elif _should_delay_regular_sell(runtime, mode):
        sell_reason = "delayed_for_profit_lock"
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0
    elif not sell_state_allowed or in_cooldown or state_requires_reentry_only or not adjusted_sell_enabled:
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0

    if getattr(inventory_profile, "force_limit_hit", False) and sell_state_allowed and not in_cooldown:
        forced_reduce_size_usd = min(max(trade_size_usd, min_notional_usd), inventory_profile.max_sell_usd)
        if forced_reduce_size_usd >= min_notional_usd:
            sell_reason = "inventory_force_reduce"
            sell_order.price = _forced_inventory_reduce_sell_price(
                strategy_mid=strategy_mid,
                strategy_sell_price=sell_order.price,
                min_sell_price=min_sell_price,
            )
            sell_order.size_usd = forced_reduce_size_usd
            sell_order.size_base = sell_order.size_usd / sell_order.price if sell_order.price > 0 else 0.0

    force_trade_candidate = None
    if buy_order.size_usd < min_notional_usd and sell_order.size_usd < min_notional_usd:
        force_trade_candidate = _build_force_trade_candidate(
            runtime=runtime,
            cycle_index=cycle_index,
            mid=strategy_mid,
            inventory_usd=inventory_usd,
            effective_max_inventory_usd=effective_max_inventory_usd,
            buy_state_allowed=buy_state_allowed,
            sell_state_allowed=sell_state_allowed,
            state_requires_reentry_only=state_requires_reentry_only,
            in_cooldown=in_cooldown,
            base_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
        )
        blocked_reason = ""
        if force_trade_candidate is not None:
            blocked_reason = _inventory_drift_block_reason(
                runtime,
                force_trade_candidate.action,
                force_trade_candidate.reason,
            )
        if blocked_reason:
            if "buy" in blocked_reason:
                buy_drift_block_reason = blocked_reason
            else:
                sell_drift_block_reason = blocked_reason
            force_trade_candidate = None
        elif force_trade_candidate is not None and force_trade_candidate.action == "BUY":
            buy_reason = force_trade_candidate.reason
            buy_enabled = True
            buy_inventory_cap = force_trade_candidate.inventory_cap_usd
            buy_order.price = force_trade_candidate.order_price
            buy_order.size_usd = min(force_trade_candidate.size_usd, available_quote_usd)
            buy_order.size_base = buy_order.size_usd / buy_order.price if buy_order.price > 0 else 0.0
        elif force_trade_candidate is not None and force_trade_candidate.action == "SELL":
            sell_reason = force_trade_candidate.reason
            sell_order.price = force_trade_candidate.order_price
            sell_order.size_usd = force_trade_candidate.size_usd
            sell_order.size_base = sell_order.size_usd / sell_order.price if sell_order.price > 0 else 0.0

    if buy_order.size_usd >= min_notional_usd:
        buy_drift_block_reason = buy_drift_block_reason or _inventory_drift_block_reason(runtime, "BUY", buy_reason)
        if buy_drift_block_reason:
            buy_enabled = False
            buy_order.size_usd = 0.0
            buy_order.size_base = 0.0

    if sell_order.size_usd >= min_notional_usd:
        sell_drift_block_reason = sell_drift_block_reason or _inventory_drift_block_reason(runtime, "SELL", sell_reason)
        if sell_drift_block_reason:
            sell_order.size_usd = 0.0
            sell_order.size_base = 0.0

    runtime.last_buy_debug_reason = _base_buy_debug_reason(
        runtime=runtime,
        cycle_index=cycle_index,
        mid=strategy_mid,
        buy_enabled=adjusted_buy_enabled,
        buy_state_allowed=buy_state_allowed,
        in_cooldown=in_cooldown,
        reentry_plan=reentry_plan,
        partial_reset_reason=partial_reset_reason,
        force_trade_candidate=force_trade_candidate,
        trend_signal_allows_buy=trend_signal_allows_buy,
    )
    if buy_drift_block_reason:
        runtime.last_buy_debug_reason = buy_drift_block_reason
    runtime.last_sell_debug_reason = sell_drift_block_reason or base_sell_debug_reason

    if sell_reason in PRIORITY_SELL_REASONS and sell_order.size_usd >= min_notional_usd:
        buy_enabled = False
        buy_order.size_usd = 0.0
        buy_order.size_base = 0.0

    if (
        runtime.enable_state_machine
        and runtime.state_context.current_state == StrategyState.DISTRIBUTING
        and sell_order.size_usd < min_notional_usd
        and runtime.reentry_state.active
    ):
        runtime.state_machine.transition(
            runtime.state_context,
            StrategyState.WAIT_REENTRY,
            cycle_index,
            "distribution_complete",
        )

    _cap_inventory_preserving_sell_order(runtime, sell_order, sell_reason)
    if runtime.enable_inventory_manager:
        if not (buy_reason.startswith("reentry_") or buy_reason.startswith("force_trade_")):
            buy_order.size_usd = runtime.inventory_manager.cap_buy_usd(
                inventory_profile,
                buy_order.size_usd,
                available_quote_usd,
            )
        if sell_reason not in PRIORITY_SELL_REASONS and not sell_reason.startswith("force_trade_"):
            sell_order.size_usd = runtime.inventory_manager.cap_sell_usd(
                inventory_profile,
                sell_order.size_usd,
            )
        if buy_order.price > 0:
            buy_order.size_base = buy_order.size_usd / buy_order.price
        if sell_order.price > 0:
            sell_order.size_base = sell_order.size_usd / sell_order.price

    buy_order.trade_reason = buy_reason
    sell_order.trade_reason = sell_reason

    legacy_source = "strategy"
    if buy_reason.startswith("reentry_"):
        legacy_source = "reentry"
    elif buy_reason.startswith("force_trade_"):
        legacy_source = "force_trade"
    elif buy_reason in {"partial_reset"}:
        legacy_source = "inventory"
    if sell_reason.startswith("force_trade_"):
        legacy_source = "force_trade"
    elif sell_reason == "inventory_correction" or mode == "OVERWEIGHT_EXIT":
        legacy_source = "inventory"

    legacy_filter_values = {
        "buy_reason": buy_reason,
        "sell_reason": sell_reason,
        "buy_enabled": buy_enabled,
        "buy_size_usd": round(buy_order.size_usd, 6),
        "sell_size_usd": round(sell_order.size_usd, 6),
        "buy_drift_block_reason": buy_drift_block_reason,
        "sell_drift_block_reason": sell_drift_block_reason,
        "trend_buy_allowed": trend_buy_allowed,
        "state_requires_reentry_only": state_requires_reentry_only,
        "in_cooldown": in_cooldown,
        "reentry_active": runtime.reentry_state.active,
    }
    legacy_block_reason = ""
    if buy_drift_block_reason or sell_drift_block_reason:
        legacy_block_reason = buy_drift_block_reason or sell_drift_block_reason
    elif sell_reason == "delayed_for_profit_lock":
        legacy_block_reason = "profit_lock_wait"
    elif state_requires_reentry_only and runtime.reentry_state.active:
        legacy_block_reason = "reentry_wait"
    elif in_cooldown:
        legacy_block_reason = "cooldown_state"
    else:
        legacy_block_reason = "no_signal"

    legacy_decision = DecisionOutcome(
        action="NONE",
        reason=legacy_block_reason,
        source="legacy",
        block_reason=legacy_block_reason,
        filter_values=legacy_filter_values,
    )
    if buy_enabled and buy_order.size_usd >= min_notional_usd:
        legacy_decision = DecisionOutcome(
            action="BUY",
            size_usd=buy_order.size_usd,
            reason=buy_reason,
            source=legacy_source,
            order_price=buy_order.price,
            inventory_cap_usd=buy_inventory_cap,
            filter_values=legacy_filter_values,
        )
    elif sell_order.size_usd >= min_notional_usd:
        legacy_decision = DecisionOutcome(
            action="SELL",
            size_usd=sell_order.size_usd,
            reason=sell_reason,
            source="inventory" if mode == "OVERWEIGHT_EXIT" else "strategy",
            order_price=sell_order.price,
            inventory_cap_usd=buy_inventory_cap,
            filter_values=legacy_filter_values,
        )
    requested_legacy_action = legacy_decision.action
    legacy_decision = _apply_signal_pipeline(
        runtime,
        cycle_index=cycle_index,
        decision=legacy_decision,
        strategy_mode=mode,
        intelligence=intelligence,
        quote=quote,
        mid=mid,
        spread_bps=spread,
        source=source,
        effective_max_inventory_usd=effective_max_inventory_usd,
        inventory_profile=inventory_profile,
        inventory_usd=inventory_usd,
    )
    if legacy_decision.action == "NONE" and requested_legacy_action == "BUY":
        buy_enabled = False
        buy_order.size_usd = 0.0
        buy_order.size_base = 0.0
    elif legacy_decision.action == "NONE" and requested_legacy_action == "SELL":
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0
    _record_decision(runtime, legacy_decision)
    _record_trade_gate(
        runtime,
        allow_trade=legacy_decision.action in {"BUY", "SELL"},
        block_reason=legacy_decision.block_reason,
        filter_values=legacy_decision.filter_values,
    )

    buy_filter_result = runtime.trade_filter.evaluate(
        side="buy",
        trade_reason=buy_reason,
        cycle_index=cycle_index,
        order_price=buy_order.price,
        last_trade_cycle=runtime.last_trade_cycle_any,
        last_trade_price=runtime.last_trade_price_any,
        loss_streak=runtime.loss_streak,
        rsi_value=current_rsi,
        momentum_bps=momentum_bps,
        regime=intelligence.regime,
        market_score=intelligence.market_score,
        volatility_state=intelligence.volatility_state,
        trade_count=engine.trade_count,
        daily_trade_count=runtime.daily_trade_count,
    ) if runtime.enable_trade_filter else None
    sell_filter_result = runtime.trade_filter.evaluate(
        side="sell",
        trade_reason=sell_reason,
        cycle_index=cycle_index,
        order_price=sell_order.price,
        last_trade_cycle=runtime.last_trade_cycle_any,
        last_trade_price=runtime.last_trade_price_any,
        loss_streak=runtime.loss_streak,
        rsi_value=current_rsi,
        momentum_bps=momentum_bps,
        regime=intelligence.regime,
        market_score=intelligence.market_score,
        volatility_state=intelligence.volatility_state,
        trade_count=engine.trade_count,
        daily_trade_count=runtime.daily_trade_count,
    ) if runtime.enable_trade_filter else None
    selected_filter_result = None
    selected_filter_values = dict(legacy_decision.filter_values)
    if legacy_decision.action == "BUY":
        selected_filter_result = buy_filter_result
    elif legacy_decision.action == "SELL":
        selected_filter_result = sell_filter_result

    if selected_filter_result is not None:
        selected_filter_values = _merge_filter_values(selected_filter_values, **selected_filter_result.filter_values)
        if not selected_filter_result.allow_trade and selected_filter_result.block_reason != "loss_streak_pause":
            _record_trade_gate(
                runtime,
                allow_trade=False,
                block_reason=selected_filter_result.block_reason,
                filter_values=selected_filter_values,
            )

    if buy_filter_result and buy_filter_result.size_multiplier > 0:
        buy_order.size_usd *= buy_filter_result.size_multiplier
        buy_order.size_base = buy_order.size_usd / buy_order.price if buy_order.price > 0 else 0.0
    if sell_filter_result and sell_filter_result.size_multiplier > 0:
        sell_order.size_usd *= sell_filter_result.size_multiplier
        sell_order.size_base = sell_order.size_usd / sell_order.price if sell_order.price > 0 else 0.0
    if (
        buy_filter_result is not None
        and buy_filter_result.allow_trade
        and 0.0 < buy_order.size_usd < min_notional_usd
    ):
        selected_filter_values["size_clamped_to_min"] = True
        selected_filter_values["pre_clamp_size_usd"] = round(buy_order.size_usd, 6)
        buy_order.size_usd = min_notional_usd
        buy_order.size_base = buy_order.size_usd / buy_order.price if buy_order.price > 0 else 0.0
    if (
        sell_filter_result is not None
        and sell_filter_result.allow_trade
        and 0.0 < sell_order.size_usd < min_notional_usd
    ):
        selected_filter_values["size_clamped_to_min"] = True
        selected_filter_values["pre_clamp_size_usd"] = round(sell_order.size_usd, 6)
        sell_order.size_usd = min_notional_usd
        sell_order.size_base = sell_order.size_usd / sell_order.price if sell_order.price > 0 else 0.0

    buy_chunk_sizes, buy_size_guard_values = _apply_trade_size_guard(
        runtime,
        buy_order,
        cycle_index=cycle_index,
    )
    sell_chunk_sizes, sell_size_guard_values = _apply_trade_size_guard(
        runtime,
        sell_order,
        cycle_index=cycle_index,
    )
    if buy_size_guard_values:
        selected_filter_values = _merge_filter_values(selected_filter_values, **buy_size_guard_values)
    if sell_size_guard_values:
        selected_filter_values = _merge_filter_values(selected_filter_values, **sell_size_guard_values)

    buy_execution_result = None
    sell_execution_result = None
    buy_execution_analytics = blank_execution_analytics()
    sell_execution_analytics = blank_execution_analytics()
    buy_execution_context = None
    sell_execution_context = None
    if runtime.enable_execution_engine and buy_order.size_usd > 0:
        buy_execution_context = _build_execution_context(
            runtime=runtime,
            cycle_index=cycle_index,
            mode=mode,
            mid=mid,
            spread_bps=spread,
            intelligence=intelligence,
            quote=quote,
            router_price=buy_order.price,
            source=source,
            effective_max_inventory_usd=effective_max_inventory_usd,
            size_usd=buy_order.size_usd,
        )
        buy_execution_result = runtime.execution_router.execute_trade(
            ExecutionSignal(
                side="buy",
                size_usd=buy_order.size_usd,
                limit_price=buy_order.price,
                trade_reason=buy_reason,
                mode=mode,
                source=legacy_source,
                pair=_execution_pair(),
                router="uniswap_v3",
                inventory_cap_usd=buy_inventory_cap,
                metadata=_build_execution_signal_metadata(
                    runtime,
                    side="buy",
                    quote=quote,
                    size_usd=buy_order.size_usd,
                    mode=mode,
                    trade_reason=buy_reason,
                    extra={"legacy": True},
                ),
            ),
            buy_execution_context,
        )
        execution_helpers.capture_execution_result(runtime, buy_execution_context, buy_execution_result)
        buy_execution_analytics = analytics_from_result(buy_execution_result)
    if runtime.enable_execution_engine and sell_order.size_usd > 0:
        sell_execution_context = _build_execution_context(
            runtime=runtime,
            cycle_index=cycle_index,
            mode=mode,
            mid=mid,
            spread_bps=spread,
            intelligence=intelligence,
            quote=quote,
            router_price=sell_order.price,
            source=source,
            effective_max_inventory_usd=effective_max_inventory_usd,
            size_usd=sell_order.size_usd,
        )
        sell_execution_result = runtime.execution_router.execute_trade(
            ExecutionSignal(
                side="sell",
                size_usd=sell_order.size_usd,
                limit_price=sell_order.price,
                trade_reason=sell_reason,
                mode=mode,
                source=legacy_source,
                pair=_execution_pair(),
                router="uniswap_v3",
                inventory_cap_usd=buy_inventory_cap,
                metadata=_build_execution_signal_metadata(
                    runtime,
                    side="sell",
                    quote=quote,
                    size_usd=sell_order.size_usd,
                    mode=mode,
                    trade_reason=sell_reason,
                    extra={"legacy": True},
                ),
            ),
            sell_execution_context,
        )
        execution_helpers.capture_execution_result(runtime, sell_execution_context, sell_execution_result)
        sell_execution_analytics = analytics_from_result(sell_execution_result)

    selected_execution_result = None
    selected_execution_context = None
    runtime.last_execution_analytics = blank_execution_analytics()
    execution_helpers.reset_execution_observation(runtime)
    if legacy_decision.action == "BUY":
        selected_execution_result = buy_execution_result
        selected_execution_context = buy_execution_context
        runtime.last_execution_analytics = buy_execution_analytics
    elif legacy_decision.action == "SELL":
        selected_execution_result = sell_execution_result
        selected_execution_context = sell_execution_context
        runtime.last_execution_analytics = sell_execution_analytics
    if selected_execution_result is not None:
        if selected_execution_context is not None:
            execution_helpers.capture_execution_result(runtime, selected_execution_context, selected_execution_result)
        selected_filter_values = _merge_filter_values(
            selected_filter_values,
            **as_filter_values(runtime.last_execution_analytics),
            **_execution_metadata_filter_values(runtime.last_execution_metadata),
        )
        if not selected_execution_result.allow_trade:
            _record_trade_gate(
                runtime,
                allow_trade=False,
                block_reason=selected_execution_result.trade_blocked_reason,
                filter_values=selected_filter_values,
            )

    if buy_execution_result and buy_execution_result.allow_trade:
        _apply_execution_result_to_order(buy_order, buy_execution_result)
    elif buy_execution_result:
        buy_enabled = False

    if sell_execution_result and sell_execution_result.allow_trade:
        _apply_execution_result_to_order(sell_order, sell_execution_result)
        _cap_inventory_preserving_sell_order(runtime, sell_order, sell_order.trade_reason)
    elif sell_execution_result:
        sell_order.size_usd = 0.0
        sell_order.size_base = 0.0

    selected_trade_side = ""
    selected_trade_size_usd = 0.0
    if legacy_decision.action == "BUY":
        selected_trade_side = "buy"
        selected_trade_size_usd = buy_order.size_usd
    elif legacy_decision.action == "SELL":
        selected_trade_side = "sell"
        selected_trade_size_usd = sell_order.size_usd

    if selected_trade_side and selected_trade_size_usd > 0:
        trade_limit_decision = _evaluate_trade_limits(
            runtime,
            side=selected_trade_side,
            trade_size_usd=selected_trade_size_usd,
            inventory_usd=inventory_usd,
            equity_usd=equity_usd,
        )
        if trade_limit_decision.reason and not trade_limit_decision.stop_trading:
            selected_filter_values = _merge_filter_values(
                selected_filter_values,
                risk_stop_size_exceeded=True,
                risk_stop_details=trade_limit_decision.details,
            )
        if trade_limit_decision.stop_trading:
            return _stop_for_risk_limit(
                runtime,
                cycle_index=cycle_index,
                reason=trade_limit_decision.reason,
                details=trade_limit_decision.details,
                inventory_usd=inventory_usd,
                equity_usd=equity_usd,
                side=selected_trade_side,
                trade_size_usd=selected_trade_size_usd,
                mode=mode,
                intelligence=intelligence,
                mid=mid,
                source=source,
                spread=spread,
                pnl_usd=pnl_usd,
                equity_row_kwargs=equity_row_kwargs,
                log_progress=log_progress,
            )

    if (
        runtime.enable_state_machine
        and sell_order.size_usd >= min_notional_usd
        and (sell_reason in FORCED_SELL_REASONS or intelligence.sell_enabled)
        and (sell_filter_result is None or sell_filter_result.allow_trade)
    ):
        runtime.state_machine.prepare_distribution(runtime.state_context, cycle_index, sell_reason)

    buy_fill = None
    sell_fill = None
    realized_pnl_before = portfolio.realized_pnl_usd
    can_execute_reason = runtime.last_decision_block_reason
    can_execute = False

    if legacy_decision.action == "BUY":
        if not buy_enabled:
            can_execute_reason = "buy_disabled"
        elif buy_filter_result is not None and not buy_filter_result.allow_trade and buy_filter_result.block_reason != "loss_streak_pause":
            can_execute_reason = buy_filter_result.block_reason
        elif buy_execution_result is not None and not buy_execution_result.allow_trade:
            can_execute_reason = buy_execution_result.trade_blocked_reason
        elif buy_order.size_usd < min_notional_usd:
            can_execute_reason = (
                "size_below_min_after_filter"
                if buy_filter_result is not None and buy_filter_result.size_multiplier != 1.0
                else "min_order_size"
            )
        elif not engine.can_place_buy(buy_inventory_cap, mid, buy_order.size_usd, mode):
            can_execute_reason = "buy_inventory_cap"
        elif not _allows_opposite_side_trade(runtime, cycle_index, "buy", buy_order.price):
            can_execute_reason = "side_flip_cooldown"
        else:
            can_execute = True
    elif legacy_decision.action == "SELL":
        if sell_reason not in FORCED_SELL_REASONS and not intelligence.sell_enabled:
            can_execute_reason = "sell_disabled"
        elif sell_filter_result is not None and not sell_filter_result.allow_trade and sell_filter_result.block_reason != "loss_streak_pause":
            can_execute_reason = sell_filter_result.block_reason
        elif sell_execution_result is not None and not sell_execution_result.allow_trade:
            can_execute_reason = sell_execution_result.trade_blocked_reason
        elif sell_order.size_usd < min_notional_usd:
            can_execute_reason = (
                "size_below_min_after_filter"
                if sell_filter_result is not None and sell_filter_result.size_multiplier != 1.0
                else "min_order_size"
            )
        elif not engine.can_place_sell(sell_order, mode):
            can_execute_reason = "protect_eth"
        elif not _allows_opposite_side_trade(runtime, cycle_index, "sell", sell_order.price):
            can_execute_reason = "side_flip_cooldown"
        else:
            can_execute = True

    if legacy_decision.action in {"BUY", "SELL"}:
        if not can_execute:
            runtime.last_execution_analytics = update_block_reason(
                runtime.last_execution_analytics,
                can_execute_reason or "trade_blocked",
            )
            selected_filter_values = _merge_filter_values(
                selected_filter_values,
                **as_filter_values(runtime.last_execution_analytics),
            )
        _record_trade_gate(
            runtime,
            allow_trade=can_execute,
            block_reason="" if can_execute else (can_execute_reason or "trade_blocked"),
            filter_values=selected_filter_values,
        )

    if (
        buy_enabled
        and buy_order.size_usd >= min_notional_usd
        and (buy_filter_result is None or buy_filter_result.allow_trade or buy_filter_result.block_reason == "loss_streak_pause")
        and engine.can_place_buy(buy_inventory_cap, mid, buy_order.size_usd, mode)
        and _allows_opposite_side_trade(runtime, cycle_index, "buy", buy_order.price)
    ):
        runtime.last_execution_analytics = buy_execution_analytics
        if buy_execution_context is not None:
            execution_helpers.capture_execution_result(runtime, buy_execution_context, buy_execution_result)
        buy_fill = engine.simulate_fill(buy_order, mid)
        realized_delta = portfolio.realized_pnl_usd - realized_pnl_before
        trade_analysis = _record_fill(runtime, cycle_index, mode, buy_fill, realized_delta)
        if buy_fill.filled:
            if buy_reason == "reentry_timeout":
                runtime.reentry_state.timeout_triggered = True
            elif buy_reason == "reentry_runaway":
                runtime.reentry_state.runaway_triggered = True
            elif buy_reason == "reentry_max_miss":
                runtime.reentry_state.max_miss_triggered = True
            if runtime.enable_state_machine:
                runtime.state_machine.handle_buy_fill(runtime.state_context, cycle_index, buy_reason)
        _append_trade_row(
            trade_logger,
            cycle_index,
            runtime.state_context.current_state.value,
            mode,
            runtime.last_decision_source,
            runtime.last_final_action,
            runtime.last_overridden_signals,
            runtime.last_allow_trade,
            runtime.last_decision_block_reason,
            runtime.last_filter_values,
            buy_fill,
            portfolio,
            runtime.last_execution_analytics,
            trade_analysis=trade_analysis,
        )
        realized_pnl_before = portfolio.realized_pnl_usd

    if (
        not (buy_fill and buy_fill.filled)
        and sell_order.size_usd >= min_notional_usd
        and (sell_reason in FORCED_SELL_REASONS or intelligence.sell_enabled)
        and (sell_filter_result is None or sell_filter_result.allow_trade or sell_filter_result.block_reason == "loss_streak_pause")
        and engine.can_place_sell(sell_order, mode)
        and _allows_opposite_side_trade(runtime, cycle_index, "sell", sell_order.price)
    ):
        runtime.last_execution_analytics = sell_execution_analytics
        if sell_execution_context is not None:
            execution_helpers.capture_execution_result(runtime, sell_execution_context, sell_execution_result)
        sell_fill = _execute_sell_chunks(
            runtime,
            cycle_index=cycle_index,
            mode=mode,
            mid=mid,
            sell_order=sell_order,
            chunk_sizes=sell_chunk_sizes,
            trade_logger=trade_logger,
            effective_max_inventory_usd=effective_max_inventory_usd,
            fallback_trade_size_usd=max(trade_size_usd, base_trade_size_usd),
        )

    if runtime.enable_state_machine:
        runtime.state_machine.sync_cycle(
            context=runtime.state_context,
            cycle_index=cycle_index,
            reentry_state=runtime.reentry_state,
            portfolio_eth=portfolio.eth,
            min_eth_reserve=engine.min_eth_reserve,
        )

    inventory_usd = portfolio.inventory_usd(mid)
    equity_usd = portfolio.total_equity_usd(mid)
    pnl_usd = equity_usd - runtime.start_eq
    sizing = _update_runtime_sizing(runtime, equity_usd=equity_usd, mid=mid)
    min_notional_usd = sizing.min_notional_usd
    inventory_profile = runtime.inventory_manager.build_profile(
        regime=intelligence.regime,
        inventory_usd=inventory_usd,
        equity_usd=equity_usd,
    )

    _track_runtime_state(runtime, cycle_index, mid, inventory_usd, equity_usd, pnl_usd)
    _track_inventory_ratio(runtime, inventory_profile.inventory_ratio)
    _apply_runtime_regime_context(
        runtime,
        intelligence=intelligence,
        regime_assessment=runtime.current_regime_assessment,
        inventory_profile=inventory_profile,
        cycle_index=cycle_index,
    )
    runtime.last_profit_pct = _current_profit_pct(runtime, strategy_mid)
    runtime.last_buy_debug_reason = _finalize_buy_debug_reason(
        base_reason=runtime.last_buy_debug_reason,
        action=legacy_decision.action,
        buy_reason=buy_reason if legacy_decision.action == "BUY" else "",
        selected_reason=legacy_decision.reason,
        allow_trade=runtime.last_allow_trade,
        block_reason=runtime.last_decision_block_reason,
        buy_fill=buy_fill,
    )
    runtime.last_sell_debug_reason = _finalize_sell_debug_reason(
        base_reason=base_sell_debug_reason,
        action=legacy_decision.action,
        sell_reason=sell_reason if legacy_decision.action == "SELL" else "",
        selected_reason=legacy_decision.reason,
        allow_trade=runtime.last_allow_trade,
        block_reason=runtime.last_decision_block_reason,
        sell_fill=sell_fill,
    )
    equity_row_kwargs.update(
        {
            "state": runtime.state_context.current_state.value,
            "decision_source": runtime.last_decision_source,
            "final_action": runtime.last_final_action,
            "overridden_signals": runtime.last_overridden_signals,
            "allow_trade": runtime.last_allow_trade,
            "block_reason": runtime.last_decision_block_reason,
            "filter_values": runtime.last_filter_values,
            "trade_size_usd": max(trade_size_usd, buy_order.size_usd if buy_fill and buy_fill.filled else trade_size_usd),
            "inventory_usd": inventory_usd,
            "inventory_ratio": inventory_profile.inventory_ratio,
            "equity_usd": equity_usd,
            "pnl_usd": pnl_usd,
            "trade_count": engine.trade_count,
            "execution_price": runtime.last_execution_price,
            "reentry_state": runtime.reentry_engine.serialize_state(runtime.reentry_state),
            "state_context": runtime.state_machine.serialize(runtime.state_context),
            "time_in_state_sec": _time_in_state_seconds(runtime, cycle_index),
            "last_transition": _last_transition(runtime),
            "last_sell_price": runtime.reentry_state.last_sell_price,
            "reentry_levels": _serialize_buy_zones(runtime.reentry_state),
            "buy_zones": _serialize_buy_zones(runtime.reentry_state),
            "executed_buy_levels": json.dumps(runtime.reentry_state.executed_buy_levels, separators=(",", ":")),
            "reentry_active": runtime.reentry_state.active,
            "reentry_timeout": runtime.reentry_engine.timeout_remaining_cycles(runtime.reentry_state, cycle_index),
            "cooldown_remaining": (
                runtime.state_machine.cooldown_remaining_cycles(runtime.state_context, cycle_index)
                if runtime.enable_state_machine
                else 0
            ),
            "profit_lock_state": _serialize_profit_lock_state(runtime.profit_lock_state),
            "current_profit_pct": runtime.last_profit_pct,
            "buy_debug_reason": runtime.last_buy_debug_reason,
            "sell_debug_reason": runtime.last_sell_debug_reason,
            "last_execution_type": runtime.last_execution_type,
            "execution_analytics": runtime.last_execution_analytics,
            "last_slippage_bps": runtime.last_slippage_bps,
            "last_trade_reason": runtime.last_trade_reason,
        }
    )
    _record_opportunity_rejection(runtime, cycle_index)

    if log_progress:
        _log_cycle(runtime, cycle_index, mode, intelligence, mid, source, equity_usd, pnl_usd, spread, inventory_usd)
        _log_trade_intent(runtime, cycle_index)
        _log_execution_decision(runtime, cycle_index)
        if buy_fill and buy_fill.filled:
            log(
                f"BUY fill | {buy_fill.size_usd:.2f} USD | {runtime.last_trade_reason} | "
                f"state {runtime.state_context.current_state.value}"
            )
        if sell_fill and sell_fill.filled:
            log(
                f"SELL fill | {sell_fill.size_usd:.2f} USD | {runtime.last_trade_reason} | "
                f"state {runtime.state_context.current_state.value}"
            )

    post_cycle_limit_decision = _evaluate_runtime_limits(runtime, inventory_usd, equity_usd)
    if post_cycle_limit_decision.stop_trading:
        _notify_risk_limit_stop(
            runtime,
            cycle_index=cycle_index,
            reason=post_cycle_limit_decision.reason,
            details=post_cycle_limit_decision.details,
        )

    _append_equity_row(**equity_row_kwargs)
    if post_cycle_limit_decision.stop_trading:
        return False
    return _kill_switch_allows_continue(pnl_usd, log_progress)


def build_summary(runtime: BotRuntime) -> dict:
    final_mid = runtime.last_mid if runtime.last_mid > 0 else (runtime.prices[-1] if runtime.prices else 0.0)
    total_cycles = sum(runtime.mode_counts.values())
    final_usdc = runtime.portfolio.usdc
    final_eth = runtime.portfolio.eth
    sizing = runtime.current_sizing
    if sizing is None:
        sizing = build_sizing_snapshot(
            current_equity_usd=runtime.portfolio.total_equity_usd(final_mid) if final_mid > 0 else max(final_usdc, 0.0),
            mid_price=final_mid,
            portfolio_usdc=final_usdc,
            portfolio_eth=final_eth,
        )
    performance_summary = runtime.performance.build_summary(
        final_mid=final_mid,
        final_usdc=final_usdc,
        final_eth=final_eth,
        realized_pnl=runtime.portfolio.realized_pnl_usd,
    )
    regime = runtime.current_regime_assessment
    edge = runtime.current_edge_assessment
    gate = runtime.current_signal_gate_decision
    trade_count = performance_summary["trade_count"]

    def _ratio(count: float) -> float:
        if total_cycles <= 0:
            return 0.0
        return count / total_cycles

    mode_distribution_pct = {
        mode: _ratio(count) * 100.0
        for mode, count in runtime.mode_counts.items()
    }
    feed_state_distribution_pct = {
        state: _ratio(count) * 100.0
        for state, count in runtime.feed_state_counts.items()
    }
    state_distribution_pct = {
        state: _ratio(count) * 100.0
        for state, count in runtime.state_counts.items()
    }
    dominant_mode = max(runtime.mode_trade_notional_usd, key=runtime.mode_trade_notional_usd.get, default="NO_TRADE")
    dominant_profit_mode = max(runtime.mode_realized_pnl_usd, key=runtime.mode_realized_pnl_usd.get, default="NO_TRADE")
    avg_hold_minutes = (runtime.cumulative_hold_minutes / runtime.closed_hold_count) if runtime.closed_hold_count else 0.0
    avg_inventory_drift_pct = (
        (runtime.cumulative_inventory_drift / runtime.inventory_drift_samples) * 100.0
        if runtime.inventory_drift_samples
        else 0.0
    )
    max_inventory_drift_pct = runtime.max_inventory_drift * 100.0

    summary = {
        **performance_summary,
        "config_profile": BOT_CONFIG_PROFILE,
        "final_mid": final_mid,
        "return_pct": (
            (performance_summary["final_pnl"] / performance_summary["start_equity"]) * 100.0
        )
        if performance_summary["start_equity"]
        else 0.0,
        "buy_count": runtime.engine.buy_count,
        "sell_count": runtime.engine.sell_count,
        "fees_paid_usd": runtime.portfolio.fees_paid_usd,
        "gross_profit_usd": runtime.gross_profit_usd,
        "gross_loss_usd": runtime.gross_loss_usd,
        "inventory_min": 0.0 if runtime.inventory_min is None else runtime.inventory_min,
        "inventory_max": 0.0 if runtime.inventory_max is None else runtime.inventory_max,
        "inventory_ratio_min": 0.0 if runtime.inventory_ratio_min is None else runtime.inventory_ratio_min,
        "inventory_ratio_max": 0.0 if runtime.inventory_ratio_max is None else runtime.inventory_ratio_max,
        "total_cycles": total_cycles,
        "no_trade_ratio": _ratio(runtime.mode_counts.get("NO_TRADE", 0)),
        "mode_counts": dict(runtime.mode_counts),
        "mode_distribution_pct": mode_distribution_pct,
        "mode_trade_counts": dict(runtime.mode_trade_counts),
        "mode_buy_counts": dict(runtime.mode_buy_counts),
        "mode_sell_counts": dict(runtime.mode_sell_counts),
        "mode_trade_notional_usd": dict(runtime.mode_trade_notional_usd),
        "mode_realized_pnl_usd": dict(runtime.mode_realized_pnl_usd),
        "regime_trade_counts": dict(runtime.regime_trade_counts),
        "regime_realized_pnl_usd": dict(runtime.regime_realized_pnl_usd),
        "dominant_mode_by_notional": dominant_mode,
        "dominant_mode_by_realized_pnl": dominant_profit_mode,
        "feed_state_counts": dict(runtime.feed_state_counts),
        "feed_state_distribution_pct": feed_state_distribution_pct,
        "state_counts": dict(runtime.state_counts),
        "state_distribution_pct": state_distribution_pct,
        "final_state": runtime.state_context.current_state.value,
        "start_usdc": runtime.start_usdc,
        "start_eth": runtime.start_eth,
        "final_usdc": final_usdc,
        "final_eth": final_eth,
        "usdc_delta": final_usdc - runtime.start_usdc,
        "eth_delta": final_eth - runtime.start_eth,
        "current_drawdown_pct": runtime.current_drawdown_pct,
        "drawdown_guard_stage": runtime.drawdown_guard_stage,
        "daily_pnl_usd": runtime.daily_pnl_usd,
        "daily_trade_count": runtime.daily_trade_count,
        "avg_hold_minutes": avg_hold_minutes,
        "inventory_drift_avg_pct": avg_inventory_drift_pct,
        "inventory_drift_max_pct": max_inventory_drift_pct,
        "rejection_reason_stats": dict(runtime.rejection_reason_counts),
        "risk_stop_active": runtime.risk_stop_active,
        "risk_stop_reason": runtime.risk_stop_reason,
        "risk_stop_message": runtime.risk_stop_message,
        "market_regime": regime.market_regime if regime is not None else "",
        "active_regime": runtime.current_active_regime,
        "trend_direction": runtime.current_trend_direction,
        "activity_state": runtime.current_activity_state,
        "inactivity_fallback_active": runtime.current_inactivity_fallback_active,
        "regime_confidence": 0.0 if regime is None else regime.regime_confidence,
        "range_width_pct": 0.0 if regime is None else regime.range_width_pct,
        "net_move_pct": 0.0 if regime is None else regime.net_move_pct,
        "direction_consistency": 0.0 if regime is None else regime.direction_consistency,
        "volatility_score": 0.0 if regime is None else regime.volatility_score,
        "edge_score": 0.0 if edge is None else edge.edge_score,
        "expected_edge_usd": 0.0 if edge is None else edge.expected_edge_usd,
        "expected_edge_bps": 0.0 if edge is None else edge.expected_edge_bps,
        "gate_decision": "allow" if gate is not None and gate.allow_trade else "reject",
        "blocked_reason": gate.blocked_reason if gate is not None else runtime.last_decision_block_reason,
        "consecutive_losses": runtime.loss_streak,
        "loss_pause_remaining": runtime.loss_pause_remaining_minutes,
        "equity": performance_summary["final_equity"],
        "execution_timeframe_seconds": runtime.execution_timeframe_seconds,
        "trend_timeframe_seconds": runtime.trend_timeframe_seconds,
        "confirmation_timeframe_seconds": runtime.confirmation_timeframe_seconds,
        "requote_interval_ms": EXECUTION_REQUOTE_INTERVAL_MS,
        "stale_quote_timeout_ms": EXECUTION_STALE_QUOTE_TIMEOUT_MS,
        "trend_filter_enabled": runtime.enable_trend_timeframe_filter,
        "confirmation_filter_enabled": runtime.enable_confirmation_filter,
        "upper_tf_short_ma": runtime.current_trend_short_ma,
        "upper_tf_long_ma": runtime.current_trend_long_ma,
        "upper_tf_bias": runtime.current_trend_bias,
        "current_mode": runtime.current_market_mode or "RANGE",
        "trend_strength": (
            ((runtime.current_trend_short_ma - runtime.current_trend_long_ma) / runtime.current_trend_long_ma)
            if runtime.current_trend_long_ma > 0
            else 0.0
        ),
        "confirmation_momentum_bps": runtime.current_confirmation_momentum_bps,
        "confirmation_slowing": runtime.current_confirmation_slowing,
        "execution_candle_count": runtime.current_execution_bucket_count,
        "trend_candle_count": runtime.current_trend_bucket_count,
        "confirmation_candle_count": runtime.current_confirmation_bucket_count,
        "reference_equity_usd": sizing.reference_equity_usd,
        "trade_size_usd": sizing.trade_size_usd,
        "max_trade_size_usd": sizing.max_trade_size_usd,
        "max_position_usd": sizing.max_position_usd,
        "force_trade_size_usd": sizing.force_trade_size_usd,
        "target_base_pct": sizing.target_base_pct,
        "target_quote_pct": sizing.target_quote_pct,
        "target_base_usd": sizing.target_base_usd,
        "target_quote_usd": sizing.target_quote_usd,
        "min_notional_usd": sizing.min_notional_usd,
        "size_clamped": sizing.size_clamped,
        "clamp_reason": sizing.clamp_reason,
        "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
        "max_exposure_usd": MAX_EXPOSURE_USD,
        "maker_count": runtime.maker_count,
        "taker_count": runtime.taker_count,
        "avg_slippage_bps": (runtime.total_slippage_bps / trade_count) if trade_count else 0.0,
    }
    return summary


def log_summary(summary: dict) -> None:
    log("========================================")
    log(f"Config profile: {summary.get('config_profile', 'unknown')}")
    log(f"Final price: {summary['final_mid']:.2f}")
    log(f"Return: {summary['return_pct']:.2f}%")
    log(f"BUY count: {summary['buy_count']}")
    log(f"SELL count: {summary['sell_count']}")
    log(f"NO_TRADE ratio: {summary['no_trade_ratio']:.2%}")
    log(f"Fees paid: {summary['fees_paid_usd']:.4f}")
    log_performance_summary(summary, log)
    log(f"Inventory min: {summary['inventory_min']:.2f}")
    log(f"Inventory max: {summary['inventory_max']:.2f}")
    log(f"Inventory ratio range: {summary['inventory_ratio_min']:.2%} -> {summary['inventory_ratio_max']:.2%}")
    log(f"Final USDC: {summary['final_usdc']:.4f}")
    log(f"Final ETH: {summary['final_eth']:.8f}")
    log(f"USDC delta: {summary['usdc_delta']:.4f}")
    log(f"ETH delta: {summary['eth_delta']:.8f}")
    log(f"Maker count: {summary['maker_count']}")
    log(f"Taker count: {summary['taker_count']}")
    log(f"Avg slippage: {summary['avg_slippage_bps']:.4f} bps")
    log(f"Mode counts: {summary['mode_counts']}")
    log(f"Mode trade counts: {summary['mode_trade_counts']}")
    log(f"Mode realized pnl: {summary['mode_realized_pnl_usd']}")
    log(f"Regime trade counts: {summary['regime_trade_counts']}")
    log(f"Regime realized pnl: {summary['regime_realized_pnl_usd']}")
    log(f"Avg hold: {summary['avg_hold_minutes']:.2f} min")
    log(
        f"Inventory drift avg/max: {summary['inventory_drift_avg_pct']:.2f}% / "
        f"{summary['inventory_drift_max_pct']:.2f}%"
    )
    log(f"Rejection stats: {summary['rejection_reason_stats']}")
    log(f"Feed states: {summary['feed_state_counts']}")
