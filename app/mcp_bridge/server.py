"""FastAPI HTTP JSON-RPC server for the MCP Source Pack bridge."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from app.mcp_bridge.registry import SourcePackRegistry


PROTOCOL_VERSION = "2024-11-05"


def create_app(registry: SourcePackRegistry | None = None) -> FastAPI:
    bridge_registry = registry or SourcePackRegistry.from_env()
    app = FastAPI(title="MCP Source Pack Bridge")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return bridge_registry.health()

    @app.get("/tools")
    async def tools() -> dict[str, object]:
        return {"tools": [tool.to_mcp_tool() for tool in bridge_registry.list_tools()]}

    @app.post("/mcp")
    async def mcp(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            return _json_rpc_error(None, -32700, "Invalid JSON.")
        if not isinstance(payload, dict):
            return _json_rpc_error(None, -32600, "JSON-RPC payload must be an object.")
        request_id = payload.get("id")
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": "mcp-source-pack-bridge", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _json_rpc_result(
                request_id,
                {"tools": [tool.to_mcp_tool() for tool in bridge_registry.list_tools()]},
            )
        if method == "tools/call":
            tool_name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            provider = bridge_registry.provider_for_tool(tool_name)
            if provider is None:
                return _json_rpc_error(request_id, -32602, f"Unknown tool: {tool_name}")
            result = provider.call_tool(tool_name, arguments)
            return _json_rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "json",
                            "json": result.to_mcp_payload(),
                        }
                    ],
                    "isError": not result.success,
                },
            )
        return _json_rpc_error(request_id, -32601, f"Unknown method: {method}")

    return app


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


app = create_app()

