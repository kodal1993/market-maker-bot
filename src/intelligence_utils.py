from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from config import INTELLIGENCE_WARMUP_ROWS, LONG_MA_WINDOW, VOL_WINDOW


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def sma(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    if len(values) < window:
        return sum(values) / len(values)
    subset = values[-window:]
    return sum(subset) / len(subset)


def ema(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    span = max(int(window), 1)
    alpha = 2.0 / (span + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = (alpha * float(value)) + ((1.0 - alpha) * result)
    return result


def stddev(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance ** 0.5


def safe_lower(value: str) -> str:
    return value.strip().lower()


def strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def parse_datetime(value) -> datetime | None:
    if value in {None, ""}:
        return None

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    try:
        parsed = parsedate_to_datetime(raw_value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    normalized = raw_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def first_value(mapping: dict, keys: list[str], default=""):
    for key in keys:
        value = mapping.get(key)
        if value not in {None, ""}:
            return value
    return default


def keyword_score(text: str, positive: list[str], negative: list[str]) -> tuple[float, int, int]:
    lowered = safe_lower(text)
    positive_hits = sum(1 for keyword in positive if keyword and keyword in lowered)
    negative_hits = sum(1 for keyword in negative if keyword and keyword in lowered)
    hit_total = positive_hits + negative_hits
    if hit_total == 0:
        return 0.0, positive_hits, negative_hits
    return clamp((positive_hits - negative_hits) / hit_total, -1.0, 1.0), positive_hits, negative_hits


def recency_weight(published_at: datetime | None, lookback_hours: float, now_utc: datetime) -> float:
    if lookback_hours <= 0:
        return 0.0

    if published_at is None:
        return 0.45

    age_seconds = (now_utc - published_at).total_seconds()
    if age_seconds < 0:
        age_seconds = 0.0

    max_age_seconds = lookback_hours * 3600.0
    if age_seconds > max_age_seconds:
        return 0.0

    freshness = 1.0 - (age_seconds / max_age_seconds)
    return clamp(0.2 + (freshness * 0.8), 0.2, 1.0)


def find_child_text(node: ET.Element, names: set[str]) -> str:
    for child in node:
        if strip_namespace(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def market_price_window(prices: list[float]) -> list[float]:
    lookback = max(INTELLIGENCE_WARMUP_ROWS, LONG_MA_WINDOW, VOL_WINDOW + 1)
    if len(prices) <= lookback:
        return prices
    return prices[-lookback:]
