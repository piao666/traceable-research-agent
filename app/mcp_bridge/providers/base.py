"""Provider base classes and HTTP helpers for the MCP Source Pack bridge."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

import requests

from app.mcp_bridge.schemas import BridgeTool, BridgeToolResult


class SourcePackProvider(ABC):
    name: str

    def __init__(self, *, fake_mode: bool, timeout_seconds: float, max_results: int, max_content_chars: int) -> None:
        self.fake_mode = fake_mode
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, max_results)
        self.max_content_chars = max(500, max_content_chars)

    @abstractmethod
    def list_tools(self) -> list[BridgeTool]:
        """Return safe tools exposed by this provider."""

    @abstractmethod
    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> BridgeToolResult:
        """Call one provider tool."""

    def _metadata(self, tool_name: str, *, data_source: str) -> dict[str, Any]:
        return {
            "provider": self.name,
            "tool": tool_name,
            "data_source": data_source,
            "fake_mode": self.fake_mode,
        }

    def _failure(self, tool_name: str, message: str, *, error_type: str = "provider_error") -> BridgeToolResult:
        metadata = self._metadata(tool_name, data_source="provider_failure")
        metadata["error_type"] = error_type
        return BridgeToolResult(
            success=False,
            error_message=message[:600],
            output_summary=f"{self.name}.{tool_name} failed: {message[:160]}",
            metadata=metadata,
        )

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        tool_name: str,
    ) -> tuple[dict[str, Any] | None, BridgeToolResult | None]:
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return None, self._failure(tool_name, "Provider returned non-object JSON.", error_type="invalid_response")
            return data, None
        except requests.RequestException as exc:
            return None, self._failure(tool_name, f"HTTP request failed: {type(exc).__name__}", error_type="http_error")
        except json.JSONDecodeError:
            return None, self._failure(tool_name, "Provider returned invalid JSON.", error_type="invalid_json")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def trim_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[:max_chars]

