from __future__ import annotations

import re

from urllib.parse import urlsplit

_SECRET_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "PRIVATE_KEY",
    "WALLET_PRIVATE_KEY",
)


def redact_secrets(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""

    rpc_pattern = re.compile(r"https?://[^\s'\"`]+", re.IGNORECASE)

    def _replace_url(match: re.Match[str]) -> str:
        original = match.group(0)
        candidate = original.rstrip('.,;:')
        suffix = original[len(candidate):]
        lower = candidate.lower()
        if any(provider in lower for provider in ("infura.io", "alchemy.com", "quicknode", "ankr")):
            parsed = urlsplit(candidate)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}{suffix}"
            return f"{candidate.split('?')[0]}{suffix}"
        return f"{candidate}{suffix}"

    sanitized = rpc_pattern.sub(_replace_url, text)
    for key in _SECRET_KEYS:
        sanitized = re.sub(rf"({key}\s*[=:]\s*)[^\s,;]+", rf"\1[REDACTED]", sanitized, flags=re.IGNORECASE)

    sanitized = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TELEGRAM_BOT_TOKEN]", sanitized)
    sanitized = re.sub(r"0x[a-fA-F0-9]{64}\b", "[REDACTED_PRIVATE_KEY]", sanitized)
    return sanitized
