from __future__ import annotations

from datetime import datetime

from config import (
    MACRO_BLOCK_MINUTES,
    MACRO_LOOKBACK_HOURS,
    MACRO_RISK_KEYWORDS,
    MACRO_RSS_URLS,
    MACRO_SUPPORTIVE_KEYWORDS,
    NEWS_LOOKBACK_HOURS,
    NEWS_MAX_ITEMS,
    NEWS_NEGATIVE_KEYWORDS,
    NEWS_POSITIVE_KEYWORDS,
    NEWS_RSS_URLS,
    ONCHAIN_BEARISH_KEYWORDS,
    ONCHAIN_BULLISH_KEYWORDS,
    ONCHAIN_LOOKBACK_HOURS,
    ONCHAIN_RSS_URLS,
    ONCHAIN_STRESS_KEYWORDS,
)
from intelligence_feeds import SignalFeedClient
from intelligence_models import FeedItem, SignalScore
from intelligence_utils import clamp, keyword_score, recency_weight


def build_news_signal(feed_client: SignalFeedClient, now_utc: datetime) -> SignalScore:
    items, errors = feed_client.load_items(NEWS_RSS_URLS)
    if not NEWS_RSS_URLS:
        return SignalScore(summary="news_not_configured", status="disabled")
    if not items:
        status = "fetch_error" if errors else "no_items"
        summary = errors[0] if errors else "news_neutral"
        return SignalScore(summary=summary, status=status)

    weighted_items: list[tuple[float, float, FeedItem]] = []
    freshest_seconds: float | None = None

    for item in items:
        item_weight = recency_weight(item.published_at, NEWS_LOOKBACK_HOURS, now_utc)
        if item_weight <= 0:
            continue

        text = f"{item.title} {item.summary}".strip()
        score, positive_hits, negative_hits = keyword_score(
            text=text,
            positive=NEWS_POSITIVE_KEYWORDS,
            negative=NEWS_NEGATIVE_KEYWORDS,
        )
        if positive_hits + negative_hits == 0:
            continue

        weighted_items.append((score, item_weight, item))

        if item.published_at is not None:
            age_seconds = max((now_utc - item.published_at).total_seconds(), 0.0)
            freshest_seconds = age_seconds if freshest_seconds is None else min(freshest_seconds, age_seconds)

    if not weighted_items:
        return SignalScore(
            summary="news_recent_but_neutral",
            status="neutral",
            item_count=min(len(items), NEWS_MAX_ITEMS),
        )

    selected = sorted(weighted_items, key=lambda row: row[1], reverse=True)[:NEWS_MAX_ITEMS]
    total_weight = sum(weight for _, weight, _ in selected)
    score = sum(item_score * weight for item_score, weight, _ in selected) / max(total_weight, 1e-9)
    confidence = clamp(0.22 + (0.08 * len(selected)) + min(abs(score), 0.3), 0.0, 0.92)
    top_item = max(selected, key=lambda row: abs(row[0]) * row[1])[2]

    return SignalScore(
        score=clamp(score, -1.0, 1.0),
        confidence=confidence,
        freshness_seconds=freshest_seconds,
        item_count=len(selected),
        summary=top_item.title[:180],
        status="active",
    )


def build_macro_signal(feed_client: SignalFeedClient, now_utc: datetime) -> SignalScore:
    if not MACRO_RSS_URLS:
        return SignalScore(summary="macro_not_configured", status="disabled")

    items, errors = feed_client.load_items(MACRO_RSS_URLS)
    if not items:
        status = "fetch_error" if errors else "no_items"
        summary = errors[0] if errors else "macro_neutral"
        return SignalScore(summary=summary, status=status)

    block_seconds = MACRO_BLOCK_MINUTES * 60.0
    weighted_scores: list[tuple[float, float, FeedItem]] = []
    blocked = False
    freshest_seconds: float | None = None

    for item in items:
        item_weight = recency_weight(item.published_at, MACRO_LOOKBACK_HOURS, now_utc)
        if item_weight <= 0:
            continue

        text = f"{item.title} {item.summary}".strip().lower()
        risk_hits = sum(1 for keyword in MACRO_RISK_KEYWORDS if keyword in text)
        support_hits = sum(1 for keyword in MACRO_SUPPORTIVE_KEYWORDS if keyword in text)
        if risk_hits == 0 and support_hits == 0:
            continue

        item_score = 0.0
        if risk_hits > 0:
            item_score -= 0.35 + (0.40 * item_weight) + (0.08 * min(risk_hits, 2))
        if support_hits > 0:
            item_score += 0.12 + (0.16 * item_weight) + (0.05 * min(support_hits, 2))

        weight = item_weight * (1.0 + (0.15 * (risk_hits + support_hits)))
        weighted_scores.append((clamp(item_score, -1.0, 1.0), weight, item))

        if item.published_at is not None:
            age_seconds = max((now_utc - item.published_at).total_seconds(), 0.0)
            freshest_seconds = age_seconds if freshest_seconds is None else min(freshest_seconds, age_seconds)
            if risk_hits > 0 and age_seconds <= block_seconds:
                blocked = True

    if not weighted_scores:
        return SignalScore(summary="macro_clear", status="neutral", item_count=0)

    total_weight = sum(weight for _, weight, _ in weighted_scores)
    score = sum(item_score * weight for item_score, weight, _ in weighted_scores) / max(total_weight, 1e-9)
    confidence = clamp(0.28 + (0.10 * len(weighted_scores)) + (0.15 if blocked else 0.0), 0.0, 0.95)
    top_item = max(weighted_scores, key=lambda row: abs(row[0]) * row[1])[2]

    return SignalScore(
        score=clamp(score, -1.0, 1.0),
        confidence=confidence,
        freshness_seconds=freshest_seconds,
        item_count=len(weighted_scores),
        summary=top_item.title[:180],
        status="blocked" if blocked else "active",
        blocked=blocked,
    )


def build_onchain_signal(feed_client: SignalFeedClient, now_utc: datetime) -> SignalScore:
    if not ONCHAIN_RSS_URLS:
        return SignalScore(summary="onchain_not_configured", status="disabled")

    items, errors = feed_client.load_items(ONCHAIN_RSS_URLS)
    if not items:
        status = "fetch_error" if errors else "no_items"
        summary = errors[0] if errors else "onchain_neutral"
        return SignalScore(summary=summary, status=status)

    weighted_items: list[tuple[float, float, int, FeedItem]] = []
    freshest_seconds: float | None = None

    for item in items:
        item_weight = recency_weight(item.published_at, ONCHAIN_LOOKBACK_HOURS, now_utc)
        if item_weight <= 0:
            continue

        text = f"{item.title} {item.summary}".strip()
        lowered = text.lower()
        score, bullish_hits, bearish_hits = keyword_score(
            text=text,
            positive=ONCHAIN_BULLISH_KEYWORDS,
            negative=ONCHAIN_BEARISH_KEYWORDS,
        )
        stress_hits = sum(1 for keyword in ONCHAIN_STRESS_KEYWORDS if keyword and keyword in lowered)
        hit_total = bullish_hits + bearish_hits + stress_hits
        if hit_total == 0:
            continue

        item_score = score
        if stress_hits > 0:
            stress_penalty = min(0.18 * stress_hits, 0.45)
            item_score = min(item_score, -0.18 - stress_penalty)

        weight = item_weight * (1.0 + (0.18 * hit_total))
        weighted_items.append((clamp(item_score, -1.0, 1.0), weight, stress_hits, item))

        if item.published_at is not None:
            age_seconds = max((now_utc - item.published_at).total_seconds(), 0.0)
            freshest_seconds = age_seconds if freshest_seconds is None else min(freshest_seconds, age_seconds)

    if not weighted_items:
        return SignalScore(summary="onchain_recent_but_neutral", status="neutral", item_count=len(items))

    selected = sorted(weighted_items, key=lambda row: row[1], reverse=True)[:NEWS_MAX_ITEMS]
    total_weight = sum(weight for _, weight, _, _ in selected)
    score = sum(item_score * weight for item_score, weight, _, _ in selected) / max(total_weight, 1e-9)
    stress_count = sum(stress_hits for _, _, stress_hits, _ in selected)
    confidence = clamp(
        0.20 + (0.07 * len(selected)) + (0.05 * min(stress_count, 3)) + min(abs(score), 0.24),
        0.0,
        0.90,
    )
    top_item = max(selected, key=lambda row: abs(row[0]) * row[1])[3]

    return SignalScore(
        score=clamp(score, -1.0, 1.0),
        confidence=confidence,
        freshness_seconds=freshest_seconds,
        item_count=len(selected),
        summary=top_item.title[:180],
        status="active",
    )
