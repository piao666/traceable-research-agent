"""Offline smoke coverage for remote MCP channel policies."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.main import app
from app.mcp.client import (
    MCPRemoteServer,
    get_remote_mcp_channel_summary,
    register_remote_mcp_server,
    reset_remote_mcp_discovery_summary,
)
from app.tools.defaults import register_default_tools
from app.tools.registry import execute_tool, get_tool, list_tools
from app.trace import store


class ConfigurableMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    tools: list[dict[str, Any]] = []
    required_authorization: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.required_authorization and self.headers.get("Authorization") != self.required_authorization:
            self._send({"error": "missing auth"}, 401)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        request = json.loads(raw.decode("utf-8") or "{}")
        method = request.get("method")
        request_id = request.get("id")
        if method == "tools/list":
            self._send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tools}})
            return
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name == "fail":
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "json",
                                    "json": {
                                        "success": False,
                                        "error_message": "fake channel failure",
                                        "metadata": {"error_type": "fake_channel_failure"},
                                    },
                                }
                            ],
                            "isError": True,
                        },
                    }
                )
                return
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "json",
                                "json": {
                                    "success": True,
                                    "output": {"tool": name, "arguments": arguments},
                                    "output_summary": f"{name} returned fake channel output.",
                                    "metadata": {"fixture": name},
                                },
                            }
                        ],
                        "isError": False,
                    },
                }
            )
            return
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        )


def handler_for(
    tools: list[dict[str, Any]],
    required_authorization: str | None = None,
) -> type[ConfigurableMCPHandler]:
    class Handler(ConfigurableMCPHandler):
        pass

    Handler.tools = tools
    Handler.required_authorization = required_authorization
    return Handler


def start_fake_server(
    tools: list[dict[str, Any]],
    required_authorization: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(tools, required_authorization))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/mcp"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def trace_metadata(trace) -> dict[str, Any]:
    try:
        output = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        return {}
    return output.get("metadata", {}) if isinstance(output, dict) else {}


def main() -> None:
    reset_remote_mcp_discovery_summary()
    init_db()
    register_default_tools()
    os.environ["FIRECRAWL_MCP_AUTH_HEADER"] = "Bearer fake-firecrawl-secret"

    firecrawl, firecrawl_url = start_fake_server(
        [
            {
                "name": "scrape",
                "description": "Read-only Firecrawl-style scrape.",
                "input_schema": {"url": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["firecrawl", "web"],
            },
            {
                "name": "search",
                "description": "Read-only Firecrawl-style search.",
                "input_schema": {"query": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["firecrawl", "search"],
            },
            {
                "name": "map",
                "description": "Read-only Firecrawl-style site map.",
                "input_schema": {"url": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["firecrawl", "map"],
            },
            {
                "name": "extract",
                "description": "Read-only Firecrawl-style extraction.",
                "input_schema": {"query": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["firecrawl", "extract"],
            },
            {
                "name": "blocked",
                "description": "Safe but blocked by local config.",
                "input_schema": {"query": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
            },
            {
                "name": "interact",
                "description": "Browser-like side-effect tool that readonly channel must skip.",
                "input_schema": {"selector": "string"},
                "read_only": False,
                "side_effect_free": False,
                "requires_confirmation": True,
                "risk_level": "high",
            },
        ],
        required_authorization="Bearer fake-firecrawl-secret",
    )
    exa, exa_url = start_fake_server(
        [
            {
                "name": "web_search_exa",
                "description": "Read-only Exa semantic search.",
                "input_schema": {"query": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["exa", "search"],
            },
            {
                "name": "web_fetch_exa",
                "description": "Read-only Exa page fetch.",
                "input_schema": {"url": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["exa", "fetch"],
            },
        ]
    )
    context7, context7_url = start_fake_server(
        [
            {
                "name": "resolve-library-id",
                "description": "Read-only Context7 library resolver.",
                "input_schema": {"libraryName": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["context7", "docs"],
            },
            {
                "name": "query-docs",
                "description": "Read-only Context7 docs query.",
                "input_schema": {"query": "string", "libraryId": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "low",
                "tags": ["context7", "docs"],
            },
        ]
    )
    firecrawl_interactive, firecrawl_interactive_url = start_fake_server(
        [
            {
                "name": "interact",
                "description": "Interactive Firecrawl agent action.",
                "input_schema": {"instructions": "string"},
                "read_only": False,
                "side_effect_free": False,
                "requires_confirmation": True,
                "risk_level": "high",
                "tags": ["firecrawl", "interactive"],
            }
        ]
    )
    browser, browser_url = start_fake_server(
        [
            {
                "name": "navigate",
                "description": "Navigate a browser page.",
                "input_schema": {"url": "string"},
                "read_only": False,
                "side_effect_free": False,
                "requires_confirmation": True,
                "risk_level": "high",
                "tags": ["browser"],
            }
        ]
    )
    database, database_url = start_fake_server(
        [
            {
                "name": "query",
                "description": "Read-only database metadata query.",
                "input_schema": {"query": "string"},
                "read_only": True,
                "side_effect_free": True,
                "requires_confirmation": False,
                "risk_level": "medium",
                "tags": ["database"],
            },
            {
                "name": "execute",
                "description": "Database write that should be skipped by readonly policy.",
                "input_schema": {"query": "string"},
                "read_only": False,
                "side_effect_free": False,
                "requires_confirmation": True,
                "risk_level": "high",
                "tags": ["database", "write"],
            },
        ]
    )
    writer, writer_url = start_fake_server(
        [
            {
                "name": "write_file",
                "description": "Write-channel tool that must not be registered.",
                "input_schema": {"path": "string"},
                "read_only": False,
                "side_effect_free": False,
                "requires_confirmation": True,
                "risk_level": "high",
            }
        ]
    )

    try:
        readonly_specs = register_remote_mcp_server(
            MCPRemoteServer(
                "firecrawl",
                firecrawl_url,
                2,
                allowed_tools=("scrape", "search", "map", "extract", "interact", "blocked"),
                blocked_tools=("blocked",),
                headers_env={"Authorization": "FIRECRAWL_MCP_AUTH_HEADER"},
                channel="readonly",
            )
        )
        exa_specs = register_remote_mcp_server(
            MCPRemoteServer("exa", exa_url, 2, channel="readonly")
        )
        context7_specs = register_remote_mcp_server(
            MCPRemoteServer("context7", context7_url, 2, channel="readonly")
        )
        database_specs = register_remote_mcp_server(
            MCPRemoteServer("database_demo", database_url, 2, channel="readonly")
        )
        interactive_specs = register_remote_mcp_server(
            MCPRemoteServer("browser_demo", browser_url, 2, channel="interactive")
        )
        firecrawl_interactive_specs = register_remote_mcp_server(
            MCPRemoteServer("firecrawl_interactive", firecrawl_interactive_url, 2, channel="interactive")
        )
        write_specs = register_remote_mcp_server(
            MCPRemoteServer("writer_demo", writer_url, 2, channel="write")
        )

        registered_names = {
            spec.name
            for spec in (
                readonly_specs
                + exa_specs
                + context7_specs
                + database_specs
                + interactive_specs
                + firecrawl_interactive_specs
                + write_specs
            )
        }
        all_names = {spec.name for spec in list_tools()}
        assert_true(
            {
                "firecrawl.scrape",
                "firecrawl.search",
                "firecrawl.map",
                "firecrawl.extract",
                "exa.web_search_exa",
                "exa.web_fetch_exa",
                "context7.resolve-library-id",
                "context7.query-docs",
                "database_demo.query",
            }
            <= registered_names,
            "readonly tools missing",
        )
        assert_true("firecrawl.blocked" not in all_names, "blocked tool was registered")
        assert_true("firecrawl.interact" not in all_names, "unsafe readonly-channel interact tool was registered")
        assert_true("database_demo.execute" not in all_names, "database write tool was registered")
        assert_true("writer_demo.write_file" not in all_names, "write-channel tool was registered")

        scrape = get_tool("firecrawl.scrape")
        navigate = get_tool("browser_demo.navigate")
        interact = get_tool("firecrawl_interactive.interact")
        assert_true(scrape is not None and scrape.metadata.get("mcp_channel") == "readonly", "readonly channel metadata missing")
        assert_true(navigate is not None and navigate.requires_confirmation, "interactive tool must require confirmation")
        assert_true(navigate.metadata.get("mcp_channel") == "interactive", "interactive channel metadata missing")
        assert_true(interact is not None and interact.requires_confirmation, "Firecrawl interact must require confirmation")

        direct = execute_tool("firecrawl.scrape", {"url": "https://example.com"})
        assert_true(direct.success, "readonly remote call failed")
        assert_true(direct.metadata.get("remote_channel") == "readonly", "remote readonly call channel missing")
        assert_true(direct.metadata.get("headers_configured") is True, "headers_env metadata missing")
        assert_true("fake-firecrawl-secret" not in json.dumps(direct.metadata), "secret leaked into metadata")

        with TestClient(app) as client:
            health = client.get("/mcp/health").json()
            assert_true("channel_summary" in health, "MCP health missing channel summary")
            tools = client.get("/mcp/tools").json()["tools"]
            exposed = {tool["name"]: tool for tool in tools}
            assert_true("firecrawl.scrape" in exposed, "readonly remote tool not exposed by MCP server")
            assert_true("browser_demo.navigate" not in exposed, "interactive tool leaked into readonly MCP endpoint")
            assert_true(exposed["firecrawl.scrape"]["channel"] == "readonly", "MCP tool channel missing")

            blocked_direct = client.post(
                "/api/tools/browser_demo.navigate/execute",
                json={"arguments": {"url": "https://example.com"}},
            )
            assert_true(blocked_direct.status_code == 403, "interactive direct execute was not blocked")

        with SessionLocal() as db:
            run = store.create_agent_run(
                db,
                "Use remote MCP browser evidence and generate a report.",
                "summary",
                "mock",
                ["browser_demo.navigate", "report_writer"],
            )
            plan = plan_task(
                run.task,
                ["browser_demo.navigate", "report_writer"],
                "mock",
                planner_mode="deterministic",
            )
            store.update_agent_run_plan(db, run.run_id, plan)
            waiting = run_plan(db, run.run_id)
            assert_true(waiting["status"] == "waiting_human", "interactive planned run did not wait for HITL")

            failure = store.create_agent_run(
                db,
                "Use remote MCP Firecrawl failure and generate a report.",
                "summary",
                "mock",
                ["firecrawl.fail", "report_writer"],
            )
            fail_server, fail_url = start_fake_server(
                [
                    {
                        "name": "fail",
                        "description": "Readonly failure tool.",
                        "input_schema": {"query": "string"},
                        "read_only": True,
                        "side_effect_free": True,
                        "requires_confirmation": False,
                        "risk_level": "low",
                    }
                ]
            )
            try:
                register_remote_mcp_server(MCPRemoteServer("firecrawl_fail", fail_url, 2, channel="readonly"))
                failure.allowed_tools_json = json.dumps(["firecrawl_fail.fail", "report_writer"])
                fail_plan = plan_task(
                    failure.task,
                    ["firecrawl_fail.fail", "report_writer"],
                    "mock",
                    planner_mode="deterministic",
                )
                store.update_agent_run_plan(db, failure.run_id, fail_plan)
                summary = run_plan(db, failure.run_id)
                traces = store.list_tool_traces(db, failure.run_id)
                assert_true(summary["status"] == "completed", "remote failure caused run failure")
                assert_true(
                    any(
                        trace.tool_name == "firecrawl_fail.fail"
                        and trace.status == "failed"
                        and trace_metadata(trace).get("remote_channel") == "readonly"
                        for trace in traces
                    ),
                    "remote failure trace missing channel metadata",
                )
            finally:
                fail_server.shutdown()
                fail_server.server_close()

        summary = get_remote_mcp_channel_summary()
        assert_true(summary["channels"]["readonly"]["registered_tools"] >= 4, "readonly summary count missing")
        assert_true(summary["channels"]["interactive"]["registered_tools"] >= 1, "interactive summary count missing")
        assert_true(summary["channels"]["write"]["skipped_tools"] >= 1, "write summary skipped count missing")
    finally:
        for server in (firecrawl, exa, context7, firecrawl_interactive, browser, database, writer):
            server.shutdown()
            server.server_close()

    print(
        json.dumps(
            {
                "mcp_channels": "ok",
                "readonly_registered": "ok",
                "interactive_hitl": "ok",
                "write_disabled": "ok",
                "allow_block": "ok",
                "headers_env_redacted": "ok",
                "health_channel_summary": "ok",
                "remote_failure_trace": "ok",
                "deep_research_source_pack": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
