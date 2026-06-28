"""Remote MCP client and registry integration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import requests

from app.config import Settings
from app.tools.base import RiskLevel, ToolResult, ToolSpec
from app.tools.registry import register_tool


PROTOCOL_VERSION = "2024-11-05"


@dataclass(frozen=True)
class MCPRemoteServer:
    name: str
    base_url: str
    timeout_seconds: float = 5.0


def _safe_server_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    return safe or "remote"


def _risk_level(value: object) -> RiskLevel:
    normalized = str(value or "low").strip().lower()
    if normalized == "high":
        return RiskLevel.HIGH
    if normalized == "medium":
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _json_rpc(server: MCPRemoteServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": f"{server.name}-{method}",
        "method": method,
        "params": params or {},
    }
    response = requests.post(
        _normalize_base_url(server.base_url),
        json=payload,
        timeout=server.timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Remote MCP response must be a JSON object.")
    return data


def _remote_tool_metadata(server: MCPRemoteServer, raw_tool: dict[str, Any]) -> dict[str, Any]:
    remote_name = str(raw_tool.get("name") or "").strip()
    if not remote_name:
        raise ValueError("Remote MCP tool metadata is missing name.")
    server_name = _safe_server_name(server.name)
    return {
        "registry_name": f"{server_name}.{remote_name}",
        "remote_name": remote_name,
        "server_name": server_name,
        "description": str(raw_tool.get("description") or f"Remote MCP tool {remote_name}."),
        "input_schema": raw_tool.get("input_schema") if isinstance(raw_tool.get("input_schema"), dict) else {},
        "output_schema": raw_tool.get("output_schema") if isinstance(raw_tool.get("output_schema"), dict) else {},
        "read_only": bool(raw_tool.get("read_only", True)),
        "side_effect_free": bool(raw_tool.get("side_effect_free", True)),
        "requires_confirmation": bool(raw_tool.get("requires_confirmation", False)),
        "risk_level": _risk_level(raw_tool.get("risk_level")),
        "tags": [str(tag) for tag in raw_tool.get("tags") or [] if str(tag).strip()],
    }


def discover_remote_mcp_tools(server: MCPRemoteServer) -> list[dict[str, Any]]:
    """Return read-only remote MCP tool metadata from one JSON-RPC endpoint."""

    data = _json_rpc(server, "tools/list")
    if data.get("error"):
        raise ValueError(str(data["error"].get("message") or data["error"]))
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    raw_tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(raw_tools, list):
        raise ValueError("Remote MCP tools/list result is missing tools array.")
    tools: list[dict[str, Any]] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            continue
        metadata = _remote_tool_metadata(server, raw_tool)
        if metadata["read_only"] and metadata["side_effect_free"] and not metadata["requires_confirmation"]:
            tools.append(metadata)
    return tools


def call_remote_mcp_tool(
    server: MCPRemoteServer,
    remote_tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Call one remote MCP tool and convert success or failure into ToolResult."""

    started = perf_counter()
    try:
        data = _json_rpc(
            server,
            "tools/call",
            {"name": remote_tool_name, "arguments": arguments or {}},
        )
        latency_ms = int((perf_counter() - started) * 1000)
        metadata = {
            "tool_source": "mcp_remote",
            "remote_server": _safe_server_name(server.name),
            "remote_tool_name": remote_tool_name,
            "remote_base_url": _normalize_base_url(server.base_url),
            "latency_ms": latency_ms,
        }
        if data.get("error"):
            message = str(data["error"].get("message") or data["error"])
            metadata["error_type"] = "mcp_remote_error"
            return ToolResult(success=False, error_message=message, metadata=metadata)

        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        content = result.get("content") if isinstance(result, dict) else None
        payload: dict[str, Any] = {}
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and isinstance(first.get("json"), dict):
                payload = first["json"]
        if not payload and isinstance(result, dict):
            payload = result

        remote_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        metadata.update(remote_metadata)
        metadata.update(
            {
                "tool_source": "mcp_remote",
                "remote_server": _safe_server_name(server.name),
                "remote_tool_name": remote_tool_name,
                "remote_base_url": _normalize_base_url(server.base_url),
                "latency_ms": latency_ms,
            }
        )
        success = bool(payload.get("success", not result.get("isError", False)))
        return ToolResult(
            success=success,
            output=payload.get("output"),
            output_summary=payload.get("output_summary"),
            error_message=payload.get("error_message"),
            metadata=metadata,
        )
    except Exception as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        return ToolResult(
            success=False,
            error_message=f"Remote MCP tool call failed: {exc}",
            metadata={
                "tool_source": "mcp_remote",
                "remote_server": _safe_server_name(server.name),
                "remote_tool_name": remote_tool_name,
                "remote_base_url": _normalize_base_url(server.base_url),
                "latency_ms": latency_ms,
                "error_type": "mcp_remote_call_failed",
            },
        )


def register_remote_mcp_server(server: MCPRemoteServer) -> list[ToolSpec]:
    """Discover safe remote MCP tools and register them in the local registry."""

    registered: list[ToolSpec] = []
    for metadata in discover_remote_mcp_tools(server):
        remote_name = metadata["remote_name"]
        spec = ToolSpec(
            name=metadata["registry_name"],
            description=f"[remote:{metadata['server_name']}] {metadata['description']}",
            input_schema=metadata["input_schema"],
            output_schema=metadata["output_schema"],
            risk_level=metadata["risk_level"],
            requires_confirmation=False,
            enabled=True,
            timeout_seconds=int(max(1, server.timeout_seconds)),
            tags=sorted(set(metadata["tags"] + ["mcp_remote", "read-only"])),
        )

        def _handler(args: dict[str, Any], _server: MCPRemoteServer = server, _remote_name: str = remote_name) -> ToolResult:
            return call_remote_mcp_tool(_server, _remote_name, args)

        register_tool(spec, _handler)
        registered.append(spec)
    return registered


def parse_remote_servers(raw: str | None) -> list[MCPRemoteServer]:
    """Parse MCP_REMOTE_SERVERS JSON or semicolon syntax."""

    text = str(raw or "").strip()
    if not text:
        return []
    servers: list[MCPRemoteServer] = []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            base_url = str(item.get("base_url") or item.get("url") or "").strip()
            if not name or not base_url:
                continue
            try:
                timeout = float(item.get("timeout_seconds", 5))
            except (TypeError, ValueError):
                timeout = 5.0
            servers.append(MCPRemoteServer(_safe_server_name(name), base_url, timeout))
        return servers

    for chunk in text.split(";"):
        if not chunk.strip() or "=" not in chunk:
            continue
        name, base_url = chunk.split("=", 1)
        if name.strip() and base_url.strip():
            servers.append(MCPRemoteServer(_safe_server_name(name), base_url.strip(), 5.0))
    return servers


def register_remote_mcp_tools_from_settings(settings_obj: Settings) -> list[ToolSpec]:
    """Opt-in remote MCP discovery from application settings."""

    if not settings_obj.mcp_remote_registry_enabled:
        return []
    registered: list[ToolSpec] = []
    for server in parse_remote_servers(settings_obj.mcp_remote_servers):
        try:
            registered.extend(register_remote_mcp_server(server))
        except Exception:
            continue
    return registered
