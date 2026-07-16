"""Shared recursive redaction for traces, tool results, logs, and exports."""

from __future__ import annotations

import re
from typing import Any


SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|credential|password|secret|token)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_text(value: object) -> str:
    redacted = str(value)
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(_replacement, redacted)
    return redacted


def redact_sensitive_data(value: Any, *, drop_sensitive_keys: bool = False) -> Any:
    """Recursively redact secret keys and common token-shaped values."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text):
                if not drop_sensitive_keys:
                    sanitized[key_text] = "[REDACTED]"
                continue
            sanitized[key_text] = redact_sensitive_data(
                item,
                drop_sensitive_keys=drop_sensitive_keys,
            )
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [
            redact_sensitive_data(item, drop_sensitive_keys=drop_sensitive_keys)
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _replacement(match: re.Match[str]) -> str:
    prefix = match.group(1) if match.lastindex else None
    return f"{prefix}=[REDACTED]" if prefix else "[REDACTED]"
