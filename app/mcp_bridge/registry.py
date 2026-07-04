"""Provider registry for the MCP Source Pack bridge."""

from __future__ import annotations

import os

from app.mcp_bridge.providers.base import SourcePackProvider, env_bool, env_int
from app.mcp_bridge.providers.context7 import Context7Provider
from app.mcp_bridge.providers.exa import ExaProvider
from app.mcp_bridge.providers.firecrawl import FirecrawlProvider
from app.mcp_bridge.schemas import BridgeTool


PROVIDER_CLASSES: dict[str, type[SourcePackProvider]] = {
    "firecrawl": FirecrawlProvider,
    "exa": ExaProvider,
    "context7": Context7Provider,
}


class SourcePackRegistry:
    def __init__(self, providers: list[SourcePackProvider]) -> None:
        self.providers = providers
        self._tool_map: dict[str, SourcePackProvider] = {}
        for provider in providers:
            for tool in provider.list_tools():
                self._tool_map[tool.name] = provider

    @classmethod
    def from_env(cls) -> "SourcePackRegistry":
        enabled = [
            item.strip().lower()
            for item in os.getenv("MCP_BRIDGE_ENABLED_PROVIDERS", "firecrawl,exa,context7").split(",")
            if item.strip()
        ]
        fake_mode = env_bool("MCP_BRIDGE_FAKE_MODE", True)
        timeout_seconds = float(os.getenv("MCP_BRIDGE_TIMEOUT_SECONDS", "20") or 20)
        max_results = env_int("MCP_BRIDGE_MAX_RESULTS", 5)
        max_content_chars = env_int("MCP_BRIDGE_MAX_CONTENT_CHARS", 12000)
        providers: list[SourcePackProvider] = []
        for name in enabled:
            cls_obj = PROVIDER_CLASSES.get(name)
            if cls_obj is None:
                continue
            providers.append(
                cls_obj(
                    fake_mode=fake_mode,
                    timeout_seconds=timeout_seconds,
                    max_results=max_results,
                    max_content_chars=max_content_chars,
                )
            )
        return cls(providers)

    def list_tools(self) -> list[BridgeTool]:
        tools: list[BridgeTool] = []
        for provider in self.providers:
            tools.extend(provider.list_tools())
        return tools

    def provider_for_tool(self, tool_name: str) -> SourcePackProvider | None:
        return self._tool_map.get(tool_name)

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "mcp-source-pack-bridge",
            "providers": [provider.name for provider in self.providers],
            "tool_count": len(self._tool_map),
        }

