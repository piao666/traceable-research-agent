"""Stable, non-secret error taxonomy for OpenAI-compatible providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from email.message import Message
from typing import Mapping


LLM_ERROR_TYPES = {
    "auth_error",
    "permission_error",
    "rate_limited",
    "provider_unavailable",
    "timeout",
    "invalid_request",
    "model_not_found",
    "context_overflow",
    "malformed_response",
    "structured_output_invalid",
    "budget_exhausted",
    "cancelled",
}


@dataclass(frozen=True)
class LLMErrorInfo:
    error_type: str
    retryable: bool
    message: str
    http_status: int | None = None
    retry_after_seconds: float | None = None


def _retry_after(headers: Mapping[str, str] | Message | None) -> float | None:
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
        if raw is None:
            return None
        try:
            parsed = float(str(raw).strip())
        except (TypeError, ValueError):
            when = parsedate_to_datetime(str(raw))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            parsed = (when - datetime.now(timezone.utc)).total_seconds()
        return min(max(parsed, 0.0), 60.0)
    except (TypeError, ValueError, OverflowError):
        return None


def classify_http_error(
    status: int,
    headers: Mapping[str, str] | Message | None = None,
    response_body: str = "",
) -> LLMErrorInfo:
    """Classify an HTTP failure without returning the provider response body."""

    body = str(response_body or "").lower()
    retry_after = _retry_after(headers)
    if status == 401:
        category, retryable = "auth_error", False
    elif status == 403:
        category, retryable = "permission_error", False
    elif status == 429:
        category, retryable = "rate_limited", True
    elif status in {408, 504}:
        category, retryable = "timeout", True
    elif status == 404 or "model_not_found" in body or "model not found" in body:
        category, retryable = "model_not_found", False
    elif status in {400, 413, 422} and any(
        marker in body for marker in ("context length", "context window", "maximum context", "too many tokens")
    ):
        category, retryable = "context_overflow", False
    elif status in {400, 409, 413, 422}:
        category, retryable = "invalid_request", False
    elif status >= 500:
        category, retryable = "provider_unavailable", True
    else:
        category, retryable = "provider_unavailable", False
    return LLMErrorInfo(
        error_type=category,
        retryable=retryable,
        message=f"LLM provider request failed ({category}, HTTP {status}).",
        http_status=status,
        retry_after_seconds=retry_after,
    )


def classify_transport_error(error: BaseException) -> LLMErrorInfo:
    name = type(error).__name__.lower()
    category = "timeout" if "timeout" in name or "timed out" in str(error).lower() else "provider_unavailable"
    return LLMErrorInfo(
        error_type=category,
        retryable=True,
        message=f"LLM provider transport failed ({category}).",
    )


def retry_delay(attempt: int, retry_after_seconds: float | None = None) -> float:
    """Bounded exponential backoff with deterministic low jitter."""

    exponential = 0.5 * (2 ** max(0, int(attempt)))
    jitter = 0.05 * ((max(0, int(attempt)) % 3) + 1)
    return min(60.0, max(exponential + jitter, retry_after_seconds or 0.0))
