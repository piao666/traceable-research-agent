"""Read-only policy boundary shared by MCP-style adapters."""

from __future__ import annotations

from typing import Any


READ_ONLY_HTTP_METHODS = frozenset({"GET"})


def is_http_method_allowed(method: str) -> bool:
    """Allow only HTTP GET regardless of runtime write configuration."""

    return method.strip().upper() in READ_ONLY_HTTP_METHODS


def readonly_policy_metadata(settings_obj: Any) -> dict[str, Any]:
    """Describe the enforced boundary without enabling configured writes."""

    return {
        "read_only": True,
        "write_operations_allowed": False,
        "mcp_adapter_mode": settings_obj.mcp_adapter_mode,
        "readonly_config_ignored": settings_obj.mcp_readonly_mode is False,
        "write_config_ignored": settings_obj.mcp_allow_write_tools is True,
    }
