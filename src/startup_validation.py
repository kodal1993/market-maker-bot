from __future__ import annotations

from config import CORE, EXECUTION, MARKET, MEV_EXECUTION, TELEGRAM, WALLET, has_env_value
from config_models import CoreConfig, ExecutionConfig, MarketConfig, MevExecutionConfig, TelegramConfig, WalletConfig


def collect_startup_validation_errors(
    *,
    core: CoreConfig,
    execution: ExecutionConfig,
    market: MarketConfig,
    mev_execution: MevExecutionConfig,
    telegram: TelegramConfig,
    wallet: WalletConfig,
    env_has_value_fn=has_env_value,
) -> list[str]:
    errors: list[str] = []

    def require_env(name: str, reason: str | None = None) -> None:
        if env_has_value_fn(name):
            return
        errors.append(reason or f"Missing required setting: {name}")

    require_env("BOT_MODE")
    require_env("CHAIN")
    require_env("RPC_TIMEOUT_SEC")
    require_env("RPC_MAX_RETRIES")
    require_env("RPC_RETRY_BACKOFF_SEC")
    require_env("ACCOUNT_REFERENCE_MODE")
    require_env("TRADE_SIZE_PCT")
    require_env("MAX_POSITION_PCT")
    require_env("MAX_TRADE_SIZE_PCT")
    require_env("FORCE_TRADE_SIZE_PCT")
    require_env("TARGET_BASE_PCT")
    require_env("TARGET_QUOTE_PCT")
    require_env("MIN_NOTIONAL_USD")
    require_env("MIN_BASE_RESERVE_PCT")
    require_env("MIN_QUOTE_RESERVE_PCT")
    require_env("MAX_DAILY_LOSS_USD")
    require_env("MAX_EXPOSURE_USD")
    require_env("KILL_SWITCH_USD")
    require_env("MAX_GAS_SPIKE_GWEI")
    require_env("ESTIMATED_SWAP_GAS_UNITS")
    require_env("MAX_GAS_TO_PROFIT_RATIO")
    require_env("EXECUTION_TIMEFRAME_SECONDS")
    require_env("TREND_FILTER_TIMEFRAME_SECONDS")
    require_env("ENABLE_TREND_TIMEFRAME_FILTER")
    require_env("ENABLE_EXECUTION_CONFIRMATION")
    require_env("CONFIRMATION_TIMEFRAME_SECONDS")
    require_env("CONFIRMATION_MOMENTUM_SHOCK_BPS")
    require_env("TELEGRAM_ENABLED")
    require_env("TELEGRAM_POLL_COMMANDS")
    require_env("TELEGRAM_API_TIMEOUT_SEC")
    require_env("TELEGRAM_API_MAX_RETRIES")
    require_env("TELEGRAM_RATE_LIMIT_SECONDS")
    require_env("ENABLE_PRIVATE_TX")
    require_env("PRIVATE_TX_TIMEOUT_SEC")
    require_env("PRIVATE_TX_MAX_RETRIES")

    if not core.rpc_urls:
        errors.append("At least one RPC endpoint is required: set RPC_URL or RPC_URLS")
    if core.rpc_timeout_sec <= 0:
        errors.append("RPC_TIMEOUT_SEC must be greater than 0")
    if core.rpc_max_retries < 0:
        errors.append("RPC_MAX_RETRIES must be 0 or greater")
    if core.rpc_retry_backoff_sec <= 0:
        errors.append("RPC_RETRY_BACKOFF_SEC must be greater than 0")

    mode = execution.account_reference_mode.strip().lower()
    if mode not in {"dynamic", "override", "fixed"}:
        errors.append("ACCOUNT_REFERENCE_MODE must be one of: dynamic, override, fixed")
    if execution.trade_size_pct <= 0:
        errors.append("TRADE_SIZE_PCT must be greater than 0")
    if execution.max_position_pct <= 0:
        errors.append("MAX_POSITION_PCT must be greater than 0")
    if execution.max_trade_size_pct <= 0:
        errors.append("MAX_TRADE_SIZE_PCT must be greater than 0")
    if execution.max_trade_size_pct > execution.max_position_pct:
        errors.append("MAX_TRADE_SIZE_PCT must be less than or equal to MAX_POSITION_PCT")
    if execution.force_trade_size_pct < 0:
        errors.append("FORCE_TRADE_SIZE_PCT must be 0 or greater")
    if execution.target_base_pct < 0 or execution.target_quote_pct < 0:
        errors.append("TARGET_BASE_PCT and TARGET_QUOTE_PCT must be 0 or greater")
    if (execution.target_base_pct + execution.target_quote_pct) <= 0:
        errors.append("TARGET_BASE_PCT and TARGET_QUOTE_PCT must sum to more than 0")
    if execution.min_notional_usd <= 0:
        errors.append("MIN_NOTIONAL_USD must be greater than 0")
    if execution.min_base_reserve_pct < 0 or execution.min_base_reserve_pct >= 1:
        errors.append("MIN_BASE_RESERVE_PCT must be between 0 and 1")
    if execution.min_quote_reserve_pct < 0 or execution.min_quote_reserve_pct >= 1:
        errors.append("MIN_QUOTE_RESERVE_PCT must be between 0 and 1")
    if execution.account_size_override < 0:
        errors.append("ACCOUNT_SIZE_OVERRIDE must be 0 or greater")
    if execution.max_daily_loss_usd <= 0:
        errors.append("MAX_DAILY_LOSS_USD must be greater than 0")
    if execution.max_exposure_usd <= 0:
        errors.append("MAX_EXPOSURE_USD must be greater than 0")
    if execution.kill_switch_usd >= 0:
        errors.append("KILL_SWITCH_USD must be negative")

    if market.execution_timeframe_seconds <= 0:
        errors.append("EXECUTION_TIMEFRAME_SECONDS must be greater than 0")
    if market.trend_filter_timeframe_seconds <= 0:
        errors.append("TREND_FILTER_TIMEFRAME_SECONDS must be greater than 0")
    if market.confirmation_timeframe_seconds <= 0:
        errors.append("CONFIRMATION_TIMEFRAME_SECONDS must be greater than 0")
    if market.confirmation_momentum_shock_bps <= 0:
        errors.append("CONFIRMATION_MOMENTUM_SHOCK_BPS must be greater than 0")
    if market.enable_trend_timeframe_filter and market.trend_filter_timeframe_seconds < market.execution_timeframe_seconds:
        errors.append("TREND_FILTER_TIMEFRAME_SECONDS must be greater than or equal to EXECUTION_TIMEFRAME_SECONDS")
    if market.enable_execution_confirmation and market.confirmation_timeframe_seconds > market.execution_timeframe_seconds:
        errors.append("CONFIRMATION_TIMEFRAME_SECONDS must be less than or equal to EXECUTION_TIMEFRAME_SECONDS")

    if mev_execution.max_gas_spike_gwei <= 0:
        errors.append("MAX_GAS_SPIKE_GWEI must be greater than 0")
    if mev_execution.estimated_swap_gas_units <= 0:
        errors.append("ESTIMATED_SWAP_GAS_UNITS must be greater than 0")
    if mev_execution.max_gas_to_profit_ratio <= 0:
        errors.append("MAX_GAS_TO_PROFIT_RATIO must be greater than 0")
    if mev_execution.private_tx_timeout_sec <= 0:
        errors.append("PRIVATE_TX_TIMEOUT_SEC must be greater than 0")
    if mev_execution.private_tx_max_retries < 0:
        errors.append("PRIVATE_TX_MAX_RETRIES must be 0 or greater")

    if telegram.api_timeout_sec <= 0:
        errors.append("TELEGRAM_API_TIMEOUT_SEC must be greater than 0")
    if telegram.api_max_retries < 0:
        errors.append("TELEGRAM_API_MAX_RETRIES must be 0 or greater")
    if telegram.rate_limit_seconds < 0:
        errors.append("TELEGRAM_RATE_LIMIT_SECONDS must be 0 or greater")

    if telegram.enabled:
        require_env(
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED=true",
        )
        if not telegram.bot_token.strip():
            errors.append("TELEGRAM_BOT_TOKEN must not be empty when TELEGRAM_ENABLED=true")
        if not telegram.chat_id.strip() and not telegram.poll_commands:
            errors.append(
                "TELEGRAM_CHAT_ID is required when TELEGRAM_ENABLED=true and TELEGRAM_POLL_COMMANDS=false",
            )

    live_mode = not core.bot_mode.strip().lower().startswith("paper")
    if live_mode and mev_execution.enable_private_tx:
        require_env(
            "WALLET_PRIVATE_KEY",
            "WALLET_PRIVATE_KEY is required when ENABLE_PRIVATE_TX=true in live mode",
        )
        require_env(
            "WALLET_ADDRESS",
            "WALLET_ADDRESS is required when ENABLE_PRIVATE_TX=true in live mode",
        )
        if not mev_execution.private_rpc_urls:
            errors.append(
                "At least one private RPC endpoint is required: set PRIVATE_RPC_URL or PRIVATE_RPC_URLS when ENABLE_PRIVATE_TX=true in live mode",
            )
        if not wallet.wallet_private_key.strip():
            errors.append("WALLET_PRIVATE_KEY must not be empty when ENABLE_PRIVATE_TX=true in live mode")
        if not wallet.wallet_address.strip():
            errors.append("WALLET_ADDRESS must not be empty when ENABLE_PRIVATE_TX=true in live mode")

    return errors


def validate_startup_config() -> list[str]:
    return collect_startup_validation_errors(
        core=CORE,
        execution=EXECUTION,
        market=MARKET,
        mev_execution=MEV_EXECUTION,
        telegram=TELEGRAM,
        wallet=WALLET,
    )


def main() -> int:
    errors = validate_startup_config()
    if not errors:
        print("Startup config validation passed.")
        return 0

    print("Startup config validation failed:")
    for error in errors:
        print(f" - {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
