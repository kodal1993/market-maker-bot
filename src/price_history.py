import csv
import time
from collections import deque
from pathlib import Path


def load_bootstrap_prices(
    equity_csv_path: str,
    max_rows: int,
    max_age_seconds: float,
) -> list[float]:
    if max_rows <= 0 or max_age_seconds <= 0:
        return []

    path = Path(equity_csv_path)
    if not path.exists():
        return []

    file_age_seconds = time.time() - path.stat().st_mtime
    if file_age_seconds > max_age_seconds:
        return []

    prices: deque[float] = deque(maxlen=max_rows)

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "price" not in reader.fieldnames:
            return []

        for row in reader:
            raw_price = row.get("price", "")
            if not raw_price:
                continue

            try:
                prices.append(float(raw_price))
            except ValueError:
                continue

    return list(prices)
