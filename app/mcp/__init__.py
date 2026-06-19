"""Minimal policy helpers for future MCP-compatible adapters."""

from app.mcp.readonly import is_http_method_allowed, readonly_policy_metadata

__all__ = ["is_http_method_allowed", "readonly_policy_metadata"]
