from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from config import SIGNAL_CACHE_SECONDS, SIGNAL_FETCH_ENABLED, SIGNAL_HTTP_TIMEOUT_SECONDS
from intelligence_models import FeedItem
from intelligence_utils import find_child_text, first_value, parse_datetime, strip_namespace


@dataclass
class _CacheEntry:
    fetched_at: float
    items: list[FeedItem]
    error: str = ""


class SignalFeedClient:
    def __init__(self):
        self.cache: dict[str, _CacheEntry] = {}

    def load_items(self, targets: list[str]) -> tuple[list[FeedItem], list[str]]:
        if not SIGNAL_FETCH_ENABLED or not targets:
            return [], []

        all_items: list[FeedItem] = []
        errors: list[str] = []

        for target in targets:
            items, error = self._load_target(target)
            all_items.extend(items)
            if error:
                errors.append(f"{target}: {error}")

        return all_items, errors

    def _load_target(self, target: str) -> tuple[list[FeedItem], str]:
        now_ts = time.time()
        cached = self.cache.get(target)

        if cached and (now_ts - cached.fetched_at) <= SIGNAL_CACHE_SECONDS:
            return list(cached.items), cached.error

        try:
            raw_payload = self._read_target(target)
            items = self._parse_payload(raw_payload, target)
            self.cache[target] = _CacheEntry(fetched_at=now_ts, items=items, error="")
            return list(items), ""
        except Exception as exc:
            error = str(exc)
            if cached and cached.items:
                self.cache[target] = _CacheEntry(fetched_at=now_ts, items=cached.items, error=error)
                return list(cached.items), error
            self.cache[target] = _CacheEntry(fetched_at=now_ts, items=[], error=error)
            return [], error

    def _read_target(self, target: str, headers: dict[str, str] | None = None) -> str:
        if target.startswith(("http://", "https://")):
            request_headers = {"User-Agent": "market-maker-bot/1.0"}
            if headers:
                request_headers.update(headers)
            request = Request(target, headers=request_headers)
            with urlopen(request, timeout=SIGNAL_HTTP_TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")

        return Path(target).read_text(encoding="utf-8")

    def _parse_payload(self, raw_payload: str, source: str) -> list[FeedItem]:
        stripped = raw_payload.lstrip()
        if not stripped:
            return []
        if stripped.startswith("{") or stripped.startswith("["):
            return self._parse_json_items(raw_payload, source)
        return self._parse_xml_items(raw_payload, source)

    def _parse_json_items(self, raw_payload: str, source: str) -> list[FeedItem]:
        payload = json.loads(raw_payload)

        if isinstance(payload, dict):
            if isinstance(payload.get("items"), list):
                raw_items = payload["items"]
            elif isinstance(payload.get("events"), list):
                raw_items = payload["events"]
            elif isinstance(payload.get("alerts"), list):
                raw_items = payload["alerts"]
            elif isinstance(payload.get("data"), list):
                raw_items = payload["data"]
            else:
                raw_items = [payload]
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[FeedItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue

            title = str(first_value(raw_item, ["title", "headline", "event", "name", "alert", "text"], "")).strip()
            summary = str(
                first_value(raw_item, ["summary", "description", "details", "body", "content"], "")
            ).strip()
            published_at = parse_datetime(
                first_value(
                    raw_item,
                    ["published_at", "published", "timestamp", "time", "date", "datetime", "event_time"],
                )
            )
            link = str(first_value(raw_item, ["link", "url"], "")).strip()
            metadata = {
                "impact": raw_item.get("impact"),
                "severity": raw_item.get("severity"),
                "amount_usd": raw_item.get("amount_usd"),
                "category": raw_item.get("category"),
                "sentiment_score": raw_item.get("sentiment_score"),
            }

            if not title and not summary:
                continue

            items.append(
                FeedItem(
                    title=title or summary[:160],
                    summary=summary,
                    published_at=published_at,
                    source=source,
                    link=link,
                    metadata=metadata,
                )
            )

        return items

    def _parse_xml_items(self, raw_payload: str, source: str) -> list[FeedItem]:
        root = ET.fromstring(raw_payload)
        items: list[FeedItem] = []
        for node in root.iter():
            if strip_namespace(node.tag) not in {"item", "entry"}:
                continue

            title = find_child_text(node, {"title", "headline"})
            summary = find_child_text(node, {"summary", "description", "content", "subtitle"})
            published_at = parse_datetime(find_child_text(node, {"pubdate", "published", "updated", "date"}))
            link = self._extract_xml_link(node)

            if not title and not summary:
                continue

            items.append(
                FeedItem(
                    title=title or summary[:160],
                    summary=summary,
                    published_at=published_at,
                    source=source,
                    link=link,
                    metadata={},
                )
            )

        return items

    def _extract_xml_link(self, node: ET.Element) -> str:
        for child in node:
            if strip_namespace(child.tag) != "link":
                continue
            href = child.attrib.get("href", "").strip()
            if href:
                return href
            text = "".join(child.itertext()).strip()
            if text:
                return text
        return ""
