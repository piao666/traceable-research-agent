"""Schemas for the lightweight MCP-compatible HTTP server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MCPToolMetadata(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    side_effect_free: bool = True
    requires_confirmation: bool = False
    risk_level: str = "low"
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    local_tool_name: str | None = None


class MCPToolListResponse(BaseModel):
    server: str
    protocol_version: str
    tools: list[MCPToolMetadata]


class MCPTraceOptions(BaseModel):
    run_id: str | None = None
    step_no: int = 1


class MCPToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    trace: MCPTraceOptions | None = None


class MCPToolCallResponse(BaseModel):
    success: bool
    output: Any | None = None
    output_summary: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPJsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = None


class MCPJsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    result: Any | None = None
    error: dict[str, Any] | None = None
