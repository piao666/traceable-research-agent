"""Remote MCP client and registry integration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import requests

from app.config import Settings
from app.mcp.policy import MCPChannel, normalize_mcp_channel
from app.tools.base import RiskLevel, ToolResult, ToolSpec
from app.tools.registry import register_tool


PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_TRANSPORTS = frozenset({"http", "http_json_rpc"})
_DISCOVERY_EVENTS: list[dict[str, Any]] = []


@dataclass(frozen=True)
class MCPRemoteServer:
    name: str
    base_url: str
    timeout_seconds: float = 5.0
    transport: str = "http_json_rpc"
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    blocked_tools: tuple[str, ...] = field(default_factory=tuple)
    headers_env: dict[str, str] = field(default_factory=dict)
    channel: str = MCPChannel.READONLY.value

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _safe_server_name(self.name))
        object.__setattr__(self, "base_url", _normalize_base_url(self.base_url))
        object.__setattr__(self, "transport", _normalize_transport(self.transport))
        object.__setattr__(self, "channel", normalize_mcp_channel(self.channel))
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(str(item).strip() for item in self.allowed_tools if str(item).strip()),
        )
        object.__setattr__(
            self,
            "blocked_tools",
            tuple(str(item).strip() for item in self.blocked_tools if str(item).strip()),
        )
        object.__setattr__(
            self,
            "headers_env",
            {
                str(header).strip(): str(env_name).strip()
                for header, env_name in self.headers_env.items()
                if str(header).strip() and str(env_name).strip()
            },
        )


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


def _risk_for_channel(channel: str, raw_value: object) -> RiskLevel:
    risk = _risk_level(raw_value)
    if channel == MCPChannel.INTERACTIVE.value and risk == RiskLevel.LOW:
        return RiskLevel.MEDIUM
    return risk


def _normalize_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def _normalize_transport(value: object) -> str:
    normalized = str(value or "http_json_rpc").strip().lower().replace("-", "_")
    if normalized == "json_rpc":
        normalized = "http_json_rpc"
    return normalized


def _remote_headers(server: MCPRemoteServer) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header_name, env_name in server.headers_env.items():
        value = os.getenv(env_name)
        if value:
            headers[header_name] = value
    return headers


def _json_rpc(server: MCPRemoteServer, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if server.transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(f"Unsupported MCP transport: {server.transport}")
    payload = {
        "jsonrpc": "2.0",
        "id": f"{server.name}-{method}",
        "method": method,
        "params": params or {},
    }
    response = requests.post(
        server.base_url,
        json=payload,
        headers=_remote_headers(server),
        timeout=server.timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Remote MCP response must be a JSON object.")
    return data


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _headers_env(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(header).strip(): str(env_name).strip()
            for header, env_name in value.items()
            if str(header).strip() and str(env_name).strip()
        }
    return {}


def _matches_filter(value: str, registry_name: str, filters: tuple[str, ...]) -> bool:
    normalized = {item.strip().lower() for item in filters if item.strip()}
    return value.lower() in normalized or registry_name.lower() in normalized


def _base_remote_metadata(server: MCPRemoteServer, remote_tool_name: str) -> dict[str, Any]:
    return {
        "tool_source": "mcp_remote",
        "remote_server": server.name,
        "remote_channel": server.channel,
        "mcp_channel": server.channel,
        "remote_tool_name": remote_tool_name,
        "remote_registry_name": f"{server.name}.{remote_tool_name}",
        "remote_base_url": server.base_url,
        "mcp_transport": server.transport,
        "headers_configured": bool(server.headers_env),
        "headers_env": sorted(server.headers_env.values()),
    }


def _remote_tool_metadata(server: MCPRemoteServer, raw_tool: dict[str, Any]) -> dict[str, Any]:
    remote_name = str(raw_tool.get("name") or "").strip()
    if not remote_name:
        raise ValueError("Remote MCP tool metadata is missing name.")
    registry_name = f"{server.name}.{remote_name}"
    read_only = bool(raw_tool.get("read_only", True))
    side_effect_free = bool(raw_tool.get("side_effect_free", True))
    requires_confirmation = bool(raw_tool.get("requires_confirmation", False))
    return {
        "registry_name": registry_name,
        "remote_name": remote_name,
        "server_name": server.name,
        "channel": server.channel,
        "transport": server.transport,
        "description": str(raw_tool.get("description") or f"Remote MCP tool {remote_name}."),
        "input_schema": raw_tool.get("input_schema") if isinstance(raw_tool.get("input_schema"), dict) else {},
        "output_schema": raw_tool.get("output_schema") if isinstance(raw_tool.get("output_schema"), dict) else {},
        "read_only": read_only,
        "side_effect_free": side_effect_free,
        "requires_confirmation": requires_confirmation,
        "risk_level": _risk_for_channel(server.channel, raw_tool.get("risk_level")),
        "tags": [str(tag) for tag in raw_tool.get("tags") or [] if str(tag).strip()],
    }


def _policy_decision(server: MCPRemoteServer, metadata: dict[str, Any]) -> tuple[bool, str]:
    remote_name = metadata["remote_name"]
    registry_name = metadata["registry_name"]
    if server.transport not in SUPPORTED_TRANSPORTS:
        return False, "unsupported_transport"
    if _matches_filter(remote_name, registry_name, server.blocked_tools):
        return False, "blocked_by_config"
    if server.allowed_tools and not _matches_filter(remote_name, registry_name, server.allowed_tools):
        return False, "not_in_allowed_tools"
    if server.channel == MCPChannel.WRITE.value:
        return False, "write_channel_disabled"
    if server.channel == MCPChannel.READONLY.value:
        safe = (
            metadata["read_only"]
            and metadata["side_effect_free"]
            and not metadata["requires_confirmation"]
        )
        if not safe:
            return False, "not_readonly_safe"
        return True, "readonly_auto_registered"
    if server.channel == MCPChannel.INTERACTIVE.value:
        return True, "interactive_requires_confirmation"
    return False, "unknown_channel"


def _record_discovery_event(
    server: MCPRemoteServer,
    *,
    remote_tool_name: str | None,
    registry_name: str | None,
    status: str,
    reason: str,
) -> None:
    _DISCOVERY_EVENTS.append(
        {
            "server": server.name,
            "channel": server.channel,
            "transport": server.transport,
            "remote_tool_name": remote_tool_name,
            "registry_name": registry_name,
            "status": status,
            "reason": reason,
            "headers_configured": bool(server.headers_env),
        }
    )


def reset_remote_mcp_discovery_summary() -> None:
    """Clear the process-local remote discovery audit summary."""

    _DISCOVERY_EVENTS.clear()


def get_remote_mcp_channel_summary() -> dict[str, Any]:
    """Return a redacted channel summary for health checks and smoke tests."""

    channels: dict[str, dict[str, Any]] = {
        channel.value: {
            "configured_servers": set(),
            "registered_tools": set(),
            "skipped_tools": 0,
            "errors": 0,
        }
        for channel in MCPChannel
    }
    for event in _DISCOVERY_EVENTS:
        channel = normalize_mcp_channel(event.get("channel"))
        bucket = channels[channel]
        server_name = event.get("server")
        if server_name:
            bucket["configured_servers"].add(str(server_name))
        if event.get("status") == "registered":
            registry_name = event.get("registry_name") or event.get("remote_tool_name")
            if registry_name:
                bucket["registered_tools"].add(str(registry_name))
        elif event.get("status") == "error":
            bucket["errors"] += 1
        else:
            bucket["skipped_tools"] += 1
    redacted = {}
    for channel, data in channels.items():
        redacted[channel] = {
            "configured_servers": len(data["configured_servers"]),
            "registered_tools": len(data["registered_tools"]),
            "skipped_tools": data["skipped_tools"],
            "errors": data["errors"],
        }
    return {
        "supported_transports": sorted(SUPPORTED_TRANSPORTS),
        "channels": redacted,
        "events": list(_DISCOVERY_EVENTS[-50:]),
    }


def discover_remote_mcp_tools(server: MCPRemoteServer) -> list[dict[str, Any]]:
    """Return policy-approved remote MCP tool metadata from one endpoint."""

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
        allowed, reason = _policy_decision(server, metadata)
        metadata["policy_decision"] = reason
        if allowed:
            _record_discovery_event(
                server,
                remote_tool_name=metadata["remote_name"],
                registry_name=metadata["registry_name"],
                status="registered",
                reason=reason,
            )
            tools.append(metadata)
        else:
            _record_discovery_event(
                server,
                remote_tool_name=metadata["remote_name"],
                registry_name=metadata["registry_name"],
                status="skipped",
                reason=reason,
            )
    return tools


def call_remote_mcp_tool(
    server: MCPRemoteServer,
    remote_tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Call one remote MCP tool and convert success or failure into ToolResult."""

    started = perf_counter()
    base_metadata = _base_remote_metadata(server, remote_tool_name)
    try:
        data = _json_rpc(
            server,
            "tools/call",
            {"name": remote_tool_name, "arguments": arguments or {}},
        )
        latency_ms = int((perf_counter() - started) * 1000)
        metadata = dict(base_metadata)
        metadata["latency_ms"] = latency_ms
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
        metadata.update(base_metadata)
        metadata["latency_ms"] = latency_ms
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
        metadata = dict(base_metadata)
        metadata.update({"latency_ms": latency_ms, "error_type": "mcp_remote_call_failed"})
        return ToolResult(
            success=False,
            error_message=f"Remote MCP tool call failed: {exc}",
            metadata=metadata,
        )


def register_remote_mcp_server(server: MCPRemoteServer) -> list[ToolSpec]:
    """Discover remote MCP tools and register policy-approved tools locally."""

    registered: list[ToolSpec] = []
    try:
        discovered = discover_remote_mcp_tools(server)
    except Exception as exc:
        _record_discovery_event(
            server,
            remote_tool_name=None,
            registry_name=None,
            status="error",
            reason=str(exc)[:300],
        )
        raise

    for metadata in discovered:
        remote_name = metadata["remote_name"]
        channel = metadata["channel"]
        requires_confirmation = channel == MCPChannel.INTERACTIVE.value or bool(
            metadata["requires_confirmation"]
        )
        tags = set(metadata["tags"])
        tags.update({"mcp_remote", f"mcp-channel-{channel}"})
        if channel == MCPChannel.READONLY.value:
            tags.add("read-only")
        if channel == MCPChannel.INTERACTIVE.value:
            tags.add("interactive")
        spec = ToolSpec(
            name=metadata["registry_name"],
            description=f"[remote:{metadata['server_name']}:{channel}] {metadata['description']}",
            input_schema=metadata["input_schema"],
            output_schema=metadata["output_schema"],
            risk_level=metadata["risk_level"],
            requires_confirmation=requires_confirmation,
            enabled=True,
            timeout_seconds=int(max(1, server.timeout_seconds)),
            tags=sorted(tags),
            metadata={
                "tool_source": "mcp_remote",
                "mcp_channel": channel,
                "remote_server": metadata["server_name"],
                "remote_tool_name": remote_name,
                "remote_registry_name": metadata["registry_name"],
                "remote_base_url": server.base_url,
                "mcp_transport": server.transport,
                "read_only": metadata["read_only"],
                "side_effect_free": metadata["side_effect_free"],
                "policy_decision": metadata["policy_decision"],
                "headers_configured": bool(server.headers_env),
                "headers_env": sorted(server.headers_env.values()),
            },
        )

        def _handler(args: dict[str, Any], _server: MCPRemoteServer = server, _remote_name: str = remote_name) -> ToolResult:
            return call_remote_mcp_tool(_server, _remote_name, args)

        register_tool(spec, _handler)
        registered.append(spec)
    return registered


def parse_remote_servers(
    raw: str | None,
    *,
    default_channel: str = MCPChannel.READONLY.value,
) -> list[MCPRemoteServer]:
    """Parse JSON or semicolon syntax into remote server configs."""

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
                timeout = float(item.get("timeout_seconds", item.get("timeout", 5)))
            except (TypeError, ValueError):
                timeout = 5.0
            servers.append(
                MCPRemoteServer(
                    name=name,
                    base_url=base_url,
                    timeout_seconds=timeout,
                    transport=str(item.get("transport") or "http_json_rpc"),
                    allowed_tools=_string_list(item.get("allowed_tools")),
                    blocked_tools=_string_list(item.get("blocked_tools")),
                    headers_env=_headers_env(item.get("headers_env")),
                    channel=normalize_mcp_channel(item.get("channel") or default_channel),
                )
            )
        return servers

    for chunk in text.split(";"):
        if not chunk.strip() or "=" not in chunk:
            continue
        name, base_url = chunk.split("=", 1)
        if name.strip() and base_url.strip():
            servers.append(
                MCPRemoteServer(
                    name=name.strip(),
                    base_url=base_url.strip(),
                    timeout_seconds=5.0,
                    channel=default_channel,
                )
            )
    return servers


def _servers_from_settings(settings_obj: Settings) -> list[MCPRemoteServer]:
    servers: list[MCPRemoteServer] = []
    if settings_obj.mcp_remote_registry_enabled:
        servers.extend(
            parse_remote_servers(
                settings_obj.mcp_remote_servers,
                default_channel=MCPChannel.READONLY.value,
            )
        )
    servers.extend(
        parse_remote_servers(
            settings_obj.mcp_channel_readonly_servers,
            default_channel=MCPChannel.READONLY.value,
        )
    )
    servers.extend(
        parse_remote_servers(
            settings_obj.mcp_channel_interactive_servers,
            default_channel=MCPChannel.INTERACTIVE.value,
        )
    )
    servers.extend(
        parse_remote_servers(
            settings_obj.mcp_channel_write_servers,
            default_channel=MCPChannel.WRITE.value,
        )
    )
    return servers


def remote_mcp_servers_configured(settings_obj: Settings) -> bool:
    """Return whether settings declare any remote MCP server endpoint."""

    return bool(_servers_from_settings(settings_obj))


def register_remote_mcp_tools_from_settings(settings_obj: Settings) -> list[ToolSpec]:
    """Opt-in remote MCP discovery from application settings."""

    registered: list[ToolSpec] = []
    for server in _servers_from_settings(settings_obj):
        try:
            registered.extend(register_remote_mcp_server(server))
        except Exception:
            continue
    return registered
