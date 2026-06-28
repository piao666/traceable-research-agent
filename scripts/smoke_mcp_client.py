"""Offline smoke coverage for remote MCP client and unified Tool Registry."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task
from app.agent.react_executor import run_react_task
from app.agent.executor import run_plan
from app.config import Settings
from app.database import SessionLocal, init_db
from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.mcp.client import MCPRemoteServer, register_remote_mcp_server
from app.tools.defaults import register_default_tools
from app.tools.registry import execute_tool, get_tool, list_tools
from app.trace import store


class FakeMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        request = json.loads(raw.decode("utf-8") or "{}")
        method = request.get("method")
        request_id = request.get("id")
        if method == "tools/list":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo a short text payload from a fake remote MCP server.",
                                "input_schema": {"text": "string"},
                                "output_schema": {"echo": "string"},
                                "read_only": True,
                                "side_effect_free": True,
                                "requires_confirmation": False,
                                "risk_level": "low",
                                "tags": ["fake", "echo"],
                            },
                            {
                                "name": "unstable",
                                "description": "Return a structured fake remote failure.",
                                "input_schema": {"text": "string"},
                                "output_schema": {"error": "string"},
                                "read_only": True,
                                "side_effect_free": True,
                                "requires_confirmation": False,
                                "risk_level": "low",
                                "tags": ["fake", "failure"],
                            },
                            {
                                "name": "writer",
                                "description": "A fake write-capable tool that must not be registered.",
                                "input_schema": {"text": "string"},
                                "read_only": False,
                                "side_effect_free": False,
                                "requires_confirmation": True,
                                "risk_level": "high",
                            },
                        ]
                    },
                }
            )
            return
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            name = params.get("name")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name == "echo":
                text = str(arguments.get("text") or arguments.get("query") or "")
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
                                        "output": {"echo": text},
                                        "output_summary": f"fake remote echo returned {len(text)} chars.",
                                        "metadata": {"remote_fixture": "echo"},
                                    },
                                }
                            ],
                            "isError": False,
                        },
                    }
                )
                return
            if name == "unstable":
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
                                        "error_message": "fake remote MCP failure",
                                        "metadata": {"error_type": "fake_remote_failure"},
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
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        )


class ScriptedLLMClient(LLMClient):
    def __init__(self, decisions: list[dict[str, Any]]):
        self.decisions = list(decisions)

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {"provider": "scripted", "model": "mcp-client-smoke", "available": True}

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        decision = self.decisions.pop(0) if self.decisions else {
            "thought": "No scripted actions remain.",
            "action": "finish",
            "args": {"summary": "Scripted run finished."},
            "finish_reason": "script_complete",
        }
        return LLMResponse(
            success=True,
            content=json.dumps(decision),
            provider="scripted",
            model="mcp-client-smoke",
        )


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def trace_metadata(trace) -> dict[str, Any]:
    try:
        output = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        return {}
    return output.get("metadata", {}) if isinstance(output, dict) else {}


def start_fake_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/mcp"


def main() -> None:
    init_db()
    register_default_tools()
    server, url = start_fake_server()
    try:
        registered = register_remote_mcp_server(MCPRemoteServer("fake", url, 2))
        names = {spec.name for spec in registered}
        all_names = {spec.name for spec in list_tools()}
        assert_true({"fake.echo", "fake.unstable"} <= names, "fake remote tools were not discovered")
        assert_true("fake.writer" not in all_names, "write-capable remote tool was registered")

        echo_spec = get_tool("fake.echo")
        assert_true(echo_spec is not None and "mcp_remote" in echo_spec.tags, "remote spec metadata missing")
        direct = execute_tool("fake.echo", {"text": "hello remote"})
        assert_true(direct.success, "direct remote tool call failed")
        assert_true(direct.metadata.get("tool_source") == "mcp_remote", "direct remote source missing")

        with SessionLocal() as db:
            planned = store.create_agent_run(
                db,
                "Use remote MCP tool to echo evidence and generate a report.",
                "summary",
                "mock",
                ["fake.echo", "report_writer"],
            )
            planned_plan = plan_task(
                planned.task,
                ["fake.echo", "report_writer"],
                "mock",
                planner_mode="deterministic",
            )
            store.update_agent_run_plan(db, planned.run_id, planned_plan)
            planned_summary = run_plan(db, planned.run_id)
            planned_traces = store.list_tool_traces(db, planned.run_id)
            assert_true(planned_summary["status"] == "completed", "planned remote run failed")
            assert_true(any(trace.tool_name == "fake.echo" for trace in planned_traces), "planned remote trace missing")
            assert_true(
                any(trace_metadata(trace).get("tool_source") == "mcp_remote" for trace in planned_traces),
                "planned remote trace source missing",
            )

            failure = store.create_agent_run(
                db,
                "Use remote MCP tool that fails and generate a report.",
                "summary",
                "mock",
                ["fake.unstable", "report_writer"],
            )
            failure_plan = plan_task(
                failure.task,
                ["fake.unstable", "report_writer"],
                "mock",
                planner_mode="deterministic",
            )
            store.update_agent_run_plan(db, failure.run_id, failure_plan)
            failure_summary = run_plan(db, failure.run_id)
            failure_traces = store.list_tool_traces(db, failure.run_id)
            assert_true(failure_summary["status"] == "completed", "remote failure caused planned run failure")
            assert_true(any(trace.status == "failed" for trace in failure_traces), "remote failure trace missing")

            react = store.create_agent_run(
                db,
                "Use remote MCP evidence, then finish with a transparent limitation if it fails.",
                "summary",
                "mock",
                ["fake.unstable", "rag_search", "report_writer"],
            )
            react_plan = plan_task(
                react.task,
                ["fake.unstable", "rag_search", "report_writer"],
                "mock",
                planner_mode="deterministic",
                execution_mode_override="react",
            )
            store.update_agent_run_plan(db, react.run_id, react_plan)
            react_summary = run_react_task(
                db,
                react.run_id,
                Settings(execution_mode="react", react_max_steps=4, react_same_tool_max_calls=2),
                ScriptedLLMClient(
                    [
                        {
                            "thought": "Try the allowed remote MCP tool.",
                            "action": "fake.unstable",
                            "args": {"text": "please fail"},
                            "finish_reason": None,
                        },
                        {
                            "thought": "The remote tool failed, so finish with a limitation.",
                            "action": "finish",
                            "args": {"summary": "Remote MCP tool failed; completed with limitation."},
                            "finish_reason": "completed_with_limitation",
                        },
                    ]
                ),
            )
            react_run = store.get_agent_run(db, react.run_id)
            react_state = json.loads(react_run.plan_json)["react_state"]
            react_traces = store.list_tool_traces(db, react.run_id)
            assert_true(react_summary["status"] == "completed", "remote failure caused ReAct run failure")
            assert_true(react_state["completed_with_limitation"] is True, "ReAct limitation finish missing")
            assert_true(
                any(
                    trace.tool_name == "fake.unstable"
                    and trace.status == "failed"
                    and trace_metadata(trace).get("tool_source") == "mcp_remote"
                    for trace in react_traces
                ),
                "ReAct remote failure observation trace missing",
            )
    finally:
        server.shutdown()
        server.server_close()

    print(
        json.dumps(
            {
                "mcp_client": "ok",
                "fake_remote_discovery": "ok",
                "remote_tool_call": "ok",
                "remote_tool_failure_visible": "ok",
                "react_remote_failure_limitation": "ok",
                "write_remote_hidden": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
