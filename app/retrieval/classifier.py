"""Classify retrieval failures into recovery-driving categories."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from typing import Any

import httpx

from app.retrieval.contracts import (
    FetchBackend,
    FetchFailure,
    FetchFailureCode,
    FetchStatus,
)


_POLICIES: dict[FetchFailureCode, tuple[bool, FetchBackend | None]] = {
    FetchFailureCode.INVALID_URL: (False, None),
    FetchFailureCode.SSRF_BLOCKED: (False, None),
    FetchFailureCode.DNS_ERROR: (True, None),
    FetchFailureCode.CONNECTION_ERROR: (True, None),
    FetchFailureCode.TIMEOUT: (True, None),
    FetchFailureCode.HTTP_401: (False, None),
    FetchFailureCode.HTTP_403: (False, FetchBackend.BROWSER),
    FetchFailureCode.HTTP_404: (False, None),
    FetchFailureCode.HTTP_410: (False, None),
    FetchFailureCode.HTTP_429: (True, None),
    FetchFailureCode.HTTP_5XX: (True, None),
    FetchFailureCode.SOFT_NOT_FOUND: (False, None),
    FetchFailureCode.JAVASCRIPT_REQUIRED: (False, FetchBackend.BROWSER),
    FetchFailureCode.BOT_CHALLENGE: (False, FetchBackend.BROWSER),
    FetchFailureCode.CLOUDFLARE_CHALLENGE: (False, FetchBackend.BROWSER),
    FetchFailureCode.CAPTCHA: (False, FetchBackend.REMOTE_EXTRACT),
    FetchFailureCode.LOGIN_REQUIRED: (False, None),
    FetchFailureCode.COOKIE_WALL: (False, FetchBackend.BROWSER),
    FetchFailureCode.PAYWALL: (False, None),
    FetchFailureCode.EMPTY_DOCUMENT: (False, FetchBackend.BROWSER),
    FetchFailureCode.BOILERPLATE_ONLY: (False, FetchBackend.BROWSER),
    FetchFailureCode.UNSUPPORTED_CONTENT_TYPE: (False, None),
    FetchFailureCode.CONTENT_TOO_LARGE: (False, FetchBackend.REMOTE_EXTRACT),
    FetchFailureCode.PDF_ROUTED: (False, None),
    FetchFailureCode.PDF_CORRUPT: (False, None),
    FetchFailureCode.EXTRACTION_FAILED: (False, FetchBackend.BROWSER),
    FetchFailureCode.ROBOTS_RESTRICTED: (False, None),
    FetchFailureCode.REDIRECT_ERROR: (False, None),
    FetchFailureCode.BATCH_DEADLINE: (True, None),
    FetchFailureCode.BACKEND_UNAVAILABLE: (False, None),
    FetchFailureCode.REMOTE_EXTRACT_UNAVAILABLE: (False, None),
    FetchFailureCode.UNKNOWN: (False, None),
}


def make_failure(
    code: FetchFailureCode,
    message: str | None = None,
    *,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
    next_strategy: FetchBackend | None | object = ...,
    tool_scoped: bool = False,
) -> FetchFailure:
    retryable, default_next = _POLICIES[code]
    selected_next = default_next if next_strategy is ... else next_strategy
    return FetchFailure(
        code=code,
        message=(message or code.value)[:1000],
        retryable=retryable,
        page_scoped=not tool_scoped,
        tool_scoped=tool_scoped,
        recommended_next_strategy=selected_next,  # type: ignore[arg-type]
        http_status=http_status,
        retry_after_seconds=retry_after_seconds,
    )


def classify_http_status(status: int, headers: Mapping[str, Any] | None = None) -> FetchFailure:
    mapping = {
        401: FetchFailureCode.HTTP_401,
        403: FetchFailureCode.HTTP_403,
        404: FetchFailureCode.HTTP_404,
        410: FetchFailureCode.HTTP_410,
        429: FetchFailureCode.HTTP_429,
    }
    code = mapping.get(status, FetchFailureCode.HTTP_5XX if status >= 500 else FetchFailureCode.UNKNOWN)
    retry_after = _retry_after(headers or {}) if code == FetchFailureCode.HTTP_429 else None
    return make_failure(
        code,
        f"HTTP {status}",
        http_status=status,
        retry_after_seconds=retry_after,
    )


def classify_exception(exc: BaseException) -> FetchFailure:
    if isinstance(exc, httpx.TimeoutException):
        return make_failure(FetchFailureCode.TIMEOUT, "Request timed out")
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        cause = exc.__cause__
        code = FetchFailureCode.DNS_ERROR if isinstance(cause, socket.gaierror) else FetchFailureCode.CONNECTION_ERROR
        return make_failure(code, type(exc).__name__)
    if isinstance(exc, socket.gaierror):
        return make_failure(FetchFailureCode.DNS_ERROR, type(exc).__name__)
    if isinstance(exc, httpx.HTTPError):
        return make_failure(FetchFailureCode.CONNECTION_ERROR, type(exc).__name__)
    return make_failure(FetchFailureCode.UNKNOWN, type(exc).__name__)


def classify_legacy_error(message: str | None) -> FetchFailure:
    text = str(message or "").strip()
    lowered = text.casefold()
    if lowered.startswith("http "):
        try:
            return classify_http_status(int(lowered.split()[1]))
        except (ValueError, IndexError):
            pass
    checks = (
        ("redirect_target_unsafe", FetchFailureCode.SSRF_BLOCKED),
        ("redirect_error", FetchFailureCode.REDIRECT_ERROR),
        ("response_too_large", FetchFailureCode.CONTENT_TOO_LARGE),
        ("pdf_routed", FetchFailureCode.PDF_ROUTED),
        ("batch_deadline", FetchFailureCode.BATCH_DEADLINE),
        ("timeout", FetchFailureCode.TIMEOUT),
        ("connection_error", FetchFailureCode.CONNECTION_ERROR),
        ("unrendered_page", FetchFailureCode.JAVASCRIPT_REQUIRED),
        ("navigation_only", FetchFailureCode.BOILERPLATE_ONLY),
    )
    for marker, code in checks:
        if marker in lowered:
            return make_failure(code, text)
    return make_failure(FetchFailureCode.UNKNOWN, text or "Unknown fetch failure")


def failure_status(failure: FetchFailure) -> FetchStatus:
    code = failure.code
    if code in {
        FetchFailureCode.HTTP_403,
        FetchFailureCode.CLOUDFLARE_CHALLENGE,
        FetchFailureCode.BOT_CHALLENGE,
        FetchFailureCode.COOKIE_WALL,
        FetchFailureCode.ROBOTS_RESTRICTED,
    }:
        return FetchStatus.BLOCKED
    if code in {FetchFailureCode.HTTP_404, FetchFailureCode.HTTP_410, FetchFailureCode.SOFT_NOT_FOUND}:
        return FetchStatus.NOT_FOUND
    if code == FetchFailureCode.HTTP_429:
        return FetchStatus.RATE_LIMITED
    if code in {FetchFailureCode.HTTP_401, FetchFailureCode.LOGIN_REQUIRED}:
        return FetchStatus.AUTH_REQUIRED
    if code in {FetchFailureCode.JAVASCRIPT_REQUIRED, FetchFailureCode.EMPTY_DOCUMENT, FetchFailureCode.BOILERPLATE_ONLY}:
        return FetchStatus.JAVASCRIPT_REQUIRED
    if code == FetchFailureCode.PAYWALL:
        return FetchStatus.PAYWALLED
    if code == FetchFailureCode.CAPTCHA:
        return FetchStatus.CAPTCHA
    if code in {FetchFailureCode.UNSUPPORTED_CONTENT_TYPE, FetchFailureCode.PDF_ROUTED, FetchFailureCode.PDF_CORRUPT}:
        return FetchStatus.UNSUPPORTED_CONTENT
    if code == FetchFailureCode.CONTENT_TOO_LARGE:
        return FetchStatus.CONTENT_TOO_LARGE
    if code in {FetchFailureCode.INVALID_URL, FetchFailureCode.SSRF_BLOCKED, FetchFailureCode.REDIRECT_ERROR}:
        return FetchStatus.UNSAFE_URL
    if code in {FetchFailureCode.TIMEOUT, FetchFailureCode.BATCH_DEADLINE}:
        return FetchStatus.TIMEOUT
    if code in {FetchFailureCode.EXTRACTION_FAILED}:
        return FetchStatus.EXTRACT_FAILED
    return FetchStatus.PROVIDER_ERROR


def _retry_after(headers: Mapping[str, Any]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(0.0, min(float(value), 3600.0)) if value is not None else None
    except (TypeError, ValueError):
        return None
