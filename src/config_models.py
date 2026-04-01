from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreConfig:
    bot_mode: str
    chain: str
    rpc_url: str
    rpc_urls: list[str]
    rpc_timeout_sec: float
    rpc_max_retries: int
    rpc_retry_backoff_sec: float


@dataclass(frozen=True)
class WalletConfig:
    wallet_private_key: str
    wallet_address: str


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    poll_commands: bool
    daily_report_enabled: bool
    daily_report_hour: int
    api_timeout_sec: float
    api_max_retries: int
    rate_limit_seconds: float


@dataclass(frozen=True)
class PortfolioConfig:
    start_usdc: float
    start_eth: float
    start_eth_usd: float
    max_inventory_usd: float
    min_order_size_usd: float
    inventory_skew_strength: float


@dataclass(frozen=True)
class ExecutionConfig:
    trade_size_usd: float
    max_trade_size_usd: float
    account_reference_mode: str
    trade_size_pct: float
    max_position_pct: float
    max_trade_size_pct: float
    force_trade_size_pct: float
    target_base_pct: float
    target_quote_pct: float
    min_notional_usd: float
    min_base_reserve_pct: float
    min_quote_reserve_pct: float
    account_size_override: float
    max_daily_loss_usd: float
    max_exposure_usd: float
    loop_seconds: float
    max_loops: int
    maker_fee_bps: float
    taker_fee_bps: float
    kill_switch_usd: float
    trades_csv: str
    equity_csv: str
    price_cache_seconds: float
    sqlite_log_path: str


@dataclass(frozen=True)
class MarketConfig:
    spread_bps: float
    min_spread_bps: float
    max_spread_bps: float
    twap_window: int
    vol_window: int
    vol_multiplier: float
    short_ma_window: int
    long_ma_window: int
    execution_timeframe_seconds: float
    trend_filter_timeframe_seconds: float
    enable_trend_timeframe_filter: bool
    enable_execution_confirmation: bool
    confirmation_timeframe_seconds: float
    confirmation_momentum_shock_bps: float
    price_bootstrap_rows: int
    price_history_max_age_seconds: float
    trend_threshold: float
    high_vol_threshold: float


@dataclass(frozen=True)
class RegimeTuningConfig:
    default_regime: str
    trend_override_only_if_strong: bool
    range_priority_when_unclear: bool
    trend_lookback_bars: int
    trend_min_net_move_pct: float
    trend_min_same_direction_bars: int
    trend_min_distance_from_vwap_pct: float
    trend_momentum_zscore_min: float
    range_lookback_bars: int
    range_max_net_move_pct: float
    range_max_distance_from_vwap_pct: float
    range_structure_required: bool
    range_structure_tolerance_pct: float
    shock_bar_move_pct: float
    max_wick_to_body_ratio: float
    max_spread_multiplier_vs_median: float
    post_shock_cooldown_bars: int


@dataclass(frozen=True)
class RangeStrategyConfig:
    range_window_bars: int
    range_top_zone_pct: float
    range_bottom_zone_pct: float
    range_mid_no_trade_zone_pct: float
    entry_threshold_bps: float
    min_edge_bps: float
    take_profit_bps: float
    soft_take_profit_bps: float
    max_hold_minutes: float
    time_stop_minutes: float
    exit_on_reversion_to_mid: bool


@dataclass(frozen=True)
class TrendStrategyConfig:
    entry_threshold_bps: float
    min_edge_bps: float
    take_profit_bps: float
    soft_take_profit_bps: float
    max_hold_minutes: float
    time_stop_minutes: float


@dataclass(frozen=True)
class ExecutionTuningConfig:
    base_entry_threshold_bps: float
    requote_interval_ms: int
    stale_quote_timeout_ms: int
    cancel_if_far_from_mid_bps: float
    reprice_on_mid_move_bps: float
    max_position_hold_minutes: float
    force_exit_if_unrealized_loss_bps: float
    force_exit_if_regime_flip: bool
    force_exit_if_inventory_pressure: bool


@dataclass(frozen=True)
class InventoryTuningConfig:
    inventory_target_pct: float
    inventory_neutral_band_pct: float
    inventory_soft_limit_pct: float
    inventory_hard_limit_pct: float
    inventory_force_reduce_pct: float
    same_side_entry_penalty_bps: float
    opposite_side_entry_bonus_bps: float
    block_same_side_entries_above_hard_limit: bool
    allow_reduce_only_above_hard_limit: bool
    force_inventory_reduction_above_pct: float
    forced_reduce_aggression_bps: float


@dataclass(frozen=True)
class SizingTuningConfig:
    base_order_size_usd: float
    range_order_size_multiplier: float
    trend_order_size_multiplier: float
    high_vol_order_size_multiplier: float
    inventory_pressure_size_multiplier: float


@dataclass(frozen=True)
class VolatilityTuningConfig:
    low_vol_atr_pct_max: float
    mid_vol_atr_pct_max: float
    high_vol_atr_pct_min: float
    low_vol_entry_threshold_bps: float
    mid_vol_entry_threshold_bps: float
    high_vol_entry_threshold_bps: float
    low_vol_take_profit_bps: float
    mid_vol_take_profit_bps: float
    high_vol_take_profit_bps: float
    low_vol_max_hold_minutes: float
    mid_vol_max_hold_minutes: float
    high_vol_max_hold_minutes: float


@dataclass(frozen=True)
class ActivityTuningConfig:
    activity_window_hours: float
    min_trades_per_activity_window: int
    daily_min_trade_target: int
    daily_good_trade_target: int
    daily_aggressive_trade_cap: int
    auto_loosen_if_low_activity: bool
    auto_loosen_entry_bps: float
    auto_loosen_min_edge_bps: float
    prioritize_range_mode_when_low_activity: bool


@dataclass(frozen=True)
class TrendConfig:
    trend_size_multiplier: float
    range_size_multiplier: float
    trend_buy_target_pct: float
    max_trend_chase_bps: float
    max_trend_pullback_bps: float
    trend_buy_min_market_score: float
    trend_buy_min_signal_score: float
    trend_buy_min_confidence: float
    trend_buy_min_long_buffer_bps: float
    trend_buy_min_strength_multiplier: float
    trend_buy_requote_bps: float
    range_spread_tightening: float
    range_directional_bias_factor: float
    trend_directional_bias_factor: float
    range_target_inventory_min: float
    range_target_inventory_max: float
    trend_target_inventory_min: float
    trend_target_inventory_max: float
    risk_off_target_inventory_max: float
    caution_target_inventory_cap: float
    side_flip_cooldown_cycles: int
    side_flip_min_bps: float
    trend_sell_spread_factor: float
    trend_buy_fill_bonus: float
    trend_sell_fill_bonus: float
    trend_overweight_unwind_fraction: float
    trend_overweight_max_multiplier: float
    min_sell_profit_bps: float
    max_exit_premium_bps: float


@dataclass(frozen=True)
class ReentryConfig:
    enabled: bool
    inventory_buffer_pct: float
    wait_reentry_pullback_pct: float
    zone_1_multiplier: float
    zone_2_multiplier: float
    zone_3_multiplier: float
    zone_1_buy_fraction: float
    zone_2_buy_fraction: float
    zone_3_buy_fraction: float
    timeout_minutes: float
    timeout_buy_fraction: float
    runaway_buy_fraction: float
    max_miss_reentry_pct: float
    max_miss_buy_fraction: float
    rsi_period: int
    rsi_buy_threshold: float
    rsi_turn_margin: float
    momentum_lookback: int
    stop_loss_pct: float
    profit_lock_level_1_bps: float
    range_profit_lock_level_1_bps: float
    profit_lock_level_1_sell_fraction: float
    profit_lock_level_2_bps: float
    range_profit_lock_level_2_bps: float
    profit_lock_level_2_sell_fraction: float
    micro_trailing_pullback_bps: float
    eth_accumulation_reinvest_pct: float
    eth_preservation_floor_multiplier: float
    partial_reset_usdc_threshold_pct: float
    partial_reset_buy_fraction: float


@dataclass(frozen=True)
class ExecutionPolicyConfig:
    enabled: bool
    min_expected_profit_pct: float
    maker_slippage_bps: float
    taker_slippage_bps: float
    slippage_size_factor: float


@dataclass(frozen=True)
class MevExecutionConfig:
    enable_private_tx: bool
    private_rpc_url: str
    private_rpc_urls: list[str]
    private_tx_timeout_sec: float
    private_tx_max_retries: int
    enable_cow: bool
    cow_min_notional_usd: float
    cow_supported_pairs: list[str]
    enable_order_slicing: bool
    max_single_swap_usd: float
    slice_count_max: int
    slice_delay_ms: int
    max_quote_deviation_bps: float
    max_twap_deviation_bps: float
    max_price_impact_bps: float
    max_slippage_bps: float
    max_gas_spike_gwei: float
    estimated_swap_gas_units: int
    max_gas_to_profit_ratio: float
    mev_risk_threshold_block: float
    public_swap_max_risk: float
    execution_policy_profile: str
    mev_policy_path: str


@dataclass(frozen=True)
class TradeFilterConfig:
    enabled: bool
    momentum_limit_low_vol_bps: float
    momentum_limit_mid_vol_bps: float
    momentum_limit_high_vol_bps: float
    buy_rsi_max: float
    sell_rsi_min: float
    loss_streak_limit: int
    min_trade_distance_pct: float
    cooldown_minutes: float
    min_time_between_trades_minutes: float
    max_trades_per_day: int
    trend_against_score: float
    strong_trend_score: float
    strong_trend_skip_score: float
    strong_trend_size_multiplier: float
    force_trade_minutes: float
    force_trade_size_fraction: float
    min_trades_per_hour: float
    debug_mode: bool


@dataclass(frozen=True)
class InventoryManagerConfig:
    enabled: bool
    normal_min: float
    normal_max: float
    uptrend_min: float
    uptrend_max: float
    downtrend_min: float
    downtrend_max: float


@dataclass(frozen=True)
class StateMachineConfig:
    enabled: bool
    loss_streak_limit: int
    cooldown_minutes: float
    accumulating_failsafe_minutes: float


@dataclass(frozen=True)
class DecisionEngineConfig:
    enabled: bool


@dataclass(frozen=True)
class DexConfig:
    uniswap_v3_factory: str
    base_weth: str
    base_usdc: str
    uniswap_pool_fee: int


@dataclass(frozen=True)
class IntelligenceConfig:
    signal_cache_seconds: float
    signal_http_timeout_seconds: float
    signal_fetch_enabled: bool
    signal_caution_threshold: float
    signal_block_threshold: float
    signal_block_risk_threshold: float
    caution_spread_multiplier: float
    caution_size_multiplier: float
    intelligence_warmup_rows: int
    low_vol_threshold_multiplier: float
    extreme_vol_threshold_multiplier: float
    risk_off_trend_multiplier: float
    overweight_exit_buffer_pct: float
    capital_preservation_drawdown_pct: float
    risk_block_drawdown_pct: float
    drawdown_pause_pct: float
    adaptive_lookback_cycles: int
    min_inventory_multiplier: float
    max_inventory_multiplier: float
    min_trade_size_multiplier: float
    max_trade_size_multiplier: float
    min_spread_multiplier: float
    max_spread_multiplier: float


@dataclass(frozen=True)
class FeedConfig:
    news_rss_urls: list[str]
    news_lookback_hours: float
    news_max_items: int
    news_positive_keywords: list[str]
    news_negative_keywords: list[str]
    macro_rss_urls: list[str]
    macro_lookback_hours: float
    macro_block_minutes: float
    macro_risk_keywords: list[str]
    macro_supportive_keywords: list[str]
    onchain_rss_urls: list[str]
    onchain_lookback_hours: float
    onchain_bullish_keywords: list[str]
    onchain_bearish_keywords: list[str]
    onchain_stress_keywords: list[str]
