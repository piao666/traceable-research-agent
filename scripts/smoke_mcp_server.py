"""Offline smoke checks for the Day38 MCP server foundation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app.tools.defaults import register_default_tools
from app.trace import store


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _create_trace_run() -> str:
    with SessionLocal() as db:
        run = store.create_agent_run(
            db,
            task="MCP smoke trace run",
            report_type="summary",
            source_mode="mock",
            allowed_tools=["file_reader"],
        )
        store.update_agent_run_plan(
            db,
            run.run_id,
            {
                "version": "day38-smoke",
                "task": run.task,
                "source_mode": "mock",
                "allowed_tools": ["file_reader"],
                "execution_mode": "planned",
                "steps": [],
                "notes": [],
            },
        )
        return run.run_id


def main() -> None:
    init_db()
    register_default_tools()
    run_id = _create_trace_run()

    client = TestClient(app)
    health = client.get("/mcp/health")
    assert_true(health.status_code == 200, "MCP health failed")
    assert_true(health.json()["read_only"] is True, "MCP health is not read-only")
    assert_true("channel_summary" in health.json(), "MCP health missing channel summary")

    tools_response = client.get("/mcp/tools")
    assert_true(tools_response.status_code == 200, "MCP tools/list failed")
    tools = tools_response.json()["tools"]
    names = {tool["name"] for tool in tools}
    expected = {
        "file_reader",
        "rag_search",
        "sql_query_readonly",
        "github_search",
        "tavily_search",
        "trace_reader",
        "report_reader",
    }
    assert_true(expected.issubset(names), f"MCP tools missing: {expected - names}")
    assert_true("report_writer" not in names, "write/barrier tool was exposed")
    for tool in tools:
        assert_true(tool["read_only"] is True, f"{tool['name']} is not read-only")
        assert_true(tool["side_effect_free"] is True, f"{tool['name']} is not side-effect-free")
        assert_true("risk_level" in tool, f"{tool['name']} missing risk metadata")
        assert_true(tool.get("channel") == "readonly", f"{tool['name']} missing readonly channel")
        assert_true(isinstance(tool.get("policy"), dict), f"{tool['name']} missing policy metadata")

    initialize = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert_true(initialize.status_code == 200, "JSON-RPC initialize failed")
    assert_true(
        initialize.json()["result"]["capabilities"]["tools"],
        "JSON-RPC initialize missing tools capability",
    )

    call = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "file_reader",
                "arguments": {"path": "demo_research_note.md", "max_chars": 200},
                "_trace": {"run_id": run_id, "step_no": 1},
            },
        },
    )
    assert_true(call.status_code == 200, "JSON-RPC tools/call failed")
    call_payload = call.json()
    assert_true(call_payload.get("error") is None, f"Unexpected MCP error: {call_payload}")
    content = call_payload["result"]["content"][0]["json"]
    assert_true(content["success"] is True, "MCP file_reader call did not succeed")
    assert_true(content["metadata"]["mcp_tool_name"] == "file_reader", "MCP metadata missing tool name")

    trace_read = client.post(
        "/mcp/tools/call",
        json={"name": "trace_reader", "arguments": {"run_id": run_id}},
    )
    assert_true(trace_read.status_code == 200, "trace_reader call failed")
    trace_payload = trace_read.json()
    assert_true(trace_payload["success"] is True, "trace_reader did not succeed")
    assert_true(trace_payload["output"]["trace_count"] >= 1, "MCP call did not write trace")
    assert_true(
        any(trace["tool_name"] == "file_reader" for trace in trace_payload["output"]["traces"]),
        "file_reader trace not found",
    )

    report_read = client.post(
        "/mcp/tools/call",
        json={"name": "report_reader", "arguments": {"run_id": run_id}},
    )
    assert_true(report_read.status_code == 200, "report_reader call failed")
    assert_true(report_read.json()["success"] is True, "report_reader did not return stable result")

    blocked = client.post(
        "/mcp/tools/call",
        json={"name": "report_writer", "arguments": {}},
    )
    assert_true(blocked.status_code in {403, 404}, "report_writer should not be exposed")

    print(
        json.dumps(
            {
                "mcp_server": "ok",
                "tool_discovery": "ok",
                "json_rpc": "ok",
                "tool_call_trace": "ok",
                "readers": "ok",
                "write_tools_hidden": "ok",
                "channel_policy_metadata": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
