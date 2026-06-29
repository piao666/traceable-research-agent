"""Offline external-client demo for the read-only MCP endpoint.

The script uses FastAPI TestClient so the demo behaves like an external MCP
client without requiring a long-running uvicorn process.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep this demo offline and stable even when the developer machine has real
# API keys configured in the environment.
os.environ["LLM_PROVIDER"] = "deterministic"
os.environ["LLM_PLANNER_ENABLED"] = "false"
os.environ["GITHUB_TOOL_DEFAULT_MODE"] = "mock"
os.environ["TAVILY_FALLBACK_TO_MOCK"] = "true"
os.environ["MCP_REMOTE_REGISTRY_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app
from app.tools.defaults import register_default_tools


EXPECTED_TOOLS = {
    "file_reader",
    "rag_search",
    "sql_query_readonly",
    "github_search",
    "tavily_search",
    "trace_reader",
    "report_reader",
}
BLOCKED_TOOLS = {
    "report_writer",
    "file_writer",
    "sql_execute",
    "db_write",
    "github_issue_create",
    "github_pr_comment",
    "github_repository_mutation",
    "github_push",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def json_rpc(
    client: TestClient,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )
    assert_true(response.status_code == 200, f"MCP {method} HTTP status was {response.status_code}")
    payload = response.json()
    assert_true(isinstance(payload, dict), f"MCP {method} did not return a JSON object")
    return payload


def json_rpc_content(payload: dict[str, Any]) -> dict[str, Any]:
    assert_true(payload.get("error") is None, f"Unexpected MCP error: {payload.get('error')}")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    assert_true(bool(content), "MCP tools/call result did not include content")
    first = content[0]
    assert_true(isinstance(first, dict), "MCP content item is not an object")
    data = first.get("json")
    assert_true(isinstance(data, dict), "MCP content item did not include a JSON payload")
    return data


def create_demo_run(client: TestClient) -> str:
    create_response = client.post(
        "/api/tasks",
        json={
            "task": "Read local traceable research docs and generate a short audit report.",
            "report_type": "markdown",
            "source_mode": "mock",
            "allowed_tools": ["file_reader", "report_writer"],
            "execution_mode_override": "planned",
        },
    )
    assert_true(create_response.status_code == 200, f"Task create failed: {create_response.text}")
    run_id = create_response.json()["run_id"]

    run_response = client.post(f"/api/tasks/{run_id}/run")
    assert_true(run_response.status_code == 200, f"Task run failed: {run_response.text}")
    run_payload = run_response.json()
    assert_true(run_payload["status"] == "completed", f"Demo run did not complete: {run_payload}")
    return run_id


def main() -> None:
    init_db()
    register_default_tools()

    with TestClient(app) as client:
        run_id = create_demo_run(client)

        initialize = json_rpc(client, 1, "initialize")
        assert_true(initialize.get("error") is None, f"Initialize failed: {initialize}")
        initialize_result = initialize.get("result") if isinstance(initialize.get("result"), dict) else {}
        capabilities = initialize_result.get("capabilities") if isinstance(initialize_result.get("capabilities"), dict) else {}
        assert_true(bool(capabilities.get("tools")), "Initialize result did not advertise tools capability")

        tools_list = json_rpc(client, 2, "tools/list")
        assert_true(tools_list.get("error") is None, f"tools/list failed: {tools_list}")
        list_result = tools_list.get("result") if isinstance(tools_list.get("result"), dict) else {}
        tools = list_result.get("tools") if isinstance(list_result.get("tools"), list) else []
        discovered_names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        missing_tools = sorted(EXPECTED_TOOLS - discovered_names)
        exposed_blocked_tools = sorted(BLOCKED_TOOLS & discovered_names)
        assert_true(not missing_tools, f"MCP tools/list missing expected tools: {missing_tools}")
        assert_true(not exposed_blocked_tools, f"MCP exposed blocked tools: {exposed_blocked_tools}")

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            assert_true(tool.get("read_only") is True, f"{tool.get('name')} is not read-only")
            assert_true(tool.get("side_effect_free") is True, f"{tool.get('name')} is not side-effect-free")
            assert_true(tool.get("requires_confirmation") is False, f"{tool.get('name')} requires confirmation")

        file_call = json_rpc(
            client,
            3,
            "tools/call",
            {
                "name": "file_reader",
                "arguments": {"path": "demo_research_note.md", "max_chars": 500},
                "_trace": {"run_id": run_id, "step_no": 99},
            },
        )
        file_payload = json_rpc_content(file_call)
        assert_true(file_payload["success"] is True, f"file_reader MCP call failed: {file_payload}")
        assert_true(
            file_payload["metadata"]["mcp_tool_name"] == "file_reader",
            "file_reader MCP metadata missing mcp_tool_name",
        )

        trace_call = json_rpc(
            client,
            4,
            "tools/call",
            {"name": "trace_reader", "arguments": {"run_id": run_id, "limit": 50}},
        )
        trace_payload = json_rpc_content(trace_call)
        assert_true(trace_payload["success"] is True, f"trace_reader failed: {trace_payload}")
        trace_output = trace_payload.get("output") if isinstance(trace_payload.get("output"), dict) else {}
        traces = trace_output.get("traces") if isinstance(trace_output.get("traces"), list) else []
        trace_count = int(trace_output.get("trace_count") or 0)
        mcp_trace_written = any(
            isinstance(trace, dict)
            and trace.get("tool_name") == "file_reader"
            and trace.get("step_no") == 99
            for trace in traces
        )
        assert_true(mcp_trace_written, "MCP tools/call did not write the expected trace row")

        report_call = json_rpc(
            client,
            5,
            "tools/call",
            {"name": "report_reader", "arguments": {"run_id": run_id}},
        )
        report_payload = json_rpc_content(report_call)
        assert_true(report_payload["success"] is True, f"report_reader failed: {report_payload}")
        report_output = report_payload.get("output") if isinstance(report_payload.get("output"), dict) else {}
        assert_true(report_output.get("exists") is True, "report_reader did not find the generated report")

        blocked_call = client.post(
            "/mcp/tools/call",
            json={"name": "report_writer", "arguments": {}},
        )
        assert_true(blocked_call.status_code in {403, 404}, "report_writer should not be callable via MCP")

        summary = {
            "mcp_external_client_demo": "ok",
            "run_id": run_id,
            "json_rpc": {
                "initialize": "ok",
                "tools_list": "ok",
                "tools_call": "ok",
            },
            "discovered_tools": sorted(discovered_names),
            "boundary_checks": {
                "expected_readonly_tools_visible": True,
                "blocked_tools_hidden": True,
                "report_writer_call_blocked": True,
            },
            "calls": {
                "file_reader": file_payload.get("output_summary"),
                "trace_reader": trace_payload.get("output_summary"),
                "report_reader": report_payload.get("output_summary"),
            },
            "trace_count": trace_count,
            "mcp_trace_written": mcp_trace_written,
            "report_exists": bool(report_output.get("exists")),
            "overall_status": "passed",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
