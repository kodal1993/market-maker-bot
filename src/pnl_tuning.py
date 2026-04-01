import argparse
import csv
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CautionProfile:
    name: str
    signal_caution_threshold: float
    signal_block_risk_threshold: float
    caution_size_multiplier: float
    caution_spread_multiplier: float


@dataclass(frozen=True)
class RangeProfile:
    name: str
    range_size_multiplier: float
    range_spread_tightening: float
    range_directional_bias_factor: float


@dataclass(frozen=True)
class TrendProfile:
    name: str
    trend_size_multiplier: float
    max_trend_chase_bps: float
    trend_buy_requote_bps: float


CAUTION_PROFILES = [
    CautionProfile("soft", -0.40, 0.84, 0.90, 1.03),
    CautionProfile("balanced", -0.36, 0.82, 0.88, 1.05),
    CautionProfile("guarded", -0.32, 0.80, 0.86, 1.06),
]

RANGE_PROFILES = [
    RangeProfile("active", 1.08, 0.86, 0.34),
    RangeProfile("aggressive", 1.12, 0.84, 0.30),
    RangeProfile("max_capture", 1.16, 0.82, 0.26),
]

TREND_PROFILES = [
    TrendProfile("restrained", 1.10, 4.5, 1.9),
    TrendProfile("controlled", 1.14, 4.8, 2.2),
    TrendProfile("balanced", 1.18, 5.0, 2.4),
]

from log_cleanup import cleanup_old_logs, format_cleanup_result


def parse_args():
    parser = argparse.ArgumentParser(description="Search for higher-PnL paper settings with risk-aware KPIs.")
    parser.add_argument("--input", required=True, help="Primary historical CSV input.")
    parser.add_argument("--control-input", default="", help="Optional secondary control CSV input.")
    parser.add_argument("--price-column", default="close", help="CSV price column.")
    parser.add_argument("--source-column", default="", help="Optional CSV source column.")
    parser.add_argument("--search-seed", type=int, default=42, help="Seed used for the fast search pass.")
    parser.add_argument("--validation-seeds", default="41,42,43", help="Comma-separated seeds for validation.")
    parser.add_argument("--top-n", type=int, default=3, help="How many top configs to validate.")
    parser.add_argument(
        "--output-dir",
        default=r"logs\backtests\tuning",
        help="Directory for tuning outputs.",
    )
    parser.add_argument(
        "--news-url",
        default=r"data\signals\benchmark_bearish_news.xml",
        help="Local or remote news feed used during tuning.",
    )
    parser.add_argument(
        "--macro-url",
        default=r"data\signals\benchmark_bearish_macro.json",
        help="Local or remote macro feed used during tuning.",
    )
    parser.add_argument(
        "--onchain-url",
        default=r"data\signals\benchmark_bearish_onchain.json",
        help="Local or remote onchain feed used during tuning.",
    )
    return parser.parse_args()


def parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def mode_profit_leader(summary: dict) -> str:
    realized = summary.get("mode_realized_pnl_usd", {})
    if not realized:
        return "UNKNOWN"
    return max(realized, key=realized.get)


def build_env(base_env: dict[str, str], caution: CautionProfile, range_profile: RangeProfile, trend: TrendProfile) -> dict[str, str]:
    env = base_env.copy()
    env.update(
        {
            "SIGNAL_CAUTION_THRESHOLD": str(caution.signal_caution_threshold),
            "SIGNAL_BLOCK_RISK_THRESHOLD": str(caution.signal_block_risk_threshold),
            "CAUTION_SIZE_MULTIPLIER": str(caution.caution_size_multiplier),
            "CAUTION_SPREAD_MULTIPLIER": str(caution.caution_spread_multiplier),
            "RANGE_SIZE_MULTIPLIER": str(range_profile.range_size_multiplier),
            "RANGE_SPREAD_TIGHTENING": str(range_profile.range_spread_tightening),
            "RANGE_DIRECTIONAL_BIAS_FACTOR": str(range_profile.range_directional_bias_factor),
            "TREND_SIZE_MULTIPLIER": str(trend.trend_size_multiplier),
            "MAX_TREND_CHASE_BPS": str(trend.max_trend_chase_bps),
            "TREND_BUY_REQUOTE_BPS": str(trend.trend_buy_requote_bps),
        }
    )
    return env


def build_label(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in name)


def run_backtest(
    python_exe: Path,
    backtest_py: Path,
    project_root: Path,
    input_path: str,
    price_column: str,
    source_column: str,
    seed: int,
    label: str,
    summary_path: Path,
    env: dict[str, str],
) -> dict:
    cmd = [
        str(python_exe),
        str(backtest_py),
        "--input",
        input_path,
        "--price-column",
        price_column,
        "--seed",
        str(seed),
        "--label",
        label,
        "--summary-json",
        str(summary_path),
    ]
    if source_column:
        cmd.extend(["--source-column", source_column])

    subprocess.run(
        cmd,
        cwd=project_root,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    with summary_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def score_summary(summary: dict, baseline_trade_count: float) -> float:
    trade_count = summary["trade_count"]
    trade_penalty = max((baseline_trade_count * 0.75) - trade_count, 0.0) * 0.08
    return (
        summary["final_pnl"]
        + (summary["pnl_per_trade"] * 80.0)
        + (min(trade_count, baseline_trade_count * 1.35) * 0.01)
        - (summary["max_drawdown_usd"] * 0.32)
        - (summary["no_trade_ratio"] * 250.0)
        - trade_penalty
    )


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def average_dicts(summaries: list[dict], key: str) -> dict[str, float]:
    names = set()
    for summary in summaries:
        names.update(summary.get(key, {}).keys())
    return {
        name: average([summary.get(key, {}).get(name, 0.0) for summary in summaries])
        for name in sorted(names)
    }


def dominant_key(values: dict[str, float]) -> str:
    if not values:
        return "UNKNOWN"
    return max(values, key=values.get)


def summarize_validation(name: str, dataset: str, summaries: list[dict]) -> dict:
    mode_realized = average_dicts(summaries, "mode_realized_pnl_usd")
    mode_distribution = average_dicts(summaries, "mode_distribution_pct")
    return {
        "config": name,
        "dataset": dataset,
        "avg_final_pnl": round(average([summary["final_pnl"] for summary in summaries]), 6),
        "avg_realized_pnl": round(average([summary["realized_pnl_usd"] for summary in summaries]), 6),
        "avg_pnl_per_trade": round(average([summary["pnl_per_trade"] for summary in summaries]), 6),
        "avg_trade_count": round(average([summary["trade_count"] for summary in summaries]), 6),
        "avg_max_drawdown_usd": round(average([summary["max_drawdown_usd"] for summary in summaries]), 6),
        "avg_no_trade_ratio": round(average([summary["no_trade_ratio"] for summary in summaries]), 6),
        "avg_range_maker_pct": round(mode_distribution.get("RANGE_MAKER", 0.0), 6),
        "avg_trend_up_pct": round(mode_distribution.get("TREND_UP", 0.0), 6),
        "avg_overweight_exit_pct": round(mode_distribution.get("OVERWEIGHT_EXIT", 0.0), 6),
        "avg_normal_feed_pct": round(average_dicts(summaries, "feed_state_distribution_pct").get("NORMAL", 0.0), 6),
        "avg_caution_feed_pct": round(average_dicts(summaries, "feed_state_distribution_pct").get("CAUTION", 0.0), 6),
        "avg_block_feed_pct": round(average_dicts(summaries, "feed_state_distribution_pct").get("BLOCK", 0.0), 6),
        "dominant_mode_by_realized_pnl": dominant_key(mode_realized),
        "range_realized_pnl_usd": round(mode_realized.get("RANGE_MAKER", 0.0), 6),
        "trend_realized_pnl_usd": round(mode_realized.get("TREND_UP", 0.0), 6),
        "overweight_realized_pnl_usd": round(mode_realized.get("OVERWEIGHT_EXIT", 0.0), 6),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    backtest_py = project_root / "src" / "backtest.py"
    cleanup_result = cleanup_old_logs([project_root / args.output_dir])
    print(f"Log cleanup | {format_cleanup_result(cleanup_result)}")
    output_root = project_root / args.output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)

    validation_seeds = parse_int_list(args.validation_seeds)
    base_env = os.environ.copy()
    base_env.update(
        {
            "SIGNAL_FETCH_ENABLED": "true",
            "NEWS_RSS_URLS": args.news_url,
            "MACRO_RSS_URLS": args.macro_url,
            "ONCHAIN_RSS_URLS": args.onchain_url,
            "MACRO_BLOCK_MINUTES": "0",
        }
    )

    baseline_primary_summary = run_backtest(
        python_exe=python_exe,
        backtest_py=backtest_py,
        project_root=project_root,
        input_path=args.input,
        price_column=args.price_column,
        source_column=args.source_column,
        seed=args.search_seed,
        label="baseline_search",
        summary_path=output_root / "baseline_search_summary.json",
        env=base_env,
    )
    baseline_trade_count = float(baseline_primary_summary["trade_count"])

    search_rows: list[dict] = []
    candidates: list[dict] = []

    for caution in CAUTION_PROFILES:
        for range_profile in RANGE_PROFILES:
            for trend in TREND_PROFILES:
                config_name = f"{caution.name}__{range_profile.name}__{trend.name}"
                env = build_env(base_env, caution, range_profile, trend)
                summary = run_backtest(
                    python_exe=python_exe,
                    backtest_py=backtest_py,
                    project_root=project_root,
                    input_path=args.input,
                    price_column=args.price_column,
                    source_column=args.source_column,
                    seed=args.search_seed,
                    label=build_label(config_name),
                    summary_path=output_root / f"{build_label(config_name)}_search_summary.json",
                    env=env,
                )
                score = score_summary(summary, baseline_trade_count)
                row = {
                    "config": config_name,
                    "score": round(score, 6),
                    "final_pnl": round(summary["final_pnl"], 6),
                    "realized_pnl": round(summary["realized_pnl_usd"], 6),
                    "pnl_per_trade": round(summary["pnl_per_trade"], 6),
                    "trade_count": summary["trade_count"],
                    "max_drawdown_usd": round(summary["max_drawdown_usd"], 6),
                    "no_trade_ratio": round(summary["no_trade_ratio"], 6),
                    "range_maker_pct": round(summary["mode_distribution_pct"].get("RANGE_MAKER", 0.0), 6),
                    "trend_up_pct": round(summary["mode_distribution_pct"].get("TREND_UP", 0.0), 6),
                    "overweight_exit_pct": round(summary["mode_distribution_pct"].get("OVERWEIGHT_EXIT", 0.0), 6),
                    "normal_feed_pct": round(summary["feed_state_distribution_pct"].get("NORMAL", 0.0), 6),
                    "caution_feed_pct": round(summary["feed_state_distribution_pct"].get("CAUTION", 0.0), 6),
                    "block_feed_pct": round(summary["feed_state_distribution_pct"].get("BLOCK", 0.0), 6),
                    "dominant_mode_by_realized_pnl": mode_profit_leader(summary),
                    "caution_threshold": caution.signal_caution_threshold,
                    "block_risk_threshold": caution.signal_block_risk_threshold,
                    "caution_size_multiplier": caution.caution_size_multiplier,
                    "caution_spread_multiplier": caution.caution_spread_multiplier,
                    "range_size_multiplier": range_profile.range_size_multiplier,
                    "range_spread_tightening": range_profile.range_spread_tightening,
                    "range_directional_bias_factor": range_profile.range_directional_bias_factor,
                    "trend_size_multiplier": trend.trend_size_multiplier,
                    "max_trend_chase_bps": trend.max_trend_chase_bps,
                    "trend_buy_requote_bps": trend.trend_buy_requote_bps,
                }
                search_rows.append(row)
                candidates.append(
                    {
                        "name": config_name,
                        "env": env,
                        "score": score,
                    }
                )

    search_rows.sort(key=lambda row: row["score"], reverse=True)
    write_csv(output_root / "search_results.csv", search_rows)

    top_candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)[: max(args.top_n, 1)]
    validation_rows: list[dict] = []

    datasets = [("primary", args.input)]
    if args.control_input:
        datasets.append(("control", args.control_input))

    baseline_validation_name = "baseline_current"
    for dataset_name, input_path in datasets:
        summaries = []
        for seed in validation_seeds:
            summaries.append(
                run_backtest(
                    python_exe=python_exe,
                    backtest_py=backtest_py,
                    project_root=project_root,
                    input_path=input_path,
                    price_column=args.price_column,
                    source_column=args.source_column,
                    seed=seed,
                    label=f"{baseline_validation_name}_{dataset_name}_{seed}",
                    summary_path=output_root / f"{baseline_validation_name}_{dataset_name}_{seed}.json",
                    env=base_env,
                )
            )
        validation_rows.append(summarize_validation(baseline_validation_name, dataset_name, summaries))

    for candidate in top_candidates:
        for dataset_name, input_path in datasets:
            summaries = []
            for seed in validation_seeds:
                summaries.append(
                    run_backtest(
                        python_exe=python_exe,
                        backtest_py=backtest_py,
                        project_root=project_root,
                        input_path=input_path,
                        price_column=args.price_column,
                        source_column=args.source_column,
                        seed=seed,
                        label=f"{build_label(candidate['name'])}_{dataset_name}_{seed}",
                        summary_path=output_root / f"{build_label(candidate['name'])}_{dataset_name}_{seed}.json",
                        env=candidate["env"],
                    )
                )
            validation_rows.append(summarize_validation(candidate["name"], dataset_name, summaries))

    write_csv(output_root / "validation_results.csv", validation_rows)

    summary = {
        "output_dir": str(output_root),
        "baseline_search": baseline_primary_summary,
        "top_configs": search_rows[: max(args.top_n, 1)],
        "validation_results": validation_rows,
    }
    with (output_root / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    print(output_root)
    print(output_root / "search_results.csv")
    print(output_root / "validation_results.csv")
    print(output_root / "summary.json")


if __name__ == "__main__":
    main()
