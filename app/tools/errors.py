"""Canonical cross-provider tool error classification."""

from __future__ import annotations

from enum import Enum
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.security.redaction import redact_sensitive_data, redact_text
from app.tools.base import ToolResult


class ToolErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    PERMISSION_ERROR = "permission_error"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESULT = "invalid_result"
    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_FOUND = "model_not_found"
    CONTEXT_OVERFLOW = "context_overflow"
    POLICY_ERROR = "policy_error"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


def http_error_metadata(status: int, headers) -> dict[str, Any]:
    """Keep only non-secret response control fields; distinguish permission and quota."""
    headers = headers or {}
    limited = status == 429 or (status == 403 and (
        headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After") is not None))
    category = ("rate_limited" if limited else "auth_error" if status == 401
                else "forbidden" if status == 403 else "not_found" if status == 404 else "api_error")
    result = {"http_status": status, "error_type": category, "rate_limited": limited}
    retry = headers.get("Retry-After")
    if retry is not None:
        try:
            seconds = float(retry)
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(str(retry))
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 0
        result["retry_after_seconds"] = max(0, min(seconds, 3600))
    return result


def transport_retry_limit(arguments: dict, configured: int) -> int:
    try:
        requested = int(arguments.get("_max_transport_retries", configured))
    except (TypeError, ValueError):
        requested = configured
    return max(0, min(configured, requested, 5))


def classify_tool_error(error_type: object, error_message: object = None) -> ToolErrorCategory:
    normalized = str(error_type or "").strip().lower()
    text = f"{normalized} {error_message or ''}".lower()

    if "rate" in normalized and "limit" in normalized or "429" in text:
        return ToolErrorCategory.RATE_LIMITED
    if "timeout" in text or "timed out" in text:
        return ToolErrorCategory.TIMEOUT
    if normalized in {"permission_error", "forbidden", "http_403"}:
        return ToolErrorCategory.PERMISSION_ERROR
    if normalized in {
        "missing_api_key",
        "auth_error",
        "unauthorized",
        "http_401",
        "login_required",
    } or any(
        term in text for term in ("authentication", "unauthorized", "invalid credential")
    ) or re.search(r"\bhttp\s+401\b", text):
        return ToolErrorCategory.AUTH_ERROR
    if normalized in {
        "invalid_response",
        "invalid_json",
        "invalid_vectors",
        "invalid_decision",
        "parse_error",
        "javascript_required",
        "cloudflare_challenge",
        "bot_challenge",
        "captcha",
        "cookie_wall",
        "paywall",
        "empty_document",
        "boilerplate_only",
        "content_too_large",
        "pdf_routed",
        "extraction_failed",
    }:
        return ToolErrorCategory.INVALID_RESULT
    if normalized == "model_not_found":
        return ToolErrorCategory.MODEL_NOT_FOUND
    if normalized == "context_overflow":
        return ToolErrorCategory.CONTEXT_OVERFLOW
    if normalized in {"invalid_args", "invalid_arguments", "invalid_sql", "invalid_request"}:
        return ToolErrorCategory.INVALID_REQUEST
    if normalized in {
        "safety_rejected",
        "readonly_policy_rejected",
        "approval_mismatch",
        "disallowed_tool",
        "source_mode_violation",
        "invalid_url",
        "ssrf_blocked",
        "redirect_error",
    }:
        return ToolErrorCategory.POLICY_ERROR
    if normalized in {
        "not_found",
        "db_not_found",
        "index_missing",
        "missing_report_file",
        "http_404",
        "http_410",
        "soft_not_found",
    }:
        return ToolErrorCategory.NOT_FOUND
    if normalized in {
        "disabled",
        "backend_disabled",
        "backend_unavailable",
        "remote_extract_unavailable",
        "adapter_not_configured",
        "unavailable",
        "not_implemented",
        "unsupported_content_type",
    }:
        return ToolErrorCategory.UNAVAILABLE
    if normalized in {"handler_error", "parallel_worker_error", "internal_error"}:
        return ToolErrorCategory.INTERNAL_ERROR
    if normalized in {
        "api_error",
        "network_error",
        "http_error",
        "mcp_remote_error",
        "mcp_remote_call_failed",
        "provider_error",
        "provider_unavailable",
        "sql_error",
        "search_error",
        "read_error",
        "dns_error",
        "connection_error",
        "http_5xx",
        "extraction_failed",
        "pdf_corrupt",
    }:
        return ToolErrorCategory.PROVIDER_ERROR
    return ToolErrorCategory.UNKNOWN


def normalize_error_metadata(
    metadata: dict[str, Any] | None,
    error_message: object = None,
) -> dict[str, Any]:
    normalized = dict(redact_sensitive_data(metadata or {}))
    if normalized.get("error_type") and not normalized.get("error_category"):
        normalized["error_category"] = classify_tool_error(
            normalized.get("error_type"),
            error_message,
        ).value
    return normalized


def normalize_tool_result(result: ToolResult) -> ToolResult:
    metadata = normalize_error_metadata(result.metadata, result.error_message)
    if result.success:
        metadata.pop("error_category", None)
    return ToolResult(
        success=result.success,
        output=redact_sensitive_data(result.output),
        output_summary=redact_text(result.output_summary) if result.output_summary else None,
        error_message=redact_text(result.error_message) if result.error_message else None,
        metadata=metadata,
    )
