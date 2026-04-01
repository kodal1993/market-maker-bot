from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config_models import CoreConfig, ExecutionConfig, MarketConfig, MevExecutionConfig, TelegramConfig, WalletConfig
from startup_validation import collect_startup_validation_errors


BASE_ENV_VARS = {
    "BOT_MODE",
    "CHAIN",
    "RPC_URL",
    "RPC_TIMEOUT_SEC",
    "RPC_MAX_RETRIES",
    "RPC_RETRY_BACKOFF_SEC",
    "ACCOUNT_REFERENCE_MODE",
    "TRADE_SIZE_PCT",
    "MAX_POSITION_PCT",
    "MAX_TRADE_SIZE_PCT",
    "FORCE_TRADE_SIZE_PCT",
    "TARGET_BASE_PCT",
    "TARGET_QUOTE_PCT",
    "MIN_NOTIONAL_USD",
    "MIN_BASE_RESERVE_PCT",
    "MIN_QUOTE_RESERVE_PCT",
    "MAX_DAILY_LOSS_USD",
    "MAX_EXPOSURE_USD",
    "KILL_SWITCH_USD",
    "MAX_GAS_SPIKE_GWEI",
    "ESTIMATED_SWAP_GAS_UNITS",
    "MAX_GAS_TO_PROFIT_RATIO",
    "EXECUTION_TIMEFRAME_SECONDS",
    "TREND_FILTER_TIMEFRAME_SECONDS",
    "ENABLE_TREND_TIMEFRAME_FILTER",
    "ENABLE_EXECUTION_CONFIRMATION",
    "CONFIRMATION_TIMEFRAME_SECONDS",
    "CONFIRMATION_MOMENTUM_SHOCK_BPS",
    "TELEGRAM_ENABLED",
    "TELEGRAM_POLL_COMMANDS",
    "TELEGRAM_API_TIMEOUT_SEC",
    "TELEGRAM_API_MAX_RETRIES",
    "TELEGRAM_RATE_LIMIT_SECONDS",
    "ENABLE_PRIVATE_TX",
    "PRIVATE_TX_TIMEOUT_SEC",
    "PRIVATE_TX_MAX_RETRIES",
}


def build_core(*, bot_mode: str = "paper") -> CoreConfig:
    return CoreConfig(
        bot_mode=bot_mode,
        chain="base",
        rpc_url="https://rpc.example",
        rpc_urls=["https://rpc.example"],
        rpc_timeout_sec=10.0,
        rpc_max_retries=3,
        rpc_retry_backoff_sec=2.0,
    )


def build_execution() -> ExecutionConfig:
    return ExecutionConfig(
        trade_size_usd=30.0,
        max_trade_size_usd=60.0,
        account_reference_mode="dynamic",
        trade_size_pct=0.10,
        max_position_pct=0.25,
        max_trade_size_pct=0.15,
        force_trade_size_pct=0.03,
        target_base_pct=0.50,
        target_quote_pct=0.50,
        min_notional_usd=10.0,
        min_base_reserve_pct=0.05,
        min_quote_reserve_pct=0.05,
        account_size_override=0.0,
        max_daily_loss_usd=25.0,
        max_exposure_usd=140.0,
        loop_seconds=6.0,
        max_loops=120,
        maker_fee_bps=2.0,
        taker_fee_bps=5.0,
        kill_switch_usd=-10.0,
        trades_csv=r"logs\trades.csv",
        equity_csv=r"logs\equity.csv",
        price_cache_seconds=10.0,
        sqlite_log_path=r"logs\trading.sqlite",
    )


def build_mev_execution(*, enable_private_tx: bool = False) -> MevExecutionConfig:
    return MevExecutionConfig(
        enable_private_tx=enable_private_tx,
        private_rpc_url="https://private-rpc.example" if enable_private_tx else "",
        private_rpc_urls=["https://private-rpc.example"] if enable_private_tx else [],
        private_tx_timeout_sec=8.0,
        private_tx_max_retries=2,
        enable_cow=False,
        cow_min_notional_usd=150.0,
        cow_supported_pairs=["WETH/USDC"],
        enable_order_slicing=False,
        max_single_swap_usd=125.0,
        slice_count_max=4,
        slice_delay_ms=250,
        max_quote_deviation_bps=35.0,
        max_twap_deviation_bps=55.0,
        max_price_impact_bps=45.0,
        max_slippage_bps=40.0,
        max_gas_spike_gwei=35.0,
        estimated_swap_gas_units=5000,
        max_gas_to_profit_ratio=1.0,
        mev_risk_threshold_block=70.0,
        public_swap_max_risk=40.0,
        execution_policy_profile="balanced",
        mev_policy_path="mev_policy.yaml",
    )


def build_market(
    *,
    execution_timeframe_seconds: float = 300.0,
    trend_filter_timeframe_seconds: float = 900.0,
    enable_trend_timeframe_filter: bool = True,
    enable_execution_confirmation: bool = False,
    confirmation_timeframe_seconds: float = 60.0,
    confirmation_momentum_shock_bps: float = 18.0,
) -> MarketConfig:
    return MarketConfig(
        spread_bps=9.0,
        min_spread_bps=3.0,
        max_spread_bps=18.0,
        twap_window=5,
        vol_window=10,
        vol_multiplier=0.55,
        short_ma_window=9,
        long_ma_window=21,
        execution_timeframe_seconds=execution_timeframe_seconds,
        trend_filter_timeframe_seconds=trend_filter_timeframe_seconds,
        enable_trend_timeframe_filter=enable_trend_timeframe_filter,
        enable_execution_confirmation=enable_execution_confirmation,
        confirmation_timeframe_seconds=confirmation_timeframe_seconds,
        confirmation_momentum_shock_bps=confirmation_momentum_shock_bps,
        price_bootstrap_rows=21,
        price_history_max_age_seconds=1800.0,
        trend_threshold=0.00012,
        high_vol_threshold=0.002,
    )


def build_telegram(
    *,
    enabled: bool = False,
    bot_token: str = "",
    chat_id: str = "",
    poll_commands: bool = True,
) -> TelegramConfig:
    return TelegramConfig(
        enabled=enabled,
        bot_token=bot_token,
        chat_id=chat_id,
        poll_commands=poll_commands,
        daily_report_enabled=True,
        daily_report_hour=20,
        api_timeout_sec=10.0,
        api_max_retries=3,
        rate_limit_seconds=1.0,
    )


def build_wallet(*, present: bool = False) -> WalletConfig:
    return WalletConfig(
        wallet_private_key="secret" if present else "",
        wallet_address="0xabc" if present else "",
    )


class StartupValidationTests(unittest.TestCase):
    def test_valid_paper_configuration_passes(self) -> None:
        errors = collect_startup_validation_errors(
            core=build_core(),
            execution=build_execution(),
            market=build_market(),
            mev_execution=build_mev_execution(enable_private_tx=False),
            telegram=build_telegram(),
            wallet=build_wallet(),
            env_has_value_fn=lambda name: name in BASE_ENV_VARS,
        )

        self.assertEqual(errors, [])

    def test_missing_required_rpc_setting_blocks_startup(self) -> None:
        core = build_core()
        core = CoreConfig(
            bot_mode=core.bot_mode,
            chain=core.chain,
            rpc_url="",
            rpc_urls=[],
            rpc_timeout_sec=core.rpc_timeout_sec,
            rpc_max_retries=core.rpc_max_retries,
            rpc_retry_backoff_sec=core.rpc_retry_backoff_sec,
        )
        errors = collect_startup_validation_errors(
            core=core,
            execution=build_execution(),
            market=build_market(),
            mev_execution=build_mev_execution(enable_private_tx=False),
            telegram=build_telegram(),
            wallet=build_wallet(),
            env_has_value_fn=lambda name: name in (BASE_ENV_VARS - {"RPC_URL"}),
        )

        self.assertIn("At least one RPC endpoint is required: set RPC_URL or RPC_URLS", errors)

    def test_telegram_enabled_requires_token_and_delivery_path(self) -> None:
        errors = collect_startup_validation_errors(
            core=build_core(),
            execution=build_execution(),
            market=build_market(),
            mev_execution=build_mev_execution(enable_private_tx=False),
            telegram=build_telegram(enabled=True, bot_token="", chat_id="", poll_commands=False),
            wallet=build_wallet(),
            env_has_value_fn=lambda name: name in BASE_ENV_VARS,
        )

        self.assertIn("TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED=true", errors)
        self.assertIn("TELEGRAM_BOT_TOKEN must not be empty when TELEGRAM_ENABLED=true", errors)
        self.assertIn(
            "TELEGRAM_CHAT_ID is required when TELEGRAM_ENABLED=true and TELEGRAM_POLL_COMMANDS=false",
            errors,
        )

    def test_live_private_tx_requires_private_rpc_and_wallet(self) -> None:
        mev_execution = build_mev_execution(enable_private_tx=True)
        mev_execution = MevExecutionConfig(
            enable_private_tx=mev_execution.enable_private_tx,
            private_rpc_url="",
            private_rpc_urls=[],
            private_tx_timeout_sec=mev_execution.private_tx_timeout_sec,
            private_tx_max_retries=mev_execution.private_tx_max_retries,
            enable_cow=mev_execution.enable_cow,
            cow_min_notional_usd=mev_execution.cow_min_notional_usd,
            cow_supported_pairs=mev_execution.cow_supported_pairs,
            enable_order_slicing=mev_execution.enable_order_slicing,
            max_single_swap_usd=mev_execution.max_single_swap_usd,
            slice_count_max=mev_execution.slice_count_max,
            slice_delay_ms=mev_execution.slice_delay_ms,
            max_quote_deviation_bps=mev_execution.max_quote_deviation_bps,
            max_twap_deviation_bps=mev_execution.max_twap_deviation_bps,
            max_price_impact_bps=mev_execution.max_price_impact_bps,
            max_slippage_bps=mev_execution.max_slippage_bps,
            max_gas_spike_gwei=mev_execution.max_gas_spike_gwei,
            estimated_swap_gas_units=mev_execution.estimated_swap_gas_units,
            max_gas_to_profit_ratio=mev_execution.max_gas_to_profit_ratio,
            mev_risk_threshold_block=mev_execution.mev_risk_threshold_block,
            public_swap_max_risk=mev_execution.public_swap_max_risk,
            execution_policy_profile=mev_execution.execution_policy_profile,
            mev_policy_path=mev_execution.mev_policy_path,
        )
        errors = collect_startup_validation_errors(
            core=build_core(bot_mode="live"),
            execution=build_execution(),
            market=build_market(),
            mev_execution=mev_execution,
            telegram=build_telegram(),
            wallet=build_wallet(present=False),
            env_has_value_fn=lambda name: name in BASE_ENV_VARS,
        )

        self.assertIn(
            "At least one private RPC endpoint is required: set PRIVATE_RPC_URL or PRIVATE_RPC_URLS when ENABLE_PRIVATE_TX=true in live mode",
            errors,
        )
        self.assertIn("WALLET_PRIVATE_KEY is required when ENABLE_PRIVATE_TX=true in live mode", errors)
        self.assertIn("WALLET_ADDRESS is required when ENABLE_PRIVATE_TX=true in live mode", errors)

    def test_invalid_risk_limit_configuration_blocks_startup(self) -> None:
        execution = ExecutionConfig(
            trade_size_usd=30.0,
            max_trade_size_usd=20.0,
            account_reference_mode="dynamic",
            trade_size_pct=0.10,
            max_position_pct=0.20,
            max_trade_size_pct=0.25,
            force_trade_size_pct=0.03,
            target_base_pct=0.0,
            target_quote_pct=0.0,
            min_notional_usd=0.0,
            min_base_reserve_pct=1.2,
            min_quote_reserve_pct=-0.1,
            account_size_override=-1.0,
            max_daily_loss_usd=0.0,
            max_exposure_usd=0.0,
            loop_seconds=6.0,
            max_loops=120,
            maker_fee_bps=2.0,
            taker_fee_bps=5.0,
            kill_switch_usd=-10.0,
            trades_csv=r"logs\trades.csv",
            equity_csv=r"logs\equity.csv",
            price_cache_seconds=10.0,
            sqlite_log_path=r"logs\trading.sqlite",
        )
        errors = collect_startup_validation_errors(
            core=build_core(),
            execution=execution,
            market=build_market(),
            mev_execution=build_mev_execution(enable_private_tx=False),
            telegram=build_telegram(),
            wallet=build_wallet(),
            env_has_value_fn=lambda name: name in BASE_ENV_VARS,
        )

        self.assertIn("MAX_TRADE_SIZE_PCT must be less than or equal to MAX_POSITION_PCT", errors)
        self.assertIn("TARGET_BASE_PCT and TARGET_QUOTE_PCT must sum to more than 0", errors)
        self.assertIn("MIN_NOTIONAL_USD must be greater than 0", errors)
        self.assertIn("MIN_BASE_RESERVE_PCT must be between 0 and 1", errors)
        self.assertIn("MIN_QUOTE_RESERVE_PCT must be between 0 and 1", errors)
        self.assertIn("ACCOUNT_SIZE_OVERRIDE must be 0 or greater", errors)
        self.assertIn("MAX_DAILY_LOSS_USD must be greater than 0", errors)
        self.assertIn("MAX_EXPOSURE_USD must be greater than 0", errors)

    def test_invalid_timeframe_relationship_blocks_startup(self) -> None:
        errors = collect_startup_validation_errors(
            core=build_core(),
            execution=build_execution(),
            market=build_market(
                execution_timeframe_seconds=300.0,
                trend_filter_timeframe_seconds=60.0,
                enable_trend_timeframe_filter=True,
                enable_execution_confirmation=True,
                confirmation_timeframe_seconds=600.0,
                confirmation_momentum_shock_bps=0.0,
            ),
            mev_execution=build_mev_execution(enable_private_tx=False),
            telegram=build_telegram(),
            wallet=build_wallet(),
            env_has_value_fn=lambda name: name in BASE_ENV_VARS,
        )

        self.assertIn("TREND_FILTER_TIMEFRAME_SECONDS must be greater than or equal to EXECUTION_TIMEFRAME_SECONDS", errors)
        self.assertIn("CONFIRMATION_TIMEFRAME_SECONDS must be less than or equal to EXECUTION_TIMEFRAME_SECONDS", errors)
        self.assertIn("CONFIRMATION_MOMENTUM_SHOCK_BPS must be greater than 0", errors)


if __name__ == "__main__":
    unittest.main()
