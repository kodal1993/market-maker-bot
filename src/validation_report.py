from performance import (
    FAIL_PROFIT_FACTOR,
    MINIMUM_VERDICT_TRADE_COUNT,
    PASS_MAX_DRAWDOWN_PCT,
    PASS_PROFIT_FACTOR,
    build_report,
    build_verdict_snapshot,
    flatten_report,
    profit_factor_display,
    resolve_profit_factor_value,
    write_report_csv,
    write_report_json,
)


def build_validation_snapshot(summary: dict) -> dict:
    return build_verdict_snapshot(summary)


MIN_VALIDATION_TRADE_COUNT = MINIMUM_VERDICT_TRADE_COUNT
