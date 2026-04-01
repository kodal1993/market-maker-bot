import argparse
import csv
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from logger import log

COINBASE_EXCHANGE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product_id}/candles"
MAX_CANDLES_PER_REQUEST = 300
VALID_GRANULARITIES = {60, 300, 900, 3600, 21600, 86400}


def parse_args():
    parser = argparse.ArgumentParser(description="Download historical Coinbase candles into a backtest-friendly CSV.")
    parser.add_argument("--product", default="ETH-USD", help="Coinbase product id, for example ETH-USD.")
    parser.add_argument(
        "--granularity",
        type=int,
        default=300,
        help="Candle size in seconds. Coinbase allows 60, 300, 900, 3600, 21600, 86400.",
    )
    parser.add_argument("--days", type=int, default=30, help="How many trailing days to download.")
    parser.add_argument("--start", default="", help="Optional UTC start time in ISO-8601, for example 2026-02-20T00:00:00Z.")
    parser.add_argument("--end", default="", help="Optional UTC end time in ISO-8601, for example 2026-03-22T00:00:00Z.")
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit output path. Defaults to data/historical/<product>_<granularity>s_<start>_<end>.csv",
    )
    parser.add_argument("--pause-ms", type=int, default=200, help="Pause between requests in milliseconds.")
    return parser.parse_args()


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_iso8601_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_output_path(product: str, granularity: int, start_dt: datetime, end_dt: datetime) -> Path:
    safe_product = product.lower().replace("-", "_")
    filename = f"{safe_product}_{granularity}s_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return Path("data") / "historical" / filename


def fetch_candles_chunk(product: str, start_dt: datetime, end_dt: datetime, granularity: int):
    query = urlencode(
        {
            "start": to_iso8601_z(start_dt),
            "end": to_iso8601_z(end_dt),
            "granularity": str(granularity),
        }
    )
    url = COINBASE_EXCHANGE_CANDLES_URL.format(product_id=product) + "?" + query
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "market-maker-bot-backtest/1.0",
        },
    )

    with urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected Coinbase response: {data}")
    return data


def download_candles(product: str, start_dt: datetime, end_dt: datetime, granularity: int, pause_ms: int):
    chunk_span = timedelta(seconds=granularity * MAX_CANDLES_PER_REQUEST)
    cursor = start_dt
    by_timestamp: dict[int, dict] = {}
    chunk_count = 0

    while cursor < end_dt:
        chunk_end = min(cursor + chunk_span, end_dt)
        chunk_count += 1
        log(
            f"Downloading chunk {chunk_count} | "
            f"{to_iso8601_z(cursor)} -> {to_iso8601_z(chunk_end)}"
        )

        try:
            candles = fetch_candles_chunk(product, cursor, chunk_end, granularity)
        except HTTPError as exc:
            raise RuntimeError(f"Coinbase HTTP error {exc.code} for {product}: {exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"Coinbase network error for {product}: {exc.reason}") from exc

        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                continue

            timestamp = int(candle[0])
            by_timestamp[timestamp] = {
                "timestamp": timestamp,
                "iso_time": datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z"),
                "low": float(candle[1]),
                "high": float(candle[2]),
                "open": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            }

        cursor = chunk_end
        if pause_ms > 0:
            time.sleep(pause_ms / 1000.0)

    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    return rows


def write_csv(rows: list[dict], output_path: Path, product: str, granularity: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp",
                "iso_time",
                "low",
                "high",
                "open",
                "close",
                "volume",
                "price",
                "source",
                "product_id",
                "granularity_seconds",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["timestamp"],
                    row["iso_time"],
                    f"{row['low']:.8f}",
                    f"{row['high']:.8f}",
                    f"{row['open']:.8f}",
                    f"{row['close']:.8f}",
                    f"{row['volume']:.8f}",
                    f"{row['close']:.8f}",
                    "coinbase_exchange",
                    product,
                    granularity,
                ]
            )


def main():
    args = parse_args()
    if args.granularity not in VALID_GRANULARITIES:
        raise ValueError(f"Unsupported granularity: {args.granularity}")

    if args.start and args.end:
        start_dt = parse_utc_datetime(args.start)
        end_dt = parse_utc_datetime(args.end)
    else:
        if args.days <= 0:
            raise ValueError("--days must be greater than zero when --start/--end are not provided.")
        end_dt = datetime.now(tz=UTC)
        start_dt = end_dt - timedelta(days=args.days)

    if end_dt <= start_dt:
        raise ValueError("end time must be later than start time")

    output_path = Path(args.output) if args.output else default_output_path(args.product, args.granularity, start_dt, end_dt)

    log(f"Product: {args.product}")
    log(f"Granularity: {args.granularity} seconds")
    log(f"Range: {to_iso8601_z(start_dt)} -> {to_iso8601_z(end_dt)}")

    rows = download_candles(
        product=args.product,
        start_dt=start_dt,
        end_dt=end_dt,
        granularity=args.granularity,
        pause_ms=args.pause_ms,
    )
    if not rows:
        raise ValueError("No candles returned by Coinbase for the selected range.")

    write_csv(rows, output_path, args.product, args.granularity)
    log(f"Rows written: {len(rows)}")
    log(f"Output CSV: {output_path}")


if __name__ == "__main__":
    main()
