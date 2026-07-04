"""Smoke tests for the MCP Source Pack Bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
import uvicorn
from fastapi.testclient import TestClient

from app.mcp.client import MCPRemoteServer, register_remote_mcp_server
from app.mcp_bridge.server import create_app
from app.tools.registry import execute_tool, get_tool


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": f"smoke-{method}", "method": method, "params": params or {}},
    )
    assert_true(response.status_code == 200, f"RPC HTTP failed: {response.status_code}")
    payload = response.json()
    assert_true("error" not in payload, f"RPC returned error: {payload}")
    return payload["result"]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_uvicorn(app, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            if response.status_code == 200:
                return server
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("Bridge server did not start in time.")


def main() -> None:
    original_env = {
        key: os.environ.get(key)
        for key in (
            "MCP_BRIDGE_FAKE_MODE",
            "MCP_BRIDGE_ENABLED_PROVIDERS",
            "FIRECRAWL_API_KEY",
            "EXA_API_KEY",
            "CONTEXT7_BASE_URL",
        )
    }
    try:
        os.environ["MCP_BRIDGE_FAKE_MODE"] = "true"
        os.environ["MCP_BRIDGE_ENABLED_PROVIDERS"] = "firecrawl,exa,context7"
        app = create_app()
        with TestClient(app) as client:
            health = client.get("/health").json()
            assert_true(health["tool_count"] == 9, f"unexpected tool count: {health}")
            initialize = rpc(client, "initialize")
            assert_true(initialize["serverInfo"]["name"] == "mcp-source-pack-bridge", "initialize failed")
            listed = rpc(client, "tools/list")
            tools = {tool["name"]: tool for tool in listed["tools"]}
            expected = {
                "firecrawl.search",
                "firecrawl.scrape",
                "firecrawl.map",
                "firecrawl.extract",
                "exa.web_search_exa",
                "exa.web_fetch_exa",
                "exa.web_search_advanced_exa",
                "context7.resolve-library-id",
                "context7.query-docs",
            }
            assert_true(expected <= set(tools), f"missing source-pack tools: {set(tools)}")
            for tool in tools.values():
                assert_true(tool["read_only"] is True, f"tool not readonly: {tool}")
                assert_true(tool["side_effect_free"] is True, f"tool not side-effect-free: {tool}")
                assert_true(tool["requires_confirmation"] is False, f"tool requires confirmation: {tool}")

            search = rpc(client, "tools/call", {"name": "firecrawl.search", "arguments": {"query": "agent research"}})
            search_payload = search["content"][0]["json"]
            assert_true(search_payload["success"] is True, "fake Firecrawl search failed")
            assert_true(search_payload["metadata"]["evidence_role"] == "discovery", "search role should be discovery")
            scrape = rpc(
                client,
                "tools/call",
                {"name": "firecrawl.scrape", "arguments": {"url": "https://example.com/source"}},
            )
            scrape_payload = scrape["content"][0]["json"]
            assert_true(scrape_payload["metadata"]["evidence_role"] == "support", "scrape role should be support")
            assert_true("markdown" in scrape_payload["output"], "scrape content missing")

        port = free_port()
        server = start_uvicorn(app, port)
        try:
            registered = register_remote_mcp_server(
                MCPRemoteServer(
                    "source_pack",
                    f"http://127.0.0.1:{port}/mcp",
                    3,
                    allowed_tools=("firecrawl.search", "firecrawl.scrape", "exa.web_search_exa", "context7.query-docs"),
                    channel="readonly",
                )
            )
            names = {spec.name for spec in registered}
            assert_true("source_pack.firecrawl.search" in names, f"remote registration missing: {names}")
            spec = get_tool("source_pack.firecrawl.search")
            assert_true(spec is not None and spec.metadata["remote_tool_name"] == "firecrawl.search", "metadata missing")
            result = execute_tool("source_pack.firecrawl.search", {"query": "bridge integration"})
            assert_true(result.success, "registered Bridge tool execution failed")
            serialized = json.dumps(result.metadata, ensure_ascii=False)
            assert_true("fake-firecrawl-secret" not in serialized, "secret leaked into metadata")
        finally:
            server.should_exit = True

        os.environ["MCP_BRIDGE_FAKE_MODE"] = "false"
        os.environ.pop("FIRECRAWL_API_KEY", None)
        real_missing_key_app = create_app()
        with TestClient(real_missing_key_app) as client:
            failed = rpc(client, "tools/call", {"name": "firecrawl.search", "arguments": {"query": "missing key"}})
            payload = failed["content"][0]["json"]
            assert_true(payload["success"] is False, "missing key should be structured failure")
            assert_true(payload["metadata"]["error_type"] == "missing_api_key", "missing key error type lost")

        print(
            json.dumps(
                {
                    "mcp_source_pack_bridge": "ok",
                    "fake_mode": "ok",
                    "tools_list": "ok",
                    "remote_registration": "ok",
                    "structured_failure": "ok",
                    "secret_redaction": "ok",
                },
                indent=2,
            )
        )
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()

