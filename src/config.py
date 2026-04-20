from __future__ import annotations

from config_env import (
    ENV_PATH,
    env_bool as _env_bool,
    env_float as _env_float,
    env_has_value as _env_has_value,
    env_int as _env_int,
    env_list as _env_list,
    env_str as _env_str,
)
from config_models import (
    ActivityTuningConfig,
    CoreConfig,
    DecisionEngineConfig,
    DexConfig,
    ExecutionConfig,
    ExecutionTuningConfig,
    ExecutionPolicyConfig,
    FeedConfig,
    IntelligenceConfig,
    InventoryTuningConfig,
    InventoryManagerConfig,
    MarketConfig,
    MevExecutionConfig,
    PortfolioConfig,
    RangeStrategyConfig,
    RegimeTuningConfig,
    ReentryConfig,
    SizingTuningConfig,
    StateMachineConfig,
    TelegramConfig,
    TradeFilterConfig,
    TrendStrategyConfig,
    TrendConfig,
    VolatilityTuningConfig,
    WalletConfig,
)
from rpc_manager import normalize_rpc_urls


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _ratio_from_pct_points(value: float) -> float:
    return max(value, 0.0) / 100.0


def _bars_from_directional_consistency(consistency: float, lookback_bars: int) -> int:
    directional_steps = max(lookback_bars - 1, 1)
    return max(int(round(max(consistency, 0.0) * directional_steps)), 1)


_BOT_CONFIG_PROFILE = (_env_str("BOT_CONFIG_PROFILE", "v8_activity").strip().lower() or "v8_activity")
_V8_ACTIVITY_PROFILE_ENABLED = _BOT_CONFIG_PROFILE in {"v8_activity", "activity_v8", "v8"}
_V5_AGGRESSIVE_PROFILE_ENABLED = _BOT_CONFIG_PROFILE in {"v5_aggressive", "aggressive_v5", "v5"}
_ADAPTIVE_MM_PROFILE = (_env_str("ADAPTIVE_MM_PROFILE", "default").strip().lower() or "default")
_ULTRA_AGGRESSIVE_PAPER_PROFILE_ENABLED = _ADAPTIVE_MM_PROFILE in {
    "ultra_aggressive_paper",
    "ultra_aggressive",
    "uap",
}

def _profile_default(baseline_default, v8_default=None, v5_default=None):
    if _V5_AGGRESSIVE_PROFILE_ENABLED and v5_default is not None:
        return v5_default
    if _V8_ACTIVITY_PROFILE_ENABLED and v8_default is not None:
        return v8_default
    return baseline_default


def _profile_float(
    name: str,
    baseline_default: float,
    v8_default: float | None = None,
    v5_default: float | None = None,
) -> float:
    return _env_float(name, _profile_default(baseline_default, v8_default, v5_default))


def _profile_int(
    name: str,
    baseline_default: int,
    v8_default: int | None = None,
    v5_default: int | None = None,
) -> int:
    return _env_int(name, _profile_default(baseline_default, v8_default, v5_default))


def _profile_bool(
    name: str,
    baseline_default: bool,
    v8_default: bool | None = None,
    v5_default: bool | None = None,
) -> bool:
    return _env_bool(name, _profile_default(baseline_default, v8_default, v5_default))


def _adaptive_profile_default(default, ultra_default=None):
    if _ULTRA_AGGRESSIVE_PAPER_PROFILE_ENABLED and ultra_default is not None:
        return ultra_default
    return default


def _adaptive_profile_float(name: str, default: float, ultra_default: float | None = None) -> float:
    return _env_float(name, _adaptive_profile_default(default, ultra_default))


def _adaptive_profile_int(name: str, default: int, ultra_default: int | None = None) -> int:
    return _env_int(name, _adaptive_profile_default(default, ultra_default))


def _normalized_drawdown_ratio(value: float) -> float:
    raw_value = abs(float(value))
    return raw_value / 100.0 if raw_value > 1.0 else raw_value


def _env_drawdown_ratio(primary_name: str, legacy_name: str, default_ratio: float) -> float:
    if _env_has_value(primary_name):
        return _normalized_drawdown_ratio(_env_float(primary_name, default_ratio * 100.0))
    return _normalized_drawdown_ratio(_env_float(legacy_name, default_ratio))


CORE = CoreConfig(
    bot_mode=_env_str("BOT_MODE", "paper"),
    chain=_env_str("CHAIN", "base"),
    rpc_url=_env_str("RPC_URL", ""),
    rpc_urls=normalize_rpc_urls(_env_str("RPC_URL", ""), _env_list("RPC_URLS")),
    rpc_timeout_sec=_env_float("RPC_TIMEOUT_SEC", 10.0),
    rpc_max_retries=_env_int("RPC_MAX_RETRIES", 3),
    rpc_retry_backoff_sec=_env_float("RPC_RETRY_BACKOFF_SEC", 2.0),
)

WALLET = WalletConfig(
    wallet_private_key=_env_str("WALLET_PRIVATE_KEY", ""),
    wallet_address=_env_str("WALLET_ADDRESS", ""),
)

TELEGRAM = TelegramConfig(
    enabled=_env_bool("TELEGRAM_ENABLED", False),
    bot_token=_env_str("TELEGRAM_BOT_TOKEN", ""),
    chat_id=_env_str("TELEGRAM_CHAT_ID", ""),
    poll_commands=_env_bool("TELEGRAM_POLL_COMMANDS", True),
    daily_report_enabled=_env_bool("TELEGRAM_DAILY_REPORT_ENABLED", True),
    daily_report_hour=max(min(_env_int("TELEGRAM_DAILY_REPORT_HOUR", 20), 23), 0),
    api_timeout_sec=_env_float("TELEGRAM_API_TIMEOUT_SEC", 10.0),
    api_max_retries=_env_int("TELEGRAM_API_MAX_RETRIES", 3),
    rate_limit_seconds=max(_env_float("TELEGRAM_RATE_LIMIT_SECONDS", 1.0), 0.0),
)

PORTFOLIO = PortfolioConfig(
    start_usdc=_env_float("START_USDC", 1000.0),
    start_eth=_env_float("START_ETH", 0.2),
    start_eth_usd=_env_float("START_ETH_USD", 0.0),
    max_inventory_usd=_env_float("MAX_INVENTORY_USD", 140.0),
    min_order_size_usd=_env_float("MIN_ORDER_SIZE_USD", 10.0),
    inventory_skew_strength=_env_float(
        "INVENTORY_SKEW_STRENGTH",
        _env_float("SKEW_STRENGTH", _profile_default(0.02, 0.02, 0.08)),
    ),
)

EXECUTION = ExecutionConfig(
    trade_size_usd=_env_float(
        "TRADE_SIZE_USD",
        _env_float("BASE_SIZE_USD", _profile_default(30.0, 25.0, 22.0)),
    ),
    max_trade_size_usd=max(_env_float("MAX_TRADE_SIZE_USD", 75.0), 0.0),
    account_reference_mode=_env_str("ACCOUNT_REFERENCE_MODE", "dynamic"),
    trade_size_pct=max(_env_float("TRADE_SIZE_PCT", 0.10), 0.0),
    max_position_pct=max(_env_float("MAX_POSITION_PCT", 0.25), 0.0),
    max_trade_size_pct=max(_env_float("MAX_TRADE_SIZE_PCT", 0.15), 0.0),
    force_trade_size_pct=max(_env_float("FORCE_TRADE_SIZE_PCT", 0.03), 0.0),
    target_base_pct=max(_env_float("TARGET_BASE_PCT", 0.50), 0.0),
    target_quote_pct=max(_env_float("TARGET_QUOTE_PCT", 0.50), 0.0),
    min_notional_usd=max(_env_float("MIN_NOTIONAL_USD", _env_float("MIN_ORDER_SIZE_USD", 10.0)), 0.0),
    min_base_reserve_pct=max(_env_float("MIN_BASE_RESERVE_PCT", 0.05), 0.0),
    min_quote_reserve_pct=max(_env_float("MIN_QUOTE_RESERVE_PCT", 0.05), 0.0),
    account_size_override=max(_env_float("ACCOUNT_SIZE_OVERRIDE", 0.0), 0.0),
    max_daily_loss_usd=max(_env_float("MAX_DAILY_LOSS_USD", 25.0), 0.0),
    max_exposure_usd=max(_env_float("MAX_EXPOSURE_USD", PORTFOLIO.max_inventory_usd), 0.0),
    loop_seconds=_env_float("LOOP_SECONDS", 6.0),
    max_loops=_env_int("MAX_LOOPS", 0),
    maker_fee_bps=_env_float("MAKER_FEE_BPS", 2.0),
    taker_fee_bps=_env_float("TAKER_FEE_BPS", 5.0),
    kill_switch_usd=_env_float("KILL_SWITCH_USD", -10.0),
    trades_csv=_env_str("TRADES_CSV", r"logs\trades.csv"),
    equity_csv=_env_str("EQUITY_CSV", r"logs\equity.csv"),
    price_cache_seconds=_env_float("PRICE_CACHE_SECONDS", 10.0),
    sqlite_log_path=_env_str("SQLITE_LOG_PATH", r"logs\trading.sqlite"),
)

MARKET = MarketConfig(
    spread_bps=_env_float(
        "SPREAD_BPS",
        _env_float("BASE_SPREAD_BPS", _profile_default(9.0, 9.0, 14.0)),
    ),
    min_spread_bps=_profile_float("MIN_SPREAD_BPS", 3.0, 3.0, 8.0),
    max_spread_bps=_profile_float("MAX_SPREAD_BPS", 18.0, 18.0, 32.0),
    twap_window=_env_int("TWAP_WINDOW", 5),
    vol_window=_env_int("VOL_WINDOW", 10),
    vol_multiplier=_env_float("VOL_MULTIPLIER", 0.55),
    short_ma_window=_env_int("SHORT_MA_WINDOW", 9),
    long_ma_window=_env_int("LONG_MA_WINDOW", 21),
    execution_timeframe_seconds=_env_float("EXECUTION_TIMEFRAME_SECONDS", 300.0),
    trend_filter_timeframe_seconds=_env_float("TREND_FILTER_TIMEFRAME_SECONDS", 900.0),
    enable_trend_timeframe_filter=_env_bool("ENABLE_TREND_TIMEFRAME_FILTER", True),
    enable_execution_confirmation=_env_bool("ENABLE_EXECUTION_CONFIRMATION", False),
    confirmation_timeframe_seconds=_env_float("CONFIRMATION_TIMEFRAME_SECONDS", 60.0),
    confirmation_momentum_shock_bps=_env_float("CONFIRMATION_MOMENTUM_SHOCK_BPS", 18.0),
    price_bootstrap_rows=_env_int(
        "PRICE_BOOTSTRAP_ROWS",
        max(_env_int("LONG_MA_WINDOW", 21), _env_int("VOL_WINDOW", 10) + 1),
    ),
    price_history_max_age_seconds=_env_float("PRICE_HISTORY_MAX_AGE_SECONDS", 1800.0),
    trend_threshold=_env_float("TREND_THRESHOLD", 0.00012),
    high_vol_threshold=_env_float("HIGH_VOL_THRESHOLD", 0.002),
)

TREND = TrendConfig(
    trend_size_multiplier=_env_float("TREND_SIZE_MULTIPLIER", 1.15),
    range_size_multiplier=_env_float("RANGE_SIZE_MULTIPLIER", 1.0),
    trend_buy_target_pct=_env_float("TREND_BUY_TARGET_PCT", 0.80),
    max_trend_chase_bps=_env_float("MAX_TREND_CHASE_BPS", 5.0),
    max_trend_pullback_bps=_env_float("MAX_TREND_PULLBACK_BPS", 12.0),
    trend_buy_min_market_score=_env_float("TREND_BUY_MIN_MARKET_SCORE", 0.05),
    trend_buy_min_signal_score=_env_float("TREND_BUY_MIN_SIGNAL_SCORE", 0.0),
    trend_buy_min_confidence=_env_float("TREND_BUY_MIN_CONFIDENCE", 0.0),
    trend_buy_min_long_buffer_bps=_env_float("TREND_BUY_MIN_LONG_BUFFER_BPS", 0.0),
    trend_buy_min_strength_multiplier=_env_float("TREND_BUY_MIN_STRENGTH_MULTIPLIER", 1.0),
    trend_buy_requote_bps=_env_float("TREND_BUY_REQUOTE_BPS", 2.0),
    range_spread_tightening=_env_float("RANGE_SPREAD_TIGHTENING", 0.90),
    range_directional_bias_factor=_env_float("RANGE_DIRECTIONAL_BIAS_FACTOR", 0.45),
    trend_directional_bias_factor=_env_float("TREND_DIRECTIONAL_BIAS_FACTOR", 0.90),
    range_target_inventory_min=_env_float("RANGE_TARGET_INVENTORY_MIN", 0.48),
    range_target_inventory_max=_env_float("RANGE_TARGET_INVENTORY_MAX", 0.55),
    trend_target_inventory_min=_env_float("TREND_TARGET_INVENTORY_MIN", 0.55),
    trend_target_inventory_max=_env_float("TREND_TARGET_INVENTORY_MAX", 0.68),
    risk_off_target_inventory_max=_env_float("RISK_OFF_TARGET_INVENTORY_MAX", 0.30),
    caution_target_inventory_cap=_env_float("CAUTION_TARGET_INVENTORY_CAP", 0.56),
    side_flip_cooldown_cycles=_env_int("SIDE_FLIP_COOLDOWN_CYCLES", 1),
    side_flip_min_bps=_env_float("SIDE_FLIP_MIN_BPS", 6.0),
    trend_sell_spread_factor=_env_float("TREND_SELL_SPREAD_FACTOR", 1.5),
    trend_buy_fill_bonus=_env_float("TREND_BUY_FILL_BONUS", 0.06),
    trend_sell_fill_bonus=_env_float("TREND_SELL_FILL_BONUS", 0.04),
    trend_overweight_unwind_fraction=_env_float("TREND_OVERWEIGHT_UNWIND_FRACTION", 0.20),
    trend_overweight_max_multiplier=_env_float("TREND_OVERWEIGHT_MAX_MULTIPLIER", 2.75),
    min_sell_profit_bps=_env_float("MIN_SELL_PROFIT_BPS", 50.0),
    max_exit_premium_bps=_env_float("MAX_EXIT_PREMIUM_BPS", 12.0),
)

REENTRY = ReentryConfig(
    enabled=_env_bool("REENTRY_ENGINE_ENABLED", True),
    inventory_buffer_pct=_env_float("REENTRY_INVENTORY_BUFFER_PCT", 0.10),
    wait_reentry_pullback_pct=_env_float("WAIT_REENTRY_PULLBACK_PCT", 0.30),
    zone_1_multiplier=_env_float("REENTRY_ZONE_1_MULTIPLIER", 0.995),
    zone_2_multiplier=_env_float("REENTRY_ZONE_2_MULTIPLIER", 0.99),
    zone_3_multiplier=_env_float("REENTRY_ZONE_3_MULTIPLIER", 0.982),
    zone_1_buy_fraction=_env_float("REENTRY_ZONE_1_BUY_FRACTION", 0.30),
    zone_2_buy_fraction=_env_float("REENTRY_ZONE_2_BUY_FRACTION", 0.30),
    zone_3_buy_fraction=_env_float("REENTRY_ZONE_3_BUY_FRACTION", 0.40),
    timeout_minutes=_env_float("REENTRY_TIMEOUT_MINUTES", 15.0),
    timeout_buy_fraction=_env_float("REENTRY_TIMEOUT_BUY_FRACTION", 0.35),
    runaway_buy_fraction=_env_float("REENTRY_RUNAWAY_BUY_FRACTION", 0.30),
    max_miss_reentry_pct=_env_float("REENTRY_MAX_MISS_PCT", 0.025),
    max_miss_buy_fraction=_env_float("REENTRY_MAX_MISS_BUY_FRACTION", 0.25),
    rsi_period=_env_int("REENTRY_RSI_PERIOD", 14),
    rsi_buy_threshold=_env_float("REENTRY_RSI_BUY_THRESHOLD", 40.0),
    rsi_turn_margin=_env_float("REENTRY_RSI_TURN_MARGIN", 1.0),
    momentum_lookback=_env_int("REENTRY_MOMENTUM_LOOKBACK", 4),
    stop_loss_pct=_env_float("STOP_LOSS_PCT", -1.20),
    profit_lock_level_1_bps=_env_float("PROFIT_LOCK_LEVEL_1_BPS", 80.0),
    range_profit_lock_level_1_bps=_env_float("RANGE_PROFIT_LOCK_LEVEL_1_BPS", 40.0),
    profit_lock_level_1_sell_fraction=_env_float("PROFIT_LOCK_LEVEL_1_SELL_FRACTION", 0.40),
    profit_lock_level_2_bps=_env_float("PROFIT_LOCK_LEVEL_2_BPS", 150.0),
    range_profit_lock_level_2_bps=_env_float("RANGE_PROFIT_LOCK_LEVEL_2_BPS", 80.0),
    profit_lock_level_2_sell_fraction=_env_float("PROFIT_LOCK_LEVEL_2_SELL_FRACTION", 0.50),
    micro_trailing_pullback_bps=_env_float("MICRO_TRAILING_PULLBACK_BPS", 10.0),
    eth_accumulation_reinvest_pct=_env_float("ETH_ACCUMULATION_REINVEST_PCT", 0.35),
    eth_preservation_floor_multiplier=_env_float("ETH_PRESERVATION_FLOOR_MULTIPLIER", 0.85),
    partial_reset_usdc_threshold_pct=_env_float("PARTIAL_RESET_USDC_THRESHOLD_PCT", 0.68),
    partial_reset_buy_fraction=_env_float("PARTIAL_RESET_BUY_FRACTION", 0.30),
)

EXECUTION_POLICY = ExecutionPolicyConfig(
    enabled=_env_bool("EXECUTION_ENGINE_ENABLED", True),
    min_expected_profit_pct=_env_float("EXECUTION_MIN_EXPECTED_PROFIT_PCT", 0.40),
    maker_slippage_bps=_env_float("EXECUTION_MAKER_SLIPPAGE_BPS", 0.5),
    taker_slippage_bps=_env_float("EXECUTION_TAKER_SLIPPAGE_BPS", 4.0),
    slippage_size_factor=_env_float("EXECUTION_SLIPPAGE_SIZE_FACTOR", 1.25),
)

MEV_EXECUTION = MevExecutionConfig(
    enable_private_tx=_env_bool("ENABLE_PRIVATE_TX", True),
    private_rpc_url=_env_str("PRIVATE_RPC_URL", ""),
    private_rpc_urls=normalize_rpc_urls(_env_str("PRIVATE_RPC_URL", ""), _env_list("PRIVATE_RPC_URLS")),
    private_tx_timeout_sec=_env_float("PRIVATE_TX_TIMEOUT_SEC", 8.0),
    private_tx_max_retries=_env_int("PRIVATE_TX_MAX_RETRIES", 2),
    enable_cow=_env_bool("ENABLE_COW", False),
    cow_min_notional_usd=_env_float("COW_MIN_NOTIONAL_USD", 150.0),
    cow_supported_pairs=_env_list("COW_SUPPORTED_PAIRS", "WETH/USDC"),
    enable_order_slicing=_env_bool("ENABLE_ORDER_SLICING", False),
    max_single_swap_usd=_env_float("MAX_SINGLE_SWAP_USD", 125.0),
    slice_count_max=_env_int("SLICE_COUNT_MAX", 4),
    slice_delay_ms=_env_int("SLICE_DELAY_MS", 250),
    max_quote_deviation_bps=_env_float("MAX_QUOTE_DEVIATION_BPS", 35.0),
    max_twap_deviation_bps=_env_float("MAX_TWAP_DEVIATION_BPS", 55.0),
    max_price_impact_bps=_env_float("MAX_PRICE_IMPACT_BPS", 45.0),
    max_slippage_bps=_env_float("MAX_SLIPPAGE_BPS", 40.0),
    max_gas_spike_gwei=_env_float("MAX_GAS_SPIKE_GWEI", 35.0),
    estimated_swap_gas_units=_env_int("ESTIMATED_SWAP_GAS_UNITS", 5000),
    max_gas_to_profit_ratio=_env_float("MAX_GAS_TO_PROFIT_RATIO", 1.0),
    mev_risk_threshold_block=_env_float("MEV_RISK_THRESHOLD_BLOCK", 70.0),
    public_swap_max_risk=_env_float("PUBLIC_SWAP_MAX_RISK", 40.0),
    execution_policy_profile=_env_str("EXECUTION_POLICY_PROFILE", "balanced"),
    mev_policy_path=_env_str("MEV_POLICY_PATH", "mev_policy.yaml"),
)

TRADE_FILTER = TradeFilterConfig(
    enabled=_env_bool("TRADE_FILTER_ENABLED", True),
    momentum_limit_low_vol_bps=_env_float("TRADE_FILTER_MOMENTUM_LIMIT_LOW_VOL_BPS", 60.0),
    momentum_limit_mid_vol_bps=_env_float("TRADE_FILTER_MOMENTUM_LIMIT_MID_VOL_BPS", 100.0),
    momentum_limit_high_vol_bps=_env_float("TRADE_FILTER_MOMENTUM_LIMIT_HIGH_VOL_BPS", 180.0),
    buy_rsi_max=_env_float("TRADE_FILTER_BUY_RSI_MAX", 70.0),
    sell_rsi_min=_env_float("TRADE_FILTER_SELL_RSI_MIN", 30.0),
    loss_streak_limit=_env_int("TRADE_FILTER_LOSS_STREAK_LIMIT", 2),
    min_trade_distance_pct=_env_float("MIN_TRADE_DISTANCE_PCT", 0.12),
    cooldown_minutes=_env_float("TRADE_COOLDOWN_MINUTES", 2.0),
    min_time_between_trades_minutes=_env_float("MIN_TIME_BETWEEN_TRADES_MINUTES", 3.0),
    max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 40),
    trend_against_score=_env_float("TRADE_FILTER_TREND_AGAINST_SCORE", 0.26),
    strong_trend_score=_env_float("TRADE_FILTER_STRONG_TREND_SCORE", 0.42),
    strong_trend_skip_score=_env_float("TRADE_FILTER_STRONG_TREND_SKIP_SCORE", 0.68),
    strong_trend_size_multiplier=_env_float("TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER", 0.55),
    force_trade_minutes=_env_float("TRADE_FILTER_FORCE_TRADE_MINUTES", 30.0),
    force_trade_size_fraction=_env_float("FORCE_TRADE_SIZE_FRACTION", 0.25),
    min_trades_per_hour=_env_float(
        "MIN_TRADES_PER_HOUR",
        _env_float("MIN_TRADE_RATE_PER_HOUR", _profile_default(5.0, 5.0, 10.0)),
    ),
    debug_mode=_env_bool("TRADE_FILTER_DEBUG_MODE", True),
)

INVENTORY_MANAGER = InventoryManagerConfig(
    enabled=_env_bool("INVENTORY_MANAGER_ENABLED", True),
    normal_min=_env_float("INVENTORY_NORMAL_MIN", 0.35),
    normal_max=_env_float("INVENTORY_NORMAL_MAX", 0.65),
    uptrend_min=_env_float("INVENTORY_UPTREND_MIN", 0.45),
    uptrend_max=_env_float("INVENTORY_UPTREND_MAX", 0.75),
    downtrend_min=_env_float("INVENTORY_DOWNTREND_MIN", 0.20),
    downtrend_max=_env_float("INVENTORY_DOWNTREND_MAX", 0.55),
)

STATE_MACHINE = StateMachineConfig(
    enabled=_env_bool("STATE_MACHINE_ENABLED", True),
    loss_streak_limit=_env_int("STATE_MACHINE_LOSS_STREAK_LIMIT", 8),
    cooldown_minutes=_env_float("STATE_MACHINE_COOLDOWN_MINUTES", 30.0),
    max_cooldown_minutes=_env_float("STATE_MACHINE_MAX_COOLDOWN_MINUTES", 2.0),
    accumulating_failsafe_minutes=_env_float("STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES", 30.0),
)

DECISION_ENGINE = DecisionEngineConfig(
    enabled=_env_bool("DECISION_ENGINE_ENABLED", True),
)

DEX = DexConfig(
    uniswap_v3_factory=_env_str("UNISWAP_V3_FACTORY", ""),
    base_weth=_env_str("BASE_WETH", ""),
    base_usdc=_env_str("BASE_USDC", ""),
    uniswap_pool_fee=_env_int("UNISWAP_POOL_FEE", 500),
)

INTELLIGENCE = IntelligenceConfig(
    signal_cache_seconds=_env_float("SIGNAL_CACHE_SECONDS", 300.0),
    signal_http_timeout_seconds=_env_float("SIGNAL_HTTP_TIMEOUT_SECONDS", 6.0),
    signal_fetch_enabled=_env_bool("SIGNAL_FETCH_ENABLED", True),
    signal_caution_threshold=_env_float("SIGNAL_CAUTION_THRESHOLD", -0.22),
    signal_block_threshold=_env_float("SIGNAL_BLOCK_THRESHOLD", -0.55),
    signal_block_risk_threshold=_env_float("SIGNAL_BLOCK_RISK_THRESHOLD", 0.72),
    caution_spread_multiplier=_env_float("CAUTION_SPREAD_MULTIPLIER", 1.12),
    caution_size_multiplier=_env_float("CAUTION_SIZE_MULTIPLIER", 0.78),
    intelligence_warmup_rows=_env_int(
        "INTELLIGENCE_WARMUP_ROWS",
        max(MARKET.long_ma_window, MARKET.vol_window + 5),
    ),
    low_vol_threshold_multiplier=_env_float("LOW_VOL_THRESHOLD_MULTIPLIER", 0.65),
    extreme_vol_threshold_multiplier=_env_float("EXTREME_VOL_THRESHOLD_MULTIPLIER", 1.85),
    risk_off_trend_multiplier=_env_float("RISK_OFF_TREND_MULTIPLIER", 1.10),
    overweight_exit_buffer_pct=_env_float("OVERWEIGHT_EXIT_BUFFER_PCT", 0.06),
    capital_preservation_drawdown_pct=_env_drawdown_ratio(
        "SOFT_DD_PCT",
        "CAPITAL_PRESERVATION_DRAWDOWN_PCT",
        _profile_default(0.03, 0.03, 0.015),
    ),
    risk_block_drawdown_pct=_env_drawdown_ratio(
        "MEDIUM_DD_PCT",
        "RISK_BLOCK_DRAWDOWN_PCT",
        _profile_default(0.05, 0.05, 0.025),
    ),
    drawdown_pause_pct=_env_drawdown_ratio(
        "HARD_DD_PCT",
        "DRAWDOWN_PAUSE_PCT",
        _profile_default(0.08, 0.08, 0.035),
    ),
    adaptive_lookback_cycles=_env_int("ADAPTIVE_LOOKBACK_CYCLES", 18),
    min_inventory_multiplier=_env_float("MIN_INVENTORY_MULTIPLIER", 0.20),
    max_inventory_multiplier=_env_float("MAX_INVENTORY_MULTIPLIER", 1.35),
    min_trade_size_multiplier=_env_float("MIN_TRADE_SIZE_MULTIPLIER", 0.0),
    max_trade_size_multiplier=_env_float("MAX_TRADE_SIZE_MULTIPLIER", 1.85),
    min_spread_multiplier=_env_float("MIN_SPREAD_MULTIPLIER", 0.85),
    max_spread_multiplier=_env_float("MAX_SPREAD_MULTIPLIER", 2.40),
)

FEEDS = FeedConfig(
    news_rss_urls=_env_list("NEWS_RSS_URLS"),
    news_lookback_hours=_env_float("NEWS_LOOKBACK_HOURS", 10.0),
    news_max_items=_env_int("NEWS_MAX_ITEMS", 12),
    news_positive_keywords=_env_list(
        "NEWS_POSITIVE_KEYWORDS",
        "etf inflow,approval,partnership,adoption,upgrade,accumulation,breakout,record demand,"
        "buyback,expansion,whale buy,stablecoin mint",
    ),
    news_negative_keywords=_env_list(
        "NEWS_NEGATIVE_KEYWORDS",
        "hack,exploit,liquidation,lawsuit,ban,delay,rejection,security issue,sell-off,"
        "outage,default,bankruptcy,drain,whale sell",
    ),
    macro_rss_urls=_env_list("MACRO_RSS_URLS"),
    macro_lookback_hours=_env_float("MACRO_LOOKBACK_HOURS", 24.0),
    macro_block_minutes=_env_float("MACRO_BLOCK_MINUTES", 90.0),
    macro_risk_keywords=_env_list(
        "MACRO_RISK_KEYWORDS",
        "fomc,cpi,pce,nfp,jobless,powell,rate decision,treasury auction,inflation,gdp",
    ),
    macro_supportive_keywords=_env_list(
        "MACRO_SUPPORTIVE_KEYWORDS",
        "rate cut,dovish,soft landing,disinflation,liquidity easing,qe",
    ),
    onchain_rss_urls=_env_list("ONCHAIN_RSS_URLS"),
    onchain_lookback_hours=_env_float("ONCHAIN_LOOKBACK_HOURS", 18.0),
    onchain_bullish_keywords=_env_list(
        "ONCHAIN_BULLISH_KEYWORDS",
        "exchange outflow,accumulation,whale buy,staking,mint,treasury buy,deposit to cold wallet,"
        "bridge inflow,fundraise,burn",
    ),
    onchain_bearish_keywords=_env_list(
        "ONCHAIN_BEARISH_KEYWORDS",
        "exchange inflow,unlock,exploit,hack,liquidation,whale sell,bridge outflow,drain,"
        "deposit to exchange,security incident",
    ),
    onchain_stress_keywords=_env_list(
        "ONCHAIN_STRESS_KEYWORDS",
        "hack,exploit,bridge outflow,exchange inflow,liquidation,unlock",
    ),
)

_legacy_target_base_pct = _env_float("TARGET_BASE_PCT", 0.50)
_legacy_high_vol_threshold_pct = max(_env_float("HIGH_VOL_THRESHOLD", 0.0055) * 100.0, 0.01)
_legacy_low_vol_threshold_pct = max(
    _legacy_high_vol_threshold_pct * max(_env_float("LOW_VOL_THRESHOLD_MULTIPLIER", 0.65), 0.05),
    0.01,
)
_inventory_target_weight_default = _env_float(
    "INVENTORY_TARGET_ETH_WEIGHT",
    _profile_default(_legacy_target_base_pct, _legacy_target_base_pct, 0.50),
)
_inventory_band_low_default = _env_float(
    "INVENTORY_BAND_LOW",
    _profile_default(max(_inventory_target_weight_default - 0.08, 0.0), max(_inventory_target_weight_default - 0.08, 0.0), 0.45),
)
_inventory_band_high_default = _env_float(
    "INVENTORY_BAND_HIGH",
    _profile_default(min(_inventory_target_weight_default + 0.08, 1.0), min(_inventory_target_weight_default + 0.08, 1.0), 0.55),
)
_inventory_neutral_band_pct_default = max(
    max(abs(_inventory_target_weight_default - _inventory_band_low_default), abs(_inventory_band_high_default - _inventory_target_weight_default))
    * 100.0,
    0.0,
)

REGIME_TUNING = RegimeTuningConfig(
    default_regime=(_env_str("REGIME_DEFAULT_REGIME", "RANGE").strip().upper() or "RANGE"),
    trend_override_only_if_strong=_env_bool("REGIME_TREND_OVERRIDE_ONLY_IF_STRONG", True),
    range_priority_when_unclear=_env_bool("REGIME_RANGE_PRIORITY_WHEN_UNCLEAR", True),
    trend_lookback_bars=max(
        _env_int("REGIME_TREND_LOOKBACK_BARS", _env_int("REGIME_LOOKBACK_CANDLES", 12)),
        3,
    ),
    trend_min_net_move_pct=_env_float(
        "REGIME_TREND_MIN_NET_MOVE_PCT",
        _env_float("REGIME_TREND_NET_MOVE_PCT", 0.45),
    ),
    trend_min_same_direction_bars=max(
        _env_int(
            "REGIME_TREND_MIN_SAME_DIRECTION_BARS",
            _bars_from_directional_consistency(
                _env_float("REGIME_MIN_DIRECTIONAL_CONSISTENCY", 0.60),
                _env_int("REGIME_TREND_LOOKBACK_BARS", _env_int("REGIME_LOOKBACK_CANDLES", 12)),
            ),
        ),
        2,
    ),
    trend_min_distance_from_vwap_pct=_env_float("REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT", 0.18),
    trend_momentum_zscore_min=_env_float("REGIME_TREND_MOMENTUM_ZSCORE_MIN", 1.2),
    range_lookback_bars=max(
        _env_int("REGIME_RANGE_LOOKBACK_BARS", _env_int("REGIME_LOOKBACK_CANDLES", 12)),
        3,
    ),
    range_max_net_move_pct=_env_float("REGIME_RANGE_MAX_NET_MOVE_PCT", 0.60),
    range_max_distance_from_vwap_pct=_env_float("REGIME_RANGE_MAX_DISTANCE_FROM_VWAP_PCT", 0.22),
    range_structure_required=_env_bool("REGIME_RANGE_STRUCTURE_REQUIRED", True),
    range_structure_tolerance_pct=_env_float("REGIME_RANGE_STRUCTURE_TOLERANCE_PCT", 0.12),
    shock_bar_move_pct=_env_float("REGIME_SHOCK_BAR_MOVE_PCT", 0.90),
    max_wick_to_body_ratio=_env_float("REGIME_MAX_WICK_TO_BODY_RATIO", 2.8),
    max_spread_multiplier_vs_median=_env_float("REGIME_MAX_SPREAD_MULTIPLIER_VS_MEDIAN", 2.2),
    post_shock_cooldown_bars=max(_env_int("REGIME_POST_SHOCK_COOLDOWN_BARS", 3), 0),
)

RANGE_STRATEGY = RangeStrategyConfig(
    range_window_bars=max(
        _env_int("RANGE_WINDOW_BARS", _env_int("REGIME_RANGE_LOOKBACK_BARS", _env_int("REGIME_LOOKBACK_CANDLES", 12))),
        3,
    ),
    range_top_zone_pct=_env_float("RANGE_TOP_ZONE_PCT", 0.25),
    range_bottom_zone_pct=_env_float(
        "RANGE_BOTTOM_ZONE_PCT",
        _env_float("RANGE_ENTRY_MAX_POSITION_PCT", 0.25),
    ),
    range_mid_no_trade_zone_pct=_profile_float("RANGE_MID_NO_TRADE_ZONE_PCT", 0.20, 0.15),
    entry_threshold_bps=_profile_float("RANGE_ENTRY_THRESHOLD_BPS", 5.0, 4.5),
    min_edge_bps=_env_float("RANGE_MIN_EDGE_BPS", _env_float("EXPECTED_EDGE_MIN_BPS", -5.0)),
    take_profit_bps=_env_float("RANGE_TAKE_PROFIT_BPS", _env_float("RANGE_PROFIT_LOCK_LEVEL_2_BPS", 13.0)),
    soft_take_profit_bps=_env_float("RANGE_SOFT_TAKE_PROFIT_BPS", _env_float("RANGE_PROFIT_LOCK_LEVEL_1_BPS", 8.0)),
    max_hold_minutes=_env_float("RANGE_MAX_HOLD_MINUTES", _env_float("MAX_POSITION_HOLD_MINUTES", 28.0)),
    time_stop_minutes=_env_float("RANGE_TIME_STOP_MINUTES", 18.0),
    exit_on_reversion_to_mid=_env_bool("RANGE_EXIT_ON_REVERSION_TO_MID", True),
)

TREND_STRATEGY = TrendStrategyConfig(
    entry_threshold_bps=_env_float("TREND_ENTRY_THRESHOLD_BPS", 11.0),
    min_edge_bps=_env_float("TREND_MIN_EDGE_BPS", -5.0),
    take_profit_bps=_env_float("TREND_TAKE_PROFIT_BPS", _env_float("PROFIT_LOCK_LEVEL_2_BPS", 14.0)),
    soft_take_profit_bps=_env_float("TREND_SOFT_TAKE_PROFIT_BPS", _env_float("PROFIT_LOCK_LEVEL_1_BPS", 9.0)),
    max_hold_minutes=_env_float("TREND_MAX_HOLD_MINUTES", _env_float("MAX_POSITION_HOLD_MINUTES", 24.0)),
    time_stop_minutes=_env_float("TREND_TIME_STOP_MINUTES", 16.0),
)

EXECUTION_TUNING = ExecutionTuningConfig(
    base_entry_threshold_bps=_env_float("EXECUTION_BASE_ENTRY_THRESHOLD_BPS", 10.0),
    requote_interval_ms=max(_profile_int("EXECUTION_REQUOTE_INTERVAL_MS", 2500, 1500), 0),
    stale_quote_timeout_ms=max(_profile_int("EXECUTION_STALE_QUOTE_TIMEOUT_MS", 4000, 2500), 0),
    cancel_if_far_from_mid_bps=_env_float("EXECUTION_CANCEL_IF_FAR_FROM_MID_BPS", 8.0),
    reprice_on_mid_move_bps=_env_float("EXECUTION_REPRICE_ON_MID_MOVE_BPS", _env_float("TREND_BUY_REQUOTE_BPS", 4.0)),
    max_position_hold_minutes=_env_float("EXECUTION_MAX_POSITION_HOLD_MINUTES", _env_float("MAX_POSITION_HOLD_MINUTES", 25.0)),
    force_exit_if_unrealized_loss_bps=_env_float(
        "EXECUTION_FORCE_EXIT_IF_UNREALIZED_LOSS_BPS",
        _env_float("STOP_LOSS_PCT", -1.20) * 100.0,
    ),
    force_exit_if_regime_flip=_env_bool("EXECUTION_FORCE_EXIT_IF_REGIME_FLIP", True),
    force_exit_if_inventory_pressure=_env_bool("EXECUTION_FORCE_EXIT_IF_INVENTORY_PRESSURE", True),
)

INVENTORY_TUNING = InventoryTuningConfig(
    inventory_target_pct=_env_float(
        "INVENTORY_TARGET_PCT",
        _inventory_target_weight_default * 100.0,
    ),
    inventory_neutral_band_pct=_env_float(
        "INVENTORY_NEUTRAL_BAND_PCT",
        _inventory_neutral_band_pct_default,
    ),
    inventory_soft_limit_pct=_env_float("INVENTORY_SOFT_LIMIT_PCT_POINTS", 25.0),
    inventory_hard_limit_pct=_env_float("INVENTORY_HARD_LIMIT_PCT_POINTS", 40.0),
    inventory_force_reduce_pct=_env_float("INVENTORY_FORCE_REDUCE_PCT", 50.0),
    same_side_entry_penalty_bps=_env_float("INVENTORY_SAME_SIDE_ENTRY_PENALTY_BPS", 5.0),
    opposite_side_entry_bonus_bps=_env_float("INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS", 3.0),
    block_same_side_entries_above_hard_limit=_env_bool(
        "INVENTORY_BLOCK_SAME_SIDE_ENTRIES_ABOVE_HARD_LIMIT",
        True,
    ),
    allow_reduce_only_above_hard_limit=_env_bool("INVENTORY_ALLOW_REDUCE_ONLY_ABOVE_HARD_LIMIT", True),
    force_inventory_reduction_above_pct=_env_float("INVENTORY_FORCE_INVENTORY_REDUCTION_ABOVE_PCT", 50.0),
    forced_reduce_aggression_bps=_env_float("INVENTORY_FORCED_REDUCE_AGGRESSION_BPS", 6.0),
)

SIZING_TUNING = SizingTuningConfig(
    base_order_size_usd=_env_float(
        "SIZING_BASE_ORDER_SIZE_USD",
        _env_float(
            "BASE_SIZE_USD",
            _env_float("TRADE_SIZE_USD", _profile_default(30.0, 25.0, 22.0)),
        ),
    ),
    range_order_size_multiplier=_env_float("SIZING_RANGE_ORDER_SIZE_MULTIPLIER", _env_float("RANGE_SIZE_MULTIPLIER", 1.0)),
    trend_order_size_multiplier=_env_float(
        "SIZING_TREND_ORDER_SIZE_MULTIPLIER",
        _env_float("AGGRESSIVE_SIZE_MULT", _profile_default(0.9, 0.9, 1.6)),
    ),
    high_vol_order_size_multiplier=_env_float(
        "SIZING_HIGH_VOL_ORDER_SIZE_MULTIPLIER",
        _env_float("DEFENSIVE_SIZE_MULT", _profile_default(0.7, 0.7, 0.55)),
    ),
    inventory_pressure_size_multiplier=_env_float("SIZING_INVENTORY_PRESSURE_SIZE_MULTIPLIER", 0.65),
)

VOLATILITY_TUNING = VolatilityTuningConfig(
    low_vol_atr_pct_max=_env_float("VOLATILITY_LOW_VOL_ATR_PCT_MAX", _legacy_low_vol_threshold_pct),
    mid_vol_atr_pct_max=_env_float("VOLATILITY_MID_VOL_ATR_PCT_MAX", _legacy_high_vol_threshold_pct),
    high_vol_atr_pct_min=_env_float("VOLATILITY_HIGH_VOL_ATR_PCT_MIN", _legacy_high_vol_threshold_pct),
    low_vol_entry_threshold_bps=_env_float("VOLATILITY_LOW_VOL_ENTRY_THRESHOLD_BPS", 6.0),
    mid_vol_entry_threshold_bps=_env_float("VOLATILITY_MID_VOL_ENTRY_THRESHOLD_BPS", 9.0),
    high_vol_entry_threshold_bps=_env_float("VOLATILITY_HIGH_VOL_ENTRY_THRESHOLD_BPS", 14.0),
    low_vol_take_profit_bps=_env_float("VOLATILITY_LOW_VOL_TAKE_PROFIT_BPS", 7.0),
    mid_vol_take_profit_bps=_env_float("VOLATILITY_MID_VOL_TAKE_PROFIT_BPS", 10.0),
    high_vol_take_profit_bps=_env_float("VOLATILITY_HIGH_VOL_TAKE_PROFIT_BPS", 15.0),
    low_vol_max_hold_minutes=_env_float("VOLATILITY_LOW_VOL_MAX_HOLD_MINUTES", 16.0),
    mid_vol_max_hold_minutes=_env_float("VOLATILITY_MID_VOL_MAX_HOLD_MINUTES", 20.0),
    high_vol_max_hold_minutes=_env_float("VOLATILITY_HIGH_VOL_MAX_HOLD_MINUTES", 12.0),
)

ACTIVITY_TUNING = ActivityTuningConfig(
    activity_window_hours=_env_float("ACTIVITY_WINDOW_HOURS", _env_float("LOW_ACTIVITY_LOOKBACK_HOURS", 6.0)),
    min_trades_per_activity_window=_env_int("ACTIVITY_MIN_TRADES_PER_ACTIVITY_WINDOW", _env_int("LOW_ACTIVITY_MIN_TRADES", 6)),
    daily_min_trade_target=_profile_int("ACTIVITY_DAILY_MIN_TRADE_TARGET", 15, 10),
    daily_good_trade_target=_env_int("ACTIVITY_DAILY_GOOD_TRADE_TARGET", 20),
    daily_aggressive_trade_cap=_env_int("ACTIVITY_DAILY_AGGRESSIVE_TRADE_CAP", _env_int("MAX_TRADES_PER_DAY", 40)),
    auto_loosen_if_low_activity=_env_bool("ACTIVITY_AUTO_LOOSEN_IF_LOW_ACTIVITY", _env_bool("LOW_ACTIVITY_GUARD_ENABLED", True)),
    auto_loosen_entry_bps=_profile_float("ACTIVITY_AUTO_LOOSEN_ENTRY_BPS", 3.0, 1.0),
    auto_loosen_min_edge_bps=_profile_float("ACTIVITY_AUTO_LOOSEN_MIN_EDGE_BPS", 2.0, 0.5),
    prioritize_range_mode_when_low_activity=_env_bool("ACTIVITY_PRIORITIZE_RANGE_MODE_WHEN_LOW_ACTIVITY", True),
    inactivity_force_entry_minutes=_env_float("INACTIVITY_FORCE_ENTRY_MINUTES", 10.0),
    inactivity_force_entry_threshold_bps=_env_float("INACTIVITY_FORCE_ENTRY_THRESHOLD_BPS", 3.5),
    inactivity_force_min_edge_bps=_env_float("INACTIVITY_FORCE_MIN_EDGE_BPS", -5.0),
    inactivity_force_size_multiplier=_env_float("INACTIVITY_FORCE_SIZE_MULTIPLIER", 0.7),
    allow_micro_edge_entries=_profile_bool("ALLOW_MICRO_EDGE_ENTRIES", False, True),
    micro_edge_min_bps=_env_float("MICRO_EDGE_MIN_BPS", 2.5),
)

# Backward-compatible aliases for the rest of the codebase.
BOT_CONFIG_PROFILE = _BOT_CONFIG_PROFILE
BOT_MODE = CORE.bot_mode
CHAIN = CORE.chain
RPC_URL = CORE.rpc_url or (CORE.rpc_urls[0] if CORE.rpc_urls else "")
RPC_URLS = CORE.rpc_urls
RPC_TIMEOUT_SEC = CORE.rpc_timeout_sec
RPC_MAX_RETRIES = CORE.rpc_max_retries
RPC_RETRY_BACKOFF_SEC = CORE.rpc_retry_backoff_sec
WALLET_PRIVATE_KEY = WALLET.wallet_private_key
WALLET_ADDRESS = WALLET.wallet_address
TELEGRAM_ENABLED = TELEGRAM.enabled
TELEGRAM_BOT_TOKEN = TELEGRAM.bot_token
TELEGRAM_CHAT_ID = TELEGRAM.chat_id
TELEGRAM_POLL_COMMANDS = TELEGRAM.poll_commands
TELEGRAM_DAILY_REPORT_ENABLED = TELEGRAM.daily_report_enabled
TELEGRAM_DAILY_REPORT_HOUR = TELEGRAM.daily_report_hour
TELEGRAM_API_TIMEOUT_SEC = TELEGRAM.api_timeout_sec
TELEGRAM_API_MAX_RETRIES = TELEGRAM.api_max_retries
TELEGRAM_RATE_LIMIT_SECONDS = TELEGRAM.rate_limit_seconds

TRADE_SIZE_USD = EXECUTION.trade_size_usd
MAX_TRADE_SIZE_USD = EXECUTION.max_trade_size_usd
ACCOUNT_REFERENCE_MODE = EXECUTION.account_reference_mode
TRADE_SIZE_PCT = EXECUTION.trade_size_pct
MAX_POSITION_PCT = EXECUTION.max_position_pct
MAX_TRADE_SIZE_PCT = EXECUTION.max_trade_size_pct
FORCE_TRADE_SIZE_PCT = EXECUTION.force_trade_size_pct
TARGET_BASE_PCT = EXECUTION.target_base_pct
TARGET_QUOTE_PCT = EXECUTION.target_quote_pct
MIN_NOTIONAL_USD = EXECUTION.min_notional_usd
MIN_BASE_RESERVE_PCT = EXECUTION.min_base_reserve_pct
MIN_QUOTE_RESERVE_PCT = EXECUTION.min_quote_reserve_pct
ACCOUNT_SIZE_OVERRIDE = EXECUTION.account_size_override
MAX_DAILY_LOSS_USD = EXECUTION.max_daily_loss_usd
MAX_EXPOSURE_USD = EXECUTION.max_exposure_usd
SPREAD_BPS = MARKET.spread_bps
MIN_SPREAD_BPS = MARKET.min_spread_bps
MAX_SPREAD_BPS = MARKET.max_spread_bps
MIN_ORDER_SIZE_USD = EXECUTION.min_notional_usd
MAX_INVENTORY_USD = PORTFOLIO.max_inventory_usd

START_USDC = PORTFOLIO.start_usdc
START_ETH = PORTFOLIO.start_eth
START_ETH_USD = PORTFOLIO.start_eth_usd

LOOP_SECONDS = EXECUTION.loop_seconds
MAX_LOOPS = EXECUTION.max_loops

MAKER_FEE_BPS = EXECUTION.maker_fee_bps
TAKER_FEE_BPS = EXECUTION.taker_fee_bps

TRADES_CSV = EXECUTION.trades_csv
EQUITY_CSV = EXECUTION.equity_csv

KILL_SWITCH_USD = EXECUTION.kill_switch_usd
INVENTORY_SKEW_STRENGTH = PORTFOLIO.inventory_skew_strength

TWAP_WINDOW = MARKET.twap_window
VOL_WINDOW = MARKET.vol_window
VOL_MULTIPLIER = MARKET.vol_multiplier

SHORT_MA_WINDOW = MARKET.short_ma_window
LONG_MA_WINDOW = MARKET.long_ma_window
EXECUTION_TIMEFRAME_SECONDS = MARKET.execution_timeframe_seconds
TREND_FILTER_TIMEFRAME_SECONDS = MARKET.trend_filter_timeframe_seconds
ENABLE_TREND_TIMEFRAME_FILTER = MARKET.enable_trend_timeframe_filter
ENABLE_EXECUTION_CONFIRMATION = MARKET.enable_execution_confirmation
CONFIRMATION_TIMEFRAME_SECONDS = MARKET.confirmation_timeframe_seconds
CONFIRMATION_MOMENTUM_SHOCK_BPS = MARKET.confirmation_momentum_shock_bps
PRICE_BOOTSTRAP_ROWS = MARKET.price_bootstrap_rows
PRICE_HISTORY_MAX_AGE_SECONDS = MARKET.price_history_max_age_seconds
TREND_THRESHOLD = MARKET.trend_threshold
HIGH_VOL_THRESHOLD = MARKET.high_vol_threshold

TREND_SIZE_MULTIPLIER = TREND.trend_size_multiplier
RANGE_SIZE_MULTIPLIER = TREND.range_size_multiplier
TREND_BUY_TARGET_PCT = TREND.trend_buy_target_pct
MAX_TREND_CHASE_BPS = TREND.max_trend_chase_bps
MAX_TREND_PULLBACK_BPS = TREND.max_trend_pullback_bps
TREND_BUY_MIN_MARKET_SCORE = TREND.trend_buy_min_market_score
TREND_BUY_MIN_SIGNAL_SCORE = TREND.trend_buy_min_signal_score
TREND_BUY_MIN_CONFIDENCE = TREND.trend_buy_min_confidence
TREND_BUY_MIN_LONG_BUFFER_BPS = TREND.trend_buy_min_long_buffer_bps
TREND_BUY_MIN_STRENGTH_MULTIPLIER = TREND.trend_buy_min_strength_multiplier
TREND_BUY_REQUOTE_BPS = TREND.trend_buy_requote_bps
RANGE_SPREAD_TIGHTENING = TREND.range_spread_tightening
RANGE_DIRECTIONAL_BIAS_FACTOR = TREND.range_directional_bias_factor
TREND_DIRECTIONAL_BIAS_FACTOR = TREND.trend_directional_bias_factor
RANGE_TARGET_INVENTORY_MIN = TREND.range_target_inventory_min
RANGE_TARGET_INVENTORY_MAX = TREND.range_target_inventory_max
TREND_TARGET_INVENTORY_MIN = TREND.trend_target_inventory_min
TREND_TARGET_INVENTORY_MAX = TREND.trend_target_inventory_max
RISK_OFF_TARGET_INVENTORY_MAX = TREND.risk_off_target_inventory_max
CAUTION_TARGET_INVENTORY_CAP = TREND.caution_target_inventory_cap
SIDE_FLIP_COOLDOWN_CYCLES = TREND.side_flip_cooldown_cycles
SIDE_FLIP_MIN_BPS = TREND.side_flip_min_bps
TREND_SELL_SPREAD_FACTOR = TREND.trend_sell_spread_factor
TREND_BUY_FILL_BONUS = TREND.trend_buy_fill_bonus
TREND_SELL_FILL_BONUS = TREND.trend_sell_fill_bonus
TREND_OVERWEIGHT_UNWIND_FRACTION = TREND.trend_overweight_unwind_fraction
TREND_OVERWEIGHT_MAX_MULTIPLIER = TREND.trend_overweight_max_multiplier
MIN_SELL_PROFIT_BPS = TREND.min_sell_profit_bps
MAX_EXIT_PREMIUM_BPS = TREND.max_exit_premium_bps

REENTRY_ENGINE_ENABLED = REENTRY.enabled
REENTRY_INVENTORY_BUFFER_PCT = REENTRY.inventory_buffer_pct
WAIT_REENTRY_PULLBACK_PCT = REENTRY.wait_reentry_pullback_pct
REENTRY_ZONE_1_MULTIPLIER = REENTRY.zone_1_multiplier
REENTRY_ZONE_2_MULTIPLIER = REENTRY.zone_2_multiplier
REENTRY_ZONE_3_MULTIPLIER = REENTRY.zone_3_multiplier
REENTRY_ZONE_1_BUY_FRACTION = REENTRY.zone_1_buy_fraction
REENTRY_ZONE_2_BUY_FRACTION = REENTRY.zone_2_buy_fraction
REENTRY_ZONE_3_BUY_FRACTION = REENTRY.zone_3_buy_fraction
REENTRY_TIMEOUT_MINUTES = REENTRY.timeout_minutes
REENTRY_TIMEOUT_BUY_FRACTION = REENTRY.timeout_buy_fraction
REENTRY_RUNAWAY_BUY_FRACTION = REENTRY.runaway_buy_fraction
REENTRY_MAX_MISS_PCT = REENTRY.max_miss_reentry_pct
REENTRY_MAX_MISS_BUY_FRACTION = REENTRY.max_miss_buy_fraction
REENTRY_RSI_PERIOD = REENTRY.rsi_period
REENTRY_RSI_BUY_THRESHOLD = REENTRY.rsi_buy_threshold
REENTRY_RSI_TURN_MARGIN = REENTRY.rsi_turn_margin
REENTRY_MOMENTUM_LOOKBACK = REENTRY.momentum_lookback
STOP_LOSS_PCT = REENTRY.stop_loss_pct
PROFIT_LOCK_LEVEL_1_BPS = REENTRY.profit_lock_level_1_bps
RANGE_PROFIT_LOCK_LEVEL_1_BPS = REENTRY.range_profit_lock_level_1_bps
PROFIT_LOCK_LEVEL_1_SELL_FRACTION = REENTRY.profit_lock_level_1_sell_fraction
PROFIT_LOCK_LEVEL_2_BPS = REENTRY.profit_lock_level_2_bps
RANGE_PROFIT_LOCK_LEVEL_2_BPS = REENTRY.range_profit_lock_level_2_bps
PROFIT_LOCK_LEVEL_2_SELL_FRACTION = REENTRY.profit_lock_level_2_sell_fraction
MICRO_TRAILING_PULLBACK_BPS = REENTRY.micro_trailing_pullback_bps
ETH_ACCUMULATION_REINVEST_PCT = REENTRY.eth_accumulation_reinvest_pct
ETH_PRESERVATION_FLOOR_MULTIPLIER = REENTRY.eth_preservation_floor_multiplier
PARTIAL_RESET_USDC_THRESHOLD_PCT = REENTRY.partial_reset_usdc_threshold_pct
PARTIAL_RESET_BUY_FRACTION = REENTRY.partial_reset_buy_fraction

EXECUTION_ENGINE_ENABLED = EXECUTION_POLICY.enabled
EXECUTION_MIN_EXPECTED_PROFIT_PCT = EXECUTION_POLICY.min_expected_profit_pct
EXECUTION_MAKER_SLIPPAGE_BPS = EXECUTION_POLICY.maker_slippage_bps
EXECUTION_TAKER_SLIPPAGE_BPS = EXECUTION_POLICY.taker_slippage_bps
EXECUTION_SLIPPAGE_SIZE_FACTOR = EXECUTION_POLICY.slippage_size_factor

ENABLE_PRIVATE_TX = MEV_EXECUTION.enable_private_tx
PRIVATE_RPC_URL = MEV_EXECUTION.private_rpc_url or (
    MEV_EXECUTION.private_rpc_urls[0] if MEV_EXECUTION.private_rpc_urls else ""
)
PRIVATE_RPC_URLS = MEV_EXECUTION.private_rpc_urls
PRIVATE_TX_TIMEOUT_SEC = MEV_EXECUTION.private_tx_timeout_sec
PRIVATE_TX_MAX_RETRIES = MEV_EXECUTION.private_tx_max_retries
ENABLE_COW = MEV_EXECUTION.enable_cow
COW_MIN_NOTIONAL_USD = MEV_EXECUTION.cow_min_notional_usd
COW_SUPPORTED_PAIRS = MEV_EXECUTION.cow_supported_pairs
ENABLE_ORDER_SLICING = MEV_EXECUTION.enable_order_slicing
MAX_SINGLE_SWAP_USD = MEV_EXECUTION.max_single_swap_usd
SLICE_COUNT_MAX = MEV_EXECUTION.slice_count_max
SLICE_DELAY_MS = MEV_EXECUTION.slice_delay_ms
MAX_QUOTE_DEVIATION_BPS = MEV_EXECUTION.max_quote_deviation_bps
MAX_TWAP_DEVIATION_BPS = MEV_EXECUTION.max_twap_deviation_bps
MAX_PRICE_IMPACT_BPS = MEV_EXECUTION.max_price_impact_bps
MAX_SLIPPAGE_BPS = MEV_EXECUTION.max_slippage_bps
MAX_GAS_SPIKE_GWEI = MEV_EXECUTION.max_gas_spike_gwei
ESTIMATED_SWAP_GAS_UNITS = MEV_EXECUTION.estimated_swap_gas_units
MAX_GAS_TO_PROFIT_RATIO = MEV_EXECUTION.max_gas_to_profit_ratio
MEV_RISK_THRESHOLD_BLOCK = MEV_EXECUTION.mev_risk_threshold_block
PUBLIC_SWAP_MAX_RISK = MEV_EXECUTION.public_swap_max_risk
EXECUTION_POLICY_PROFILE = MEV_EXECUTION.execution_policy_profile
MEV_POLICY_PATH = MEV_EXECUTION.mev_policy_path

TRADE_FILTER_ENABLED = TRADE_FILTER.enabled
TRADE_FILTER_MOMENTUM_LIMIT_LOW_VOL_BPS = TRADE_FILTER.momentum_limit_low_vol_bps
TRADE_FILTER_MOMENTUM_LIMIT_MID_VOL_BPS = TRADE_FILTER.momentum_limit_mid_vol_bps
TRADE_FILTER_MOMENTUM_LIMIT_HIGH_VOL_BPS = TRADE_FILTER.momentum_limit_high_vol_bps
TRADE_FILTER_MOMENTUM_LIMIT_BPS = TRADE_FILTER.momentum_limit_mid_vol_bps
TRADE_FILTER_BUY_RSI_MAX = TRADE_FILTER.buy_rsi_max
TRADE_FILTER_SELL_RSI_MIN = TRADE_FILTER.sell_rsi_min
TRADE_FILTER_LOSS_STREAK_LIMIT = TRADE_FILTER.loss_streak_limit
MIN_TRADE_DISTANCE_PCT = TRADE_FILTER.min_trade_distance_pct
TRADE_COOLDOWN_MINUTES = TRADE_FILTER.cooldown_minutes
MIN_TIME_BETWEEN_TRADES_MINUTES = TRADE_FILTER.min_time_between_trades_minutes
MAX_TRADES_PER_DAY = TRADE_FILTER.max_trades_per_day
TRADE_FILTER_TREND_AGAINST_SCORE = TRADE_FILTER.trend_against_score
TRADE_FILTER_STRONG_TREND_SCORE = TRADE_FILTER.strong_trend_score
TRADE_FILTER_STRONG_TREND_SKIP_SCORE = TRADE_FILTER.strong_trend_skip_score
TRADE_FILTER_STRONG_TREND_SIZE_MULTIPLIER = TRADE_FILTER.strong_trend_size_multiplier
TRADE_FILTER_FORCE_TRADE_MINUTES = TRADE_FILTER.force_trade_minutes
FORCE_TRADE_SIZE_FRACTION = TRADE_FILTER.force_trade_size_fraction
MIN_TRADE_RATE_PER_HOUR = TRADE_FILTER.min_trades_per_hour
TRADE_FILTER_DEBUG_MODE = TRADE_FILTER.debug_mode

INVENTORY_MANAGER_ENABLED = INVENTORY_MANAGER.enabled
INVENTORY_NORMAL_MIN = INVENTORY_MANAGER.normal_min
INVENTORY_NORMAL_MAX = INVENTORY_MANAGER.normal_max
INVENTORY_UPTREND_MIN = INVENTORY_MANAGER.uptrend_min
INVENTORY_UPTREND_MAX = INVENTORY_MANAGER.uptrend_max
INVENTORY_DOWNTREND_MIN = INVENTORY_MANAGER.downtrend_min
INVENTORY_DOWNTREND_MAX = INVENTORY_MANAGER.downtrend_max

STATE_MACHINE_ENABLED = STATE_MACHINE.enabled
STATE_MACHINE_LOSS_STREAK_LIMIT = STATE_MACHINE.loss_streak_limit
STATE_MACHINE_COOLDOWN_MINUTES = STATE_MACHINE.cooldown_minutes
STATE_MACHINE_MAX_COOLDOWN_MINUTES = STATE_MACHINE.max_cooldown_minutes
STATE_MACHINE_ACCUMULATING_FAILSAFE_MINUTES = STATE_MACHINE.accumulating_failsafe_minutes

DECISION_ENGINE_ENABLED = DECISION_ENGINE.enabled

UNISWAP_V3_FACTORY = DEX.uniswap_v3_factory
BASE_WETH = DEX.base_weth
BASE_USDC = DEX.base_usdc
UNISWAP_POOL_FEE = DEX.uniswap_pool_fee

PRICE_CACHE_SECONDS = EXECUTION.price_cache_seconds
SQLITE_LOG_PATH = EXECUTION.sqlite_log_path

SIGNAL_CACHE_SECONDS = INTELLIGENCE.signal_cache_seconds
SIGNAL_HTTP_TIMEOUT_SECONDS = INTELLIGENCE.signal_http_timeout_seconds
SIGNAL_FETCH_ENABLED = INTELLIGENCE.signal_fetch_enabled
SIGNAL_CAUTION_THRESHOLD = INTELLIGENCE.signal_caution_threshold
SIGNAL_BLOCK_THRESHOLD = INTELLIGENCE.signal_block_threshold
SIGNAL_BLOCK_RISK_THRESHOLD = INTELLIGENCE.signal_block_risk_threshold
CAUTION_SPREAD_MULTIPLIER = INTELLIGENCE.caution_spread_multiplier
CAUTION_SIZE_MULTIPLIER = INTELLIGENCE.caution_size_multiplier

INTELLIGENCE_WARMUP_ROWS = INTELLIGENCE.intelligence_warmup_rows
LOW_VOL_THRESHOLD_MULTIPLIER = INTELLIGENCE.low_vol_threshold_multiplier
EXTREME_VOL_THRESHOLD_MULTIPLIER = INTELLIGENCE.extreme_vol_threshold_multiplier
RISK_OFF_TREND_MULTIPLIER = INTELLIGENCE.risk_off_trend_multiplier
OVERWEIGHT_EXIT_BUFFER_PCT = INTELLIGENCE.overweight_exit_buffer_pct
CAPITAL_PRESERVATION_DRAWDOWN_PCT = INTELLIGENCE.capital_preservation_drawdown_pct
RISK_BLOCK_DRAWDOWN_PCT = INTELLIGENCE.risk_block_drawdown_pct
DRAWDOWN_PAUSE_PCT = INTELLIGENCE.drawdown_pause_pct
ADAPTIVE_LOOKBACK_CYCLES = INTELLIGENCE.adaptive_lookback_cycles
MIN_INVENTORY_MULTIPLIER = INTELLIGENCE.min_inventory_multiplier
MAX_INVENTORY_MULTIPLIER = INTELLIGENCE.max_inventory_multiplier
MIN_TRADE_SIZE_MULTIPLIER = INTELLIGENCE.min_trade_size_multiplier
MAX_TRADE_SIZE_MULTIPLIER = INTELLIGENCE.max_trade_size_multiplier
MIN_SPREAD_MULTIPLIER = INTELLIGENCE.min_spread_multiplier
MAX_SPREAD_MULTIPLIER = INTELLIGENCE.max_spread_multiplier

ENABLE_REGIME_DETECTOR = _env_bool("ENABLE_REGIME_DETECTOR", True)
ENABLE_EDGE_FILTER = _env_bool("ENABLE_EDGE_FILTER", True)
REGIME_LOOKBACK_CANDLES = _env_int("REGIME_LOOKBACK_CANDLES", 24)
REGIME_CHOP_NET_MOVE_PCT = _env_float("REGIME_CHOP_NET_MOVE_PCT", 0.30)
REGIME_TREND_NET_MOVE_PCT = _env_float("REGIME_TREND_NET_MOVE_PCT", 0.50)
REGIME_MIN_DIRECTIONAL_CONSISTENCY = _env_float("REGIME_MIN_DIRECTIONAL_CONSISTENCY", 0.60)
REGIME_RANGE_WIDTH_MIN_PCT = _env_float("REGIME_RANGE_WIDTH_MIN_PCT", 0.40)
REGIME_RANGE_WIDTH_MAX_PCT = _env_float("REGIME_RANGE_WIDTH_MAX_PCT", 2.00)
EDGE_SCORE_MIN = _env_float("EDGE_SCORE_MIN", 55.0)
EDGE_SCORE_MIN_REENTRY = _env_float("EDGE_SCORE_MIN_REENTRY", 60.0)
EXPECTED_EDGE_MIN_USD = _env_float("EXPECTED_EDGE_MIN_USD", 0.05)
EXPECTED_EDGE_MIN_BPS = _env_float("EXPECTED_EDGE_MIN_BPS", -5.0)
REENTRY_MIN_PULLBACK_PCT = _env_float("REENTRY_MIN_PULLBACK_PCT", 0.40)
REENTRY_EDGE_SCORE_MIN = _env_float("REENTRY_EDGE_SCORE_MIN", 60.0)
REENTRY_BLOCK_AFTER_LOSS_MINUTES = _env_float("REENTRY_BLOCK_AFTER_LOSS_MINUTES", 20.0)
MAX_CONSECUTIVE_LOSSES_BEFORE_PAUSE = _env_int("MAX_CONSECUTIVE_LOSSES_BEFORE_PAUSE", 8)
LOSS_PAUSE_MINUTES = _env_float("LOSS_PAUSE_MINUTES", 30.0)
HIGH_EDGE_OVERRIDE_SCORE = _env_float("HIGH_EDGE_OVERRIDE_SCORE", 80.0)
CHOP_DISABLE_NEW_TRADES = _env_bool("CHOP_DISABLE_NEW_TRADES", not _V5_AGGRESSIVE_PROFILE_ENABLED)
TREND_DOWN_DISABLE_RANGE_BUYS = _env_bool("TREND_DOWN_DISABLE_RANGE_BUYS", not _V5_AGGRESSIVE_PROFILE_ENABLED)
TREND_UP_DISABLE_COUNTERTREND_SELLS = _env_bool("TREND_UP_DISABLE_COUNTERTREND_SELLS", not _V5_AGGRESSIVE_PROFILE_ENABLED)
EMA_RANGE_BAND_BPS = _env_float("EMA_RANGE_BAND_BPS", 6.0)
TREND_MOMENTUM_BLOCK_BPS = _env_float("TREND_MOMENTUM_BLOCK_BPS", 35.0)
ENTRY_TRIGGER_RELAX_MULTIPLIER = _env_float("ENTRY_TRIGGER_RELAX_MULTIPLIER", 0.74)
MIN_EDGE_RELAX_MULTIPLIER = _env_float("MIN_EDGE_RELAX_MULTIPLIER", 0.82)
LOW_VOL_TRIGGER_MULTIPLIER = _env_float("LOW_VOL_TRIGGER_MULTIPLIER", 0.86)
HIGH_VOL_TRIGGER_MULTIPLIER = _env_float("HIGH_VOL_TRIGGER_MULTIPLIER", 1.14)
LOW_VOL_PROFIT_TARGET_MULTIPLIER = _env_float("LOW_VOL_PROFIT_TARGET_MULTIPLIER", 0.85)
RANGE_ENTRY_MAX_POSITION_PCT = _env_float("RANGE_ENTRY_MAX_POSITION_PCT", 0.36)
RANGE_EXIT_MIN_POSITION_PCT = _env_float("RANGE_EXIT_MIN_POSITION_PCT", 0.68)
RANGE_MEAN_REVERSION_EXIT_POSITION_PCT = _env_float("RANGE_MEAN_REVERSION_EXIT_POSITION_PCT", 0.54)
INVENTORY_SOFT_LIMIT_PCT = _env_float("INVENTORY_SOFT_LIMIT_PCT", 0.88)
INVENTORY_HARD_LIMIT_PCT = _env_float("INVENTORY_HARD_LIMIT_PCT", 1.00)
INVENTORY_SKEW_ACCELERATION = _env_float("INVENTORY_SKEW_ACCELERATION", 1.75)
MAX_POSITION_HOLD_MINUTES = _env_float("MAX_POSITION_HOLD_MINUTES", 90.0)
LOW_ACTIVITY_GUARD_ENABLED = _env_bool(
    "LOW_ACTIVITY_GUARD_ENABLED",
    BOT_MODE.strip().lower().startswith("paper"),
)
LOW_ACTIVITY_LOOKBACK_HOURS = _env_float("LOW_ACTIVITY_LOOKBACK_HOURS", 4.0)
LOW_ACTIVITY_MIN_TRADES = _env_int("LOW_ACTIVITY_MIN_TRADES", 3)
LOW_ACTIVITY_MAX_DRAWDOWN_PCT = _env_float("LOW_ACTIVITY_MAX_DRAWDOWN_PCT", 0.015)
LOW_ACTIVITY_THRESHOLD_RELAX_MULTIPLIER = _env_float("LOW_ACTIVITY_THRESHOLD_RELAX_MULTIPLIER", 0.94)

_strategy_target_base_pct = _clamp(_ratio_from_pct_points(INVENTORY_TUNING.inventory_target_pct), 0.0, 1.0)
_strategy_neutral_band_ratio = _clamp(_ratio_from_pct_points(INVENTORY_TUNING.inventory_neutral_band_pct), 0.0, 0.49)
_strategy_range_target_min = _clamp(_strategy_target_base_pct - _strategy_neutral_band_ratio, 0.0, 1.0)
_strategy_range_target_max = _clamp(_strategy_target_base_pct + _strategy_neutral_band_ratio, 0.0, 1.0)
_strategy_soft_limit_abs_ratio = _clamp(
    _strategy_target_base_pct + _ratio_from_pct_points(INVENTORY_TUNING.inventory_soft_limit_pct),
    _strategy_range_target_max,
    1.0,
)
_strategy_hard_limit_abs_ratio = _clamp(
    _strategy_target_base_pct + _ratio_from_pct_points(INVENTORY_TUNING.inventory_hard_limit_pct),
    _strategy_soft_limit_abs_ratio,
    1.0,
)
_strategy_force_limit_abs_ratio = _clamp(
    _strategy_target_base_pct + _ratio_from_pct_points(INVENTORY_TUNING.force_inventory_reduction_above_pct),
    _strategy_hard_limit_abs_ratio,
    1.0,
)
_strategy_high_vol_ratio = max(_ratio_from_pct_points(VOLATILITY_TUNING.high_vol_atr_pct_min), 1e-6)
_strategy_low_vol_ratio = max(_ratio_from_pct_points(VOLATILITY_TUNING.low_vol_atr_pct_max), 1e-6)

TRADE_SIZE_USD = SIZING_TUNING.base_order_size_usd
TARGET_BASE_PCT = _strategy_target_base_pct
TARGET_QUOTE_PCT = _clamp(1.0 - TARGET_BASE_PCT, 0.0, 1.0)
HIGH_VOL_THRESHOLD = _strategy_high_vol_ratio
LOW_VOL_THRESHOLD_MULTIPLIER = _clamp(_strategy_low_vol_ratio / _strategy_high_vol_ratio, 0.05, 1.0)
EXTREME_VOL_THRESHOLD_MULTIPLIER = _clamp(
    max(
        _strategy_high_vol_ratio + max(_strategy_high_vol_ratio - _strategy_low_vol_ratio, 0.0),
        _strategy_high_vol_ratio * 1.20,
    ) / _strategy_high_vol_ratio,
    1.20,
    4.0,
)
TREND_SIZE_MULTIPLIER = SIZING_TUNING.trend_order_size_multiplier
RANGE_SIZE_MULTIPLIER = SIZING_TUNING.range_order_size_multiplier
TREND_BUY_REQUOTE_BPS = max(EXECUTION_TUNING.reprice_on_mid_move_bps, 0.0)
PROFIT_LOCK_LEVEL_1_BPS = TREND_STRATEGY.soft_take_profit_bps
PROFIT_LOCK_LEVEL_2_BPS = TREND_STRATEGY.take_profit_bps
RANGE_PROFIT_LOCK_LEVEL_1_BPS = RANGE_STRATEGY.soft_take_profit_bps
RANGE_PROFIT_LOCK_LEVEL_2_BPS = RANGE_STRATEGY.take_profit_bps
RANGE_TARGET_INVENTORY_MIN = _strategy_range_target_min
RANGE_TARGET_INVENTORY_MAX = _strategy_range_target_max
TREND_TARGET_INVENTORY_MIN = _clamp(_strategy_target_base_pct, 0.0, 1.0)
TREND_TARGET_INVENTORY_MAX = _strategy_range_target_max
RISK_OFF_TARGET_INVENTORY_MAX = _clamp(_strategy_target_base_pct - (_strategy_neutral_band_ratio * 0.50), 0.0, 1.0)
CAUTION_TARGET_INVENTORY_CAP = _clamp(_strategy_target_base_pct + (_strategy_neutral_band_ratio * 0.50), 0.0, 1.0)
MAX_TRADES_PER_DAY = ACTIVITY_TUNING.daily_aggressive_trade_cap
MIN_TRADE_RATE_PER_HOUR = TRADE_FILTER.min_trades_per_hour
LOW_ACTIVITY_GUARD_ENABLED = ACTIVITY_TUNING.auto_loosen_if_low_activity
LOW_ACTIVITY_LOOKBACK_HOURS = ACTIVITY_TUNING.activity_window_hours
LOW_ACTIVITY_MIN_TRADES = ACTIVITY_TUNING.min_trades_per_activity_window
LOW_ACTIVITY_THRESHOLD_RELAX_MULTIPLIER = _clamp(
    (EXECUTION_TUNING.base_entry_threshold_bps - ACTIVITY_TUNING.auto_loosen_entry_bps)
    / max(EXECUTION_TUNING.base_entry_threshold_bps, 1.0),
    0.50,
    1.0,
)
MIN_EDGE_RELAX_MULTIPLIER = _clamp(
    (RANGE_STRATEGY.min_edge_bps - ACTIVITY_TUNING.auto_loosen_min_edge_bps)
    / max(RANGE_STRATEGY.min_edge_bps, 1.0),
    0.50,
    1.0,
)
LOW_VOL_TRIGGER_MULTIPLIER = _clamp(
    VOLATILITY_TUNING.low_vol_entry_threshold_bps / max(EXECUTION_TUNING.base_entry_threshold_bps, 1.0),
    0.35,
    1.50,
)
HIGH_VOL_TRIGGER_MULTIPLIER = _clamp(
    VOLATILITY_TUNING.high_vol_entry_threshold_bps / max(EXECUTION_TUNING.base_entry_threshold_bps, 1.0),
    0.50,
    2.00,
)
LOW_VOL_PROFIT_TARGET_MULTIPLIER = _clamp(
    VOLATILITY_TUNING.low_vol_take_profit_bps / max(VOLATILITY_TUNING.mid_vol_take_profit_bps, 1.0),
    0.30,
    1.0,
)
RANGE_ENTRY_MAX_POSITION_PCT = _clamp(RANGE_STRATEGY.range_bottom_zone_pct, 0.0, 0.49)
RANGE_EXIT_MIN_POSITION_PCT = _clamp(1.0 - RANGE_STRATEGY.range_top_zone_pct, 0.51, 1.0)
RANGE_MEAN_REVERSION_EXIT_POSITION_PCT = _clamp(
    0.5 + (RANGE_STRATEGY.range_mid_no_trade_zone_pct / 2.0),
    0.50,
    RANGE_EXIT_MIN_POSITION_PCT,
)
INVENTORY_NORMAL_MIN = _strategy_range_target_min
INVENTORY_NORMAL_MAX = _strategy_range_target_max
INVENTORY_UPTREND_MIN = _strategy_range_target_min
INVENTORY_UPTREND_MAX = _strategy_range_target_max
INVENTORY_DOWNTREND_MIN = _clamp(_strategy_target_base_pct - (_strategy_neutral_band_ratio * 1.50), 0.0, 1.0)
INVENTORY_DOWNTREND_MAX = _strategy_target_base_pct
INVENTORY_SOFT_LIMIT_PCT = _strategy_soft_limit_abs_ratio / max(INVENTORY_NORMAL_MAX, 1e-6)
INVENTORY_HARD_LIMIT_PCT = _strategy_hard_limit_abs_ratio / max(INVENTORY_NORMAL_MAX, 1e-6)
INVENTORY_FORCE_REDUCE_LIMIT_PCT = _strategy_force_limit_abs_ratio
MAX_POSITION_HOLD_MINUTES = EXECUTION_TUNING.max_position_hold_minutes
EXPECTED_EDGE_MIN_BPS = RANGE_STRATEGY.min_edge_bps
REGIME_LOOKBACK_CANDLES = max(REGIME_TUNING.trend_lookback_bars, REGIME_TUNING.range_lookback_bars)
REGIME_TREND_NET_MOVE_PCT = REGIME_TUNING.trend_min_net_move_pct
REGIME_MIN_DIRECTIONAL_CONSISTENCY = _clamp(
    REGIME_TUNING.trend_min_same_direction_bars / max(REGIME_TUNING.trend_lookback_bars - 1, 1),
    0.0,
    1.0,
)

REGIME_DEFAULT_REGIME = REGIME_TUNING.default_regime
REGIME_TREND_OVERRIDE_ONLY_IF_STRONG = REGIME_TUNING.trend_override_only_if_strong
REGIME_RANGE_PRIORITY_WHEN_UNCLEAR = REGIME_TUNING.range_priority_when_unclear
REGIME_TREND_LOOKBACK_BARS = REGIME_TUNING.trend_lookback_bars
REGIME_TREND_MIN_NET_MOVE_PCT = REGIME_TUNING.trend_min_net_move_pct
REGIME_TREND_MIN_SAME_DIRECTION_BARS = REGIME_TUNING.trend_min_same_direction_bars
REGIME_TREND_MIN_DISTANCE_FROM_VWAP_PCT = REGIME_TUNING.trend_min_distance_from_vwap_pct
REGIME_TREND_MOMENTUM_ZSCORE_MIN = REGIME_TUNING.trend_momentum_zscore_min
REGIME_RANGE_LOOKBACK_BARS = REGIME_TUNING.range_lookback_bars
REGIME_RANGE_MAX_NET_MOVE_PCT = REGIME_TUNING.range_max_net_move_pct
REGIME_RANGE_MAX_DISTANCE_FROM_VWAP_PCT = REGIME_TUNING.range_max_distance_from_vwap_pct
REGIME_RANGE_STRUCTURE_REQUIRED = REGIME_TUNING.range_structure_required
REGIME_RANGE_STRUCTURE_TOLERANCE_PCT = REGIME_TUNING.range_structure_tolerance_pct
REGIME_SHOCK_BAR_MOVE_PCT = REGIME_TUNING.shock_bar_move_pct
REGIME_MAX_WICK_TO_BODY_RATIO = REGIME_TUNING.max_wick_to_body_ratio
REGIME_MAX_SPREAD_MULTIPLIER_VS_MEDIAN = REGIME_TUNING.max_spread_multiplier_vs_median
REGIME_POST_SHOCK_COOLDOWN_BARS = REGIME_TUNING.post_shock_cooldown_bars
RANGE_WINDOW_BARS = RANGE_STRATEGY.range_window_bars
RANGE_TOP_ZONE_PCT = RANGE_STRATEGY.range_top_zone_pct
RANGE_BOTTOM_ZONE_PCT = RANGE_STRATEGY.range_bottom_zone_pct
RANGE_MID_NO_TRADE_ZONE_PCT = RANGE_STRATEGY.range_mid_no_trade_zone_pct
RANGE_ENTRY_THRESHOLD_BPS = RANGE_STRATEGY.entry_threshold_bps
RANGE_MIN_EDGE_BPS = RANGE_STRATEGY.min_edge_bps
RANGE_TAKE_PROFIT_BPS = RANGE_STRATEGY.take_profit_bps
RANGE_SOFT_TAKE_PROFIT_BPS = RANGE_STRATEGY.soft_take_profit_bps
RANGE_MAX_HOLD_MINUTES = RANGE_STRATEGY.max_hold_minutes
RANGE_TIME_STOP_MINUTES = RANGE_STRATEGY.time_stop_minutes
RANGE_EXIT_ON_REVERSION_TO_MID = RANGE_STRATEGY.exit_on_reversion_to_mid
TREND_ENTRY_THRESHOLD_BPS = TREND_STRATEGY.entry_threshold_bps
TREND_MIN_EDGE_BPS = TREND_STRATEGY.min_edge_bps
TREND_TAKE_PROFIT_BPS = TREND_STRATEGY.take_profit_bps
TREND_SOFT_TAKE_PROFIT_BPS = TREND_STRATEGY.soft_take_profit_bps
TREND_MAX_HOLD_MINUTES = TREND_STRATEGY.max_hold_minutes
TREND_TIME_STOP_MINUTES = TREND_STRATEGY.time_stop_minutes
EXECUTION_BASE_ENTRY_THRESHOLD_BPS = EXECUTION_TUNING.base_entry_threshold_bps
EXECUTION_REQUOTE_INTERVAL_MS = EXECUTION_TUNING.requote_interval_ms
EXECUTION_STALE_QUOTE_TIMEOUT_MS = EXECUTION_TUNING.stale_quote_timeout_ms
EXECUTION_CANCEL_IF_FAR_FROM_MID_BPS = EXECUTION_TUNING.cancel_if_far_from_mid_bps
EXECUTION_REPRICE_ON_MID_MOVE_BPS = EXECUTION_TUNING.reprice_on_mid_move_bps
EXECUTION_FORCE_EXIT_IF_UNREALIZED_LOSS_BPS = EXECUTION_TUNING.force_exit_if_unrealized_loss_bps
EXECUTION_FORCE_EXIT_IF_REGIME_FLIP = EXECUTION_TUNING.force_exit_if_regime_flip
EXECUTION_FORCE_EXIT_IF_INVENTORY_PRESSURE = EXECUTION_TUNING.force_exit_if_inventory_pressure
INVENTORY_TARGET_PCT = INVENTORY_TUNING.inventory_target_pct
INVENTORY_NEUTRAL_BAND_PCT = INVENTORY_TUNING.inventory_neutral_band_pct
INVENTORY_SOFT_LIMIT_PCT_POINTS = INVENTORY_TUNING.inventory_soft_limit_pct
INVENTORY_HARD_LIMIT_PCT_POINTS = INVENTORY_TUNING.inventory_hard_limit_pct
INVENTORY_FORCE_REDUCE_PCT = INVENTORY_TUNING.inventory_force_reduce_pct
INVENTORY_FORCE_REDUCE_LIMIT_PCT = _strategy_force_limit_abs_ratio
INVENTORY_SAME_SIDE_ENTRY_PENALTY_BPS = INVENTORY_TUNING.same_side_entry_penalty_bps
INVENTORY_OPPOSITE_SIDE_ENTRY_BONUS_BPS = INVENTORY_TUNING.opposite_side_entry_bonus_bps
INVENTORY_BLOCK_SAME_SIDE_ENTRIES_ABOVE_HARD_LIMIT = INVENTORY_TUNING.block_same_side_entries_above_hard_limit
INVENTORY_ALLOW_REDUCE_ONLY_ABOVE_HARD_LIMIT = INVENTORY_TUNING.allow_reduce_only_above_hard_limit
INVENTORY_FORCE_INVENTORY_REDUCTION_ABOVE_PCT = INVENTORY_TUNING.force_inventory_reduction_above_pct
INVENTORY_FORCED_REDUCE_AGGRESSION_BPS = INVENTORY_TUNING.forced_reduce_aggression_bps
SIZING_BASE_ORDER_SIZE_USD = SIZING_TUNING.base_order_size_usd
SIZING_RANGE_ORDER_SIZE_MULTIPLIER = SIZING_TUNING.range_order_size_multiplier
SIZING_TREND_ORDER_SIZE_MULTIPLIER = SIZING_TUNING.trend_order_size_multiplier
SIZING_HIGH_VOL_ORDER_SIZE_MULTIPLIER = SIZING_TUNING.high_vol_order_size_multiplier
SIZING_INVENTORY_PRESSURE_SIZE_MULTIPLIER = SIZING_TUNING.inventory_pressure_size_multiplier
VOLATILITY_LOW_VOL_ATR_PCT_MAX = VOLATILITY_TUNING.low_vol_atr_pct_max
VOLATILITY_MID_VOL_ATR_PCT_MAX = VOLATILITY_TUNING.mid_vol_atr_pct_max
VOLATILITY_HIGH_VOL_ATR_PCT_MIN = VOLATILITY_TUNING.high_vol_atr_pct_min
VOLATILITY_LOW_VOL_ENTRY_THRESHOLD_BPS = VOLATILITY_TUNING.low_vol_entry_threshold_bps
VOLATILITY_MID_VOL_ENTRY_THRESHOLD_BPS = VOLATILITY_TUNING.mid_vol_entry_threshold_bps
VOLATILITY_HIGH_VOL_ENTRY_THRESHOLD_BPS = VOLATILITY_TUNING.high_vol_entry_threshold_bps
VOLATILITY_LOW_VOL_TAKE_PROFIT_BPS = VOLATILITY_TUNING.low_vol_take_profit_bps
VOLATILITY_MID_VOL_TAKE_PROFIT_BPS = VOLATILITY_TUNING.mid_vol_take_profit_bps
VOLATILITY_HIGH_VOL_TAKE_PROFIT_BPS = VOLATILITY_TUNING.high_vol_take_profit_bps
VOLATILITY_LOW_VOL_MAX_HOLD_MINUTES = VOLATILITY_TUNING.low_vol_max_hold_minutes
VOLATILITY_MID_VOL_MAX_HOLD_MINUTES = VOLATILITY_TUNING.mid_vol_max_hold_minutes
VOLATILITY_HIGH_VOL_MAX_HOLD_MINUTES = VOLATILITY_TUNING.high_vol_max_hold_minutes
ACTIVITY_WINDOW_HOURS = ACTIVITY_TUNING.activity_window_hours
ACTIVITY_MIN_TRADES_PER_ACTIVITY_WINDOW = ACTIVITY_TUNING.min_trades_per_activity_window
ACTIVITY_DAILY_MIN_TRADE_TARGET = ACTIVITY_TUNING.daily_min_trade_target
ACTIVITY_DAILY_GOOD_TRADE_TARGET = ACTIVITY_TUNING.daily_good_trade_target
ACTIVITY_DAILY_AGGRESSIVE_TRADE_CAP = ACTIVITY_TUNING.daily_aggressive_trade_cap
ACTIVITY_AUTO_LOOSEN_IF_LOW_ACTIVITY = ACTIVITY_TUNING.auto_loosen_if_low_activity
ACTIVITY_AUTO_LOOSEN_ENTRY_BPS = ACTIVITY_TUNING.auto_loosen_entry_bps
ACTIVITY_AUTO_LOOSEN_MIN_EDGE_BPS = ACTIVITY_TUNING.auto_loosen_min_edge_bps
ACTIVITY_PRIORITIZE_RANGE_MODE_WHEN_LOW_ACTIVITY = ACTIVITY_TUNING.prioritize_range_mode_when_low_activity
INACTIVITY_FORCE_ENTRY_MINUTES = ACTIVITY_TUNING.inactivity_force_entry_minutes
INACTIVITY_FORCE_ENTRY_THRESHOLD_BPS = ACTIVITY_TUNING.inactivity_force_entry_threshold_bps
INACTIVITY_FORCE_MIN_EDGE_BPS = ACTIVITY_TUNING.inactivity_force_min_edge_bps
INACTIVITY_FORCE_SIZE_MULTIPLIER = ACTIVITY_TUNING.inactivity_force_size_multiplier
ALLOW_MICRO_EDGE_ENTRIES = ACTIVITY_TUNING.allow_micro_edge_entries
MICRO_EDGE_MIN_BPS = ACTIVITY_TUNING.micro_edge_min_bps

BASE_SPREAD_BPS = SPREAD_BPS
BASE_SIZE_USD = TRADE_SIZE_USD
AGGRESSIVE_SIZE_MULT = _env_float("AGGRESSIVE_SIZE_MULT", _profile_default(0.9, 0.9, 1.6))
DEFENSIVE_SIZE_MULT = _env_float("DEFENSIVE_SIZE_MULT", _profile_default(0.7, 0.7, 0.55))
EDGE_STRONG_POS = _env_float("EDGE_STRONG_POS", _profile_default(0.004, 0.004, 0.003))
EDGE_WEAK_POS = _env_float("EDGE_WEAK_POS", 0.0)
EDGE_SOFT_NEG = _env_float("EDGE_SOFT_NEG", _profile_default(-0.0015, -0.0015, -0.002))
COOLDOWN_AGGRESSIVE_SEC = max(_env_float("COOLDOWN_AGGRESSIVE_SEC", _profile_default(30.0, 20.0, 15.0)), 0.0)
COOLDOWN_BASE_SEC = max(_env_float("COOLDOWN_BASE_SEC", _profile_default(120.0, 25.0, 25.0)), 0.0)
COOLDOWN_DEFENSIVE_SEC = max(_env_float("COOLDOWN_DEFENSIVE_SEC", _profile_default(180.0, 45.0, 45.0)), 0.0)
MIN_TRADES_PER_HOUR = max(_env_float("MIN_TRADES_PER_HOUR", MIN_TRADE_RATE_PER_HOUR), 0.0)
FREEZE_RECOVERY_MINUTES = max(_env_float("FREEZE_RECOVERY_MINUTES", _profile_default(30.0, 20.0, 20.0)), 0.0)
INVENTORY_TARGET_ETH_WEIGHT = TARGET_BASE_PCT
INVENTORY_BAND_LOW = RANGE_TARGET_INVENTORY_MIN
INVENTORY_BAND_HIGH = RANGE_TARGET_INVENTORY_MAX
SKEW_STRENGTH = INVENTORY_SKEW_STRENGTH
SOFT_DD_PCT = -(CAPITAL_PRESERVATION_DRAWDOWN_PCT * 100.0)
MEDIUM_DD_PCT = -(RISK_BLOCK_DRAWDOWN_PCT * 100.0)
HARD_DD_PCT = -(DRAWDOWN_PAUSE_PCT * 100.0)

NEWS_RSS_URLS = FEEDS.news_rss_urls
NEWS_LOOKBACK_HOURS = FEEDS.news_lookback_hours
NEWS_MAX_ITEMS = FEEDS.news_max_items
NEWS_POSITIVE_KEYWORDS = FEEDS.news_positive_keywords
NEWS_NEGATIVE_KEYWORDS = FEEDS.news_negative_keywords

MACRO_RSS_URLS = FEEDS.macro_rss_urls
MACRO_LOOKBACK_HOURS = FEEDS.macro_lookback_hours
MACRO_BLOCK_MINUTES = FEEDS.macro_block_minutes
MACRO_RISK_KEYWORDS = FEEDS.macro_risk_keywords
MACRO_SUPPORTIVE_KEYWORDS = FEEDS.macro_supportive_keywords

ONCHAIN_RSS_URLS = FEEDS.onchain_rss_urls
ONCHAIN_LOOKBACK_HOURS = FEEDS.onchain_lookback_hours
ONCHAIN_BULLISH_KEYWORDS = FEEDS.onchain_bullish_keywords
ONCHAIN_BEARISH_KEYWORDS = FEEDS.onchain_bearish_keywords
ONCHAIN_STRESS_KEYWORDS = FEEDS.onchain_stress_keywords

ADAPTIVE_MARKET_MAKER_ENABLED = _env_bool("ADAPTIVE_MARKET_MAKER_ENABLED", False)
ADAPTIVE_REGIME_ENABLED = _env_bool("ADAPTIVE_REGIME_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_EDGE_ENABLED = _env_bool("ADAPTIVE_EDGE_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_MODE_SELECTOR_ENABLED = _env_bool("ADAPTIVE_MODE_SELECTOR_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_DYNAMIC_QUOTING_ENABLED = _env_bool("ADAPTIVE_DYNAMIC_QUOTING_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_RISK_GOVERNOR_ENABLED = _env_bool("ADAPTIVE_RISK_GOVERNOR_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_PERFORMANCE_ADAPTATION_ENABLED = _env_bool("ADAPTIVE_PERFORMANCE_ADAPTATION_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_INVENTORY_BANDS_ENABLED = _env_bool("ADAPTIVE_INVENTORY_BANDS_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_FILL_QUALITY_ENABLED = _env_bool("ADAPTIVE_FILL_QUALITY_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_SOFT_FILTERS_ENABLED = _env_bool("ADAPTIVE_SOFT_FILTERS_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_LOGGING_ENABLED = _env_bool("ADAPTIVE_LOGGING_ENABLED", ADAPTIVE_MARKET_MAKER_ENABLED)
ADAPTIVE_MM_PROFILE = _ADAPTIVE_MM_PROFILE
ADAPTIVE_PERF_WINDOW_MINUTES = max(_env_float("ADAPTIVE_PERF_WINDOW_MINUTES", 45.0), 5.0)
ADAPTIVE_REPORT_WINDOW_MINUTES = max(_env_float("ADAPTIVE_REPORT_WINDOW_MINUTES", 60.0), 15.0)
ADAPTIVE_REGIME_BREAKOUT_BPS = _env_float("ADAPTIVE_REGIME_BREAKOUT_BPS", 28.0)
ADAPTIVE_REGIME_EVENT_VOL_MULTIPLIER = _env_float("ADAPTIVE_REGIME_EVENT_VOL_MULTIPLIER", 1.8)
ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS = _env_float("ADAPTIVE_REGIME_ILLIQUID_SPREAD_BPS", 18.0)
ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD = _env_float("ADAPTIVE_REGIME_ILLIQUID_LIQUIDITY_USD", 200.0)
ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE = _adaptive_profile_float("ADAPTIVE_EDGE_MIN_SCORE_TO_QUOTE", 12.0, 12.0)
ADAPTIVE_EDGE_STANDBY_SCORE = _adaptive_profile_float("ADAPTIVE_EDGE_STANDBY_SCORE", 8.0, 20.0)
ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE = _adaptive_profile_float("ADAPTIVE_AGGRESSIVE_MODE_MIN_EDGE", 60.0, 55.0)
ADAPTIVE_NORMAL_MODE_MIN_EDGE = _adaptive_profile_float("ADAPTIVE_NORMAL_MODE_MIN_EDGE", 50.0, 45.0)
ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE = _adaptive_profile_float("ADAPTIVE_DEFENSIVE_MODE_MIN_EDGE", 36.0, 32.0)
ADAPTIVE_REGIME_MEDIUM_CONFIDENCE = _adaptive_profile_float("ADAPTIVE_REGIME_MEDIUM_CONFIDENCE", 50.0, 42.0)
ADAPTIVE_REGIME_TREND_CONFIDENCE = _adaptive_profile_float("ADAPTIVE_REGIME_TREND_CONFIDENCE", 58.0, 48.0)
ADAPTIVE_REGIME_EXTREME_EVENT_SCORE = _adaptive_profile_float("ADAPTIVE_REGIME_EXTREME_EVENT_SCORE", 82.0, 92.0)
ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE = _adaptive_profile_float("ADAPTIVE_REGIME_SEVERE_ILLIQUID_SCORE", 84.0, 94.0)
ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_PASSIVE_MM_SPREAD_MULTIPLIER", 0.92, 0.85)
ADAPTIVE_SKEWED_MM_SPREAD_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_SKEWED_MM_SPREAD_MULTIPLIER", 1.00, 0.95)
ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_DEFENSIVE_MM_SPREAD_MULTIPLIER", 1.12, 1.15)
ADAPTIVE_TREND_ASSIST_SPREAD_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_TREND_ASSIST_SPREAD_MULTIPLIER", 0.98, 1.05)
ADAPTIVE_REBALANCE_ONLY_SPREAD_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_REBALANCE_ONLY_SPREAD_MULTIPLIER", 1.06, 1.10)
ADAPTIVE_AGGRESSIVE_SIZE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_AGGRESSIVE_SIZE_MULTIPLIER", 1.18, 1.35)
ADAPTIVE_NORMAL_SIZE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_NORMAL_SIZE_MULTIPLIER", 1.00, 1.10)
ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_DEFENSIVE_SIZE_MULTIPLIER", 0.78, 0.70)
ADAPTIVE_TREND_ASSIST_SIZE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_TREND_ASSIST_SIZE_MULTIPLIER", 1.08, 1.15)
ADAPTIVE_REBALANCE_SIZE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_REBALANCE_SIZE_MULTIPLIER", 0.92, 0.90)
ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT = _adaptive_profile_float("ADAPTIVE_RISK_SOFT_DRAWDOWN_PCT", 0.04, 0.055)
ADAPTIVE_RISK_HARD_DRAWDOWN_PCT = _adaptive_profile_float("ADAPTIVE_RISK_HARD_DRAWDOWN_PCT", 0.07, 0.095)
ADAPTIVE_RISK_KILL_DRAWDOWN_PCT = _adaptive_profile_float("ADAPTIVE_RISK_KILL_DRAWDOWN_PCT", 0.12, 0.14)
ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO = _adaptive_profile_float("ADAPTIVE_RISK_SOFT_TOXIC_FILL_RATIO", 0.40, 0.46)
ADAPTIVE_RISK_HARD_TOXIC_FILL_RATIO = _adaptive_profile_float("ADAPTIVE_RISK_HARD_TOXIC_FILL_RATIO", 0.58, 0.68)
ADAPTIVE_RISK_KILL_TOXIC_FILL_RATIO = _adaptive_profile_float("ADAPTIVE_RISK_KILL_TOXIC_FILL_RATIO", 0.82, 0.88)
ADAPTIVE_RISK_SOFT_LIQUIDITY_USD = _adaptive_profile_float("ADAPTIVE_RISK_SOFT_LIQUIDITY_USD", 220.0, 180.0)
ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT = _adaptive_profile_float("ADAPTIVE_RISK_HARD_DRAWDOWN_ACCEL_PCT", 0.035, 0.055)
ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT = _adaptive_profile_int("ADAPTIVE_RISK_HARD_TOXIC_CLUSTER_COUNT", 3, 4)
ADAPTIVE_RISK_KILL_TOXIC_CLUSTER_COUNT = _adaptive_profile_int("ADAPTIVE_RISK_KILL_TOXIC_CLUSTER_COUNT", 5, 6)
ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES = _adaptive_profile_int("ADAPTIVE_RISK_KILL_INVALID_PRICE_CYCLES", 3, 4)
ADAPTIVE_RISK_KILL_LIQUIDITY_USD = _adaptive_profile_float("ADAPTIVE_RISK_KILL_LIQUIDITY_USD", 110.0, 80.0)
ADAPTIVE_INVENTORY_NEUTRAL_MIN = _adaptive_profile_float("ADAPTIVE_INVENTORY_NEUTRAL_MIN", 0.45, 0.45)
ADAPTIVE_INVENTORY_NEUTRAL_MAX = _adaptive_profile_float("ADAPTIVE_INVENTORY_NEUTRAL_MAX", 0.55, 0.55)
ADAPTIVE_INVENTORY_SOFT_MIN = _adaptive_profile_float("ADAPTIVE_INVENTORY_SOFT_MIN", 0.40, 0.40)
ADAPTIVE_INVENTORY_SOFT_MAX = _adaptive_profile_float("ADAPTIVE_INVENTORY_SOFT_MAX", 0.60, 0.60)
ADAPTIVE_INVENTORY_STRONG_MIN = _adaptive_profile_float("ADAPTIVE_INVENTORY_STRONG_MIN", 0.35, 0.35)
ADAPTIVE_INVENTORY_STRONG_MAX = _adaptive_profile_float("ADAPTIVE_INVENTORY_STRONG_MAX", 0.65, 0.65)
ADAPTIVE_INVENTORY_HARD_MIN = _adaptive_profile_float("ADAPTIVE_INVENTORY_HARD_MIN", 0.34, 0.30)
ADAPTIVE_INVENTORY_HARD_MAX = _adaptive_profile_float("ADAPTIVE_INVENTORY_HARD_MAX", 0.66, 0.70)
ADAPTIVE_MILD_TREND_SKEW_STRENGTH = _adaptive_profile_float("ADAPTIVE_MILD_TREND_SKEW_STRENGTH", 1.15, 1.25)
ADAPTIVE_STRONG_TREND_SKEW_STRENGTH = _adaptive_profile_float("ADAPTIVE_STRONG_TREND_SKEW_STRENGTH", 1.35, 1.50)
ADAPTIVE_REBALANCE_SKEW_STRENGTH = _adaptive_profile_float("ADAPTIVE_REBALANCE_SKEW_STRENGTH", 1.55, 1.75)
ADAPTIVE_ADVERSE_WIDEN_THRESHOLD = _adaptive_profile_float("ADAPTIVE_ADVERSE_WIDEN_THRESHOLD", 0.28, 0.30)
ADAPTIVE_ADVERSE_WIDEN_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_ADVERSE_WIDEN_MULTIPLIER", 1.05, 1.03)
ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD = _adaptive_profile_float("ADAPTIVE_ADVERSE_SIZE_REDUCE_THRESHOLD", 0.46, 0.55)
ADAPTIVE_ADVERSE_SIZE_REDUCE_MULTIPLIER = _adaptive_profile_float("ADAPTIVE_ADVERSE_SIZE_REDUCE_MULTIPLIER", 0.90, 0.88)
ADAPTIVE_ADVERSE_DEFENSIVE_THRESHOLD = _adaptive_profile_float("ADAPTIVE_ADVERSE_DEFENSIVE_THRESHOLD", 0.64, 0.75)
ADAPTIVE_PERF_SPREAD_LOWER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SPREAD_LOWER_BOUND", 0.90, 0.86)
ADAPTIVE_PERF_SPREAD_UPPER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SPREAD_UPPER_BOUND", 1.18, 1.16)
ADAPTIVE_PERF_SIZE_CAP_LOWER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SIZE_CAP_LOWER_BOUND", 0.60, 0.70)
ADAPTIVE_PERF_SIZE_CAP_UPPER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SIZE_CAP_UPPER_BOUND", 1.10, 1.20)
ADAPTIVE_PERF_EDGE_THRESHOLD_LOWER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_EDGE_THRESHOLD_LOWER_BOUND", 0.88, 0.84)
ADAPTIVE_PERF_EDGE_THRESHOLD_UPPER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_EDGE_THRESHOLD_UPPER_BOUND", 1.22, 1.12)
ADAPTIVE_PERF_SKEW_LOWER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SKEW_LOWER_BOUND", 0.92, 0.95)
ADAPTIVE_PERF_SKEW_UPPER_BOUND = _adaptive_profile_float("ADAPTIVE_PERF_SKEW_UPPER_BOUND", 1.10, 1.16)


def has_env_value(name: str) -> bool:
    return _env_has_value(name)
