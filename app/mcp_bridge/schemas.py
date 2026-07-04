"""Shared structures for the MCP Source Pack bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BridgeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    side_effect_free: bool = True
    requires_confirmation: bool = False
    risk_level: str = "low"
    tags: list[str] = field(default_factory=list)

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "read_only": self.read_only,
            "side_effect_free": self.side_effect_free,
            "requires_confirmation": self.requires_confirmation,
            "risk_level": self.risk_level,
            "tags": self.tags,
        }


@dataclass(frozen=True)
class BridgeToolResult:
    success: bool
    output: Any | None = None
    output_summary: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_mcp_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "output_summary": self.output_summary,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


def json_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }

