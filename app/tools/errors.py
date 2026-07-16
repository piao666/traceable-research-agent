"""Canonical cross-provider tool error classification."""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.security.redaction import redact_sensitive_data, redact_text
from app.tools.base import ToolResult


class ToolErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESULT = "invalid_result"
    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    POLICY_ERROR = "policy_error"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


def classify_tool_error(error_type: object, error_message: object = None) -> ToolErrorCategory:
    normalized = str(error_type or "").strip().lower()
    text = f"{normalized} {error_message or ''}".lower()

    if "rate" in normalized and "limit" in normalized or "429" in text:
        return ToolErrorCategory.RATE_LIMITED
    if "timeout" in text or "timed out" in text:
        return ToolErrorCategory.TIMEOUT
    if normalized in {"missing_api_key", "auth_error", "unauthorized", "forbidden"} or any(
        term in text for term in ("authentication", "unauthorized", "invalid credential")
    ):
        return ToolErrorCategory.AUTH_ERROR
    if normalized in {
        "invalid_response",
        "invalid_json",
        "invalid_vectors",
        "invalid_decision",
        "parse_error",
    }:
        return ToolErrorCategory.INVALID_RESULT
    if normalized in {"invalid_args", "invalid_arguments", "invalid_sql"}:
        return ToolErrorCategory.INVALID_REQUEST
    if normalized in {
        "safety_rejected",
        "readonly_policy_rejected",
        "approval_mismatch",
        "disallowed_tool",
    }:
        return ToolErrorCategory.POLICY_ERROR
    if normalized in {"not_found", "db_not_found", "index_missing", "missing_report_file"}:
        return ToolErrorCategory.NOT_FOUND
    if normalized in {
        "disabled",
        "backend_disabled",
        "backend_unavailable",
        "adapter_not_configured",
        "unavailable",
        "not_implemented",
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
        "sql_error",
        "search_error",
        "read_error",
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
