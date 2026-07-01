"""Read-only MCP tool exposure policy."""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.tools.base import RiskLevel, ToolSpec


READ_ONLY_HTTP_METHODS = frozenset({"GET"})
SEMANTIC_READ_ONLY_POST_TOOLS = frozenset({"tavily_search"})
WRITE_CAPABLE_TOOL_NAMES = frozenset(
    {
        "github_issue_create",
        "github_pr_comment",
        "github_repository_mutation",
        "github_push",
        "file_writer",
        "sql_execute",
        "db_write",
    }
)


class MCPChannel(str, Enum):
    READONLY = "readonly"
    INTERACTIVE = "interactive"
    WRITE = "write"


def normalize_mcp_channel(value: object) -> str:
    """Return a stable MCP channel name."""

    normalized = str(value or MCPChannel.READONLY.value).strip().lower()
    if normalized in {item.value for item in MCPChannel}:
        return normalized
    return MCPChannel.READONLY.value


def is_http_method_allowed(method: str, *, tool_name: str | None = None) -> bool:
    """Allow GET, plus semantic read-only POST for explicitly safe tools."""

    normalized_method = method.strip().upper()
    if normalized_method in READ_ONLY_HTTP_METHODS:
        return True
    if normalized_method == "POST" and tool_name in SEMANTIC_READ_ONLY_POST_TOOLS:
        return True
    return False


def readonly_policy_metadata(settings_obj: Any) -> dict[str, Any]:
    """Describe the enforced boundary without enabling configured writes."""

    return {
        "read_only": True,
        "write_operations_allowed": False,
        "mcp_adapter_mode": settings_obj.mcp_adapter_mode,
        "readonly_config_ignored": settings_obj.mcp_readonly_mode is False,
        "write_config_ignored": settings_obj.mcp_allow_write_tools is True,
    }


def is_tool_exposable(spec: ToolSpec, *, alias: str | None = None) -> bool:
    """Return whether a local tool may be exposed by the MCP server."""

    exposed_name = alias or spec.name
    if exposed_name in WRITE_CAPABLE_TOOL_NAMES or spec.name in WRITE_CAPABLE_TOOL_NAMES:
        return False
    if tool_channel(spec) != MCPChannel.READONLY.value:
        return False
    if spec.requires_confirmation:
        return False
    if not spec.enabled:
        return False
    return is_tool_read_only(spec)


def is_tool_read_only(spec: ToolSpec) -> bool:
    """Infer read-only status from risk and registry tags."""

    tags = {tag.strip().lower() for tag in spec.tags}
    if "write" in tags or "mutation" in tags:
        return False
    if spec.risk_level == RiskLevel.HIGH:
        return False
    if "read-only" in tags:
        return True
    return spec.name in {"file_reader", "sql_query", "rag_search", "mcp_github_search", "tavily_search"}


def tool_channel(spec: ToolSpec | None) -> str:
    """Return the configured MCP channel for a tool."""

    if spec is None:
        return MCPChannel.READONLY.value
    return normalize_mcp_channel((spec.metadata or {}).get("mcp_channel"))


def is_tool_auto_executable(spec: ToolSpec | None) -> bool:
    """Return whether a tool may run without HITL in agent/direct paths."""

    if spec is None or not spec.enabled:
        return False
    if tool_channel(spec) == MCPChannel.WRITE.value:
        return False
    if spec.requires_confirmation:
        return False
    return True


def requires_interactive_confirmation(spec: ToolSpec | None) -> bool:
    """Return whether a registered tool must wait for human confirmation."""

    if spec is None:
        return False
    return tool_channel(spec) == MCPChannel.INTERACTIVE.value or spec.requires_confirmation


def is_parallel_safe_tool(spec: ToolSpec | None) -> bool:
    """Return whether a tool may run in automatic parallel execution."""

    if spec is None:
        return False
    return (
        is_tool_auto_executable(spec)
        and tool_channel(spec) == MCPChannel.READONLY.value
        and is_tool_read_only(spec)
    )


def mcp_policy_metadata(spec: ToolSpec, *, alias: str | None = None) -> dict[str, Any]:
    """Return stable MCP metadata required by external clients."""

    channel = tool_channel(spec)
    read_only = is_tool_read_only(spec)
    return {
        "name": alias or spec.name,
        "local_tool_name": spec.name,
        "read_only": read_only,
        "side_effect_free": read_only and channel == MCPChannel.READONLY.value,
        "requires_confirmation": spec.requires_confirmation,
        "risk_level": spec.risk_level.value,
        "channel": channel,
        "policy": {
            "channel": channel,
            "auto_executable": is_tool_auto_executable(spec),
            "mcp_exposable": is_tool_exposable(spec, alias=alias),
            "requires_confirmation": requires_interactive_confirmation(spec),
        },
    }
