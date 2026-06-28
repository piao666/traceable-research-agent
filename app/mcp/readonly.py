"""Backward-compatible read-only policy helpers."""

from __future__ import annotations

from app.mcp.policy import (
    READ_ONLY_HTTP_METHODS,
    is_http_method_allowed as _policy_method_allowed,
    readonly_policy_metadata,
)


def is_http_method_allowed(method: str) -> bool:
    """Allow only HTTP GET for legacy GitHub adapter checks."""

    return _policy_method_allowed(method)
