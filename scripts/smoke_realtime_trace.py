"""Smoke checks for Day40 realtime trace streaming."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.agent.dispatcher import run_task_by_mode
from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.config import settings
from app.database import SessionLocal, init_db
from app.main import app
from app.mcp.client import MCPRemoteServer, register_remote_mcp_server
from app.rag.build_index import build_local_index
from app.tools.defaults import register_default_tools
from app.trace import store
from scripts.init_demo_db import init_demo_db
from scripts.smoke_mcp_client import start_fake_server


TASK = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def prepare_runtime() -> None:
    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        data_lines = [
            line.removeprefix("data: ").strip()
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if not data_lines:
            continue
        events.append(json.loads("\n".join(data_lines)))
    return events


def stream_events(client: TestClient, run_id: str) -> tuple[str, list[dict[str, Any]]]:
    response = client.get(
        f"/api/tasks/{run_id}/events",
        params={
            "poll_interval_seconds": 0.1,
            "heartbeat_seconds": 30,
            "max_duration_seconds": 5,
        },
    )
    assert_true(response.status_code == 200, f"SSE request failed: {response.status_code}")
    content_type = response.headers.get("content-type", "")
    assert_true(
        content_type.startswith("text/event-stream"),
        f"unexpected content-type: {content_type}",
    )
    return response.text, parse_sse(response.text)


def make_parallel_plan() -> dict[str, Any]:
    return {
        "version": "day40-smoke",
        "task": TASK,
        "source_mode": "mock",
        "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
        "planner_source": "smoke",
        "execution_mode": "planned",
        "steps": [
            {
                "step_no": 1,
                "goal": "Read local evidence.",
                "tool_name": "file_reader",
                "arguments": {"path": "demo_research_note.md", "max_chars": 1000},
                "expected_output": "Local text content.",
                "completion_criteria": "File read succeeds.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 2,
                "goal": "Query local demo database.",
                "tool_name": "sql_query",
                "arguments": {"query": "SELECT title FROM documents", "limit": 3},
                "expected_output": "Read-only rows.",
                "completion_criteria": "SQL returns rows.",
                "risk_level": "medium",
                "requires_confirmation": False,
            },
            {
                "step_no": 3,
                "goal": "Retrieve local RAG evidence.",
                "tool_name": "rag_search",
                "arguments": {"query": "trace persistence tool registry", "top_k": 2},
                "expected_output": "RAG hits.",
                "completion_criteria": "RAG returns hits.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 4,
                "goal": "Generate final report.",
                "tool_name": "report_writer",
                "arguments": {},
                "expected_output": "Markdown report.",
                "completion_criteria": "Report is saved.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
        ],
        "notes": ["Day40 realtime smoke plan."],
    }


def create_run(db, plan: dict[str, Any]):
    run = store.create_agent_run(
        db,
        task=plan["task"],
        report_type="summary",
        source_mode="mock",
        allowed_tools=plan["allowed_tools"],
    )
    store.update_agent_run_plan(db, run.run_id, plan)
    return run


def test_completed_stream(client: TestClient, db) -> str:
    settings.parallel_execution_enabled = False
    run = create_run(db, make_parallel_plan())
    summary = run_task_by_mode(db, run.run_id, settings)
    assert_true(summary["status"] == "completed", "completed stream fixture did not complete")
    _raw, events = stream_events(client, run.run_id)
    event_types = [event["event_type"] for event in events]
    assert_true(event_types[0] == "run_status", "first event should be run_status")
    assert_true("trace_finished" in event_types, "completed stream missing trace events")
    assert_true("report_ready" in event_types, "completed stream missing report_ready")
    assert_true(event_types[-1] == "done", "completed stream did not finish with done")
    for event in events:
        for key in (
            "run_id",
            "event_type",
            "step_no",
            "tool_name",
            "status",
            "output_summary",
            "error_message",
            "latency_ms",
            "metadata",
            "created_at",
            "finished_at",
        ):
            assert_true(key in event, f"SSE event missing key: {key}")
    return run.run_id


def test_hitl_waiting_stream(client: TestClient, db) -> str:
    plan = make_parallel_plan()
    plan["steps"][-1]["requires_confirmation"] = True
    plan["steps"][-1]["risk_level"] = "high"
    run = create_run(db, plan)
    summary = run_plan(db, run.run_id)
    assert_true(summary["status"] == "waiting_human", "HITL fixture did not wait")
    _raw, events = stream_events(client, run.run_id)
    assert_true(any(event["event_type"] == "waiting_human" for event in events), "waiting_human event missing")
    assert_true(events[-1]["event_type"] == "done", "waiting_human stream did not close with done")
    assert_true(events[-1]["status"] == "waiting_human", "done event did not preserve waiting_human status")
    return run.run_id


def test_parallel_metadata_stream(client: TestClient, db) -> str:
    settings.parallel_execution_enabled = True
    run = create_run(db, make_parallel_plan())
    summary = run_task_by_mode(db, run.run_id, settings)
    assert_true(summary["status"] == "completed", "parallel stream fixture did not complete")
    _raw, events = stream_events(client, run.run_id)
    parallel_events = [
        event for event in events
        if isinstance(event.get("metadata"), dict) and event["metadata"].get("parallel") is True
    ]
    assert_true(len(parallel_events) >= 2, "parallel metadata was not visible in SSE")
    assert_true(
        all(event["metadata"].get("execution_mode") == "planned_parallel" for event in parallel_events),
        "parallel execution_mode metadata missing",
    )
    return run.run_id


def test_remote_failure_stream(client: TestClient, db) -> str:
    server, url = start_fake_server()
    try:
        register_remote_mcp_server(MCPRemoteServer("fake_rt", url, 2))
        run = store.create_agent_run(
            db,
            "Use remote MCP tool that fails and generate a report.",
            "summary",
            "mock",
            ["fake_rt.unstable", "report_writer"],
        )
        plan = plan_task(
            run.task,
            ["fake_rt.unstable", "report_writer"],
            "mock",
            planner_mode="deterministic",
        )
        store.update_agent_run_plan(db, run.run_id, plan)
        summary = run_plan(db, run.run_id)
        assert_true(summary["status"] == "completed", "remote failure caused run failure")
        _raw, events = stream_events(client, run.run_id)
        failed = [
            event for event in events
            if event.get("tool_name") == "fake_rt.unstable" and event.get("status") == "failed"
        ]
        assert_true(failed, "remote failed trace missing from SSE")
        assert_true(
            failed[0]["metadata"].get("tool_source") == "mcp_remote",
            "remote tool_source metadata missing from SSE",
        )
        return run.run_id
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    original = {
        "parallel_execution_enabled": settings.parallel_execution_enabled,
        "parallel_max_workers": settings.parallel_max_workers,
        "parallel_timeout_seconds": settings.parallel_timeout_seconds,
        "llm_planner_enabled": settings.llm_planner_enabled,
        "llm_planner_mode": settings.llm_planner_mode,
    }
    try:
        prepare_runtime()
        settings.llm_planner_enabled = False
        settings.llm_planner_mode = "deterministic"
        settings.parallel_max_workers = 3
        settings.parallel_timeout_seconds = 60
        with TestClient(app) as client:
            with SessionLocal() as db:
                test_completed_stream(client, db)
                test_hitl_waiting_stream(client, db)
                test_parallel_metadata_stream(client, db)
                test_remote_failure_stream(client, db)
        print(
            json.dumps(
                {
                    "realtime_trace": "ok",
                    "sse_format": "ok",
                    "completed_stream": "ok",
                    "hitl_waiting": "ok",
                    "parallel_metadata": "ok",
                    "remote_mcp_failure_visible": "ok",
                },
                indent=2,
            )
        )
    finally:
        settings.parallel_execution_enabled = original["parallel_execution_enabled"]
        settings.parallel_max_workers = original["parallel_max_workers"]
        settings.parallel_timeout_seconds = original["parallel_timeout_seconds"]
        settings.llm_planner_enabled = original["llm_planner_enabled"]
        settings.llm_planner_mode = original["llm_planner_mode"]


if __name__ == "__main__":
    main()
