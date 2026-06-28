"""Smoke checks for Day37 planned parallel tool execution."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.agent.dispatcher import run_task_by_mode
from app.config import settings
from app.database import SessionLocal, init_db
from app.main import app
from app.rag.build_index import build_local_index
from app.tools.defaults import register_default_tools
from app.trace import store
from scripts.init_demo_db import init_demo_db


TASK = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def prepare_runtime() -> None:
    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()


def make_plan(*, bad_file: bool = False, hitl: bool = False) -> dict[str, Any]:
    file_path = "missing_parallel_input.md" if bad_file else "demo_research_note.md"
    return {
        "version": "day37-smoke",
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
                "arguments": {"path": file_path, "max_chars": 1000},
                "expected_output": "Local text content.",
                "completion_criteria": "File read succeeds or records a visible failure.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 2,
                "goal": "Query local demo database.",
                "tool_name": "sql_query",
                "arguments": {"query": "SELECT title FROM documents", "limit": 3},
                "expected_output": "Read-only rows.",
                "completion_criteria": "SQL returns rows or a structured empty result.",
                "risk_level": "medium",
                "requires_confirmation": False,
            },
            {
                "step_no": 3,
                "goal": "Retrieve local RAG evidence.",
                "tool_name": "rag_search",
                "arguments": {"query": "trace persistence tool registry", "top_k": 2},
                "expected_output": "RAG hits.",
                "completion_criteria": "RAG returns hits or a stable empty result.",
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
                "risk_level": "high" if hitl else "low",
                "requires_confirmation": hitl,
            },
        ],
        "notes": ["Day37 smoke plan."],
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


def trace_metadata(trace) -> dict[str, Any]:
    try:
        payload = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        return {}
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def parallel_traces(db, run_id: str):
    traces = store.list_tool_traces(db, run_id)
    return [trace for trace in traces if trace_metadata(trace).get("parallel") is True]


def assert_parallel_group(db, run_id: str) -> None:
    traces = parallel_traces(db, run_id)
    assert_true(len(traces) >= 2, "expected at least two parallel traces")
    group_counts = Counter(trace_metadata(trace).get("parallel_group_id") for trace in traces)
    assert_true(any(count >= 2 for count in group_counts.values()), "parallel traces did not share a group")
    for trace in traces:
        metadata = trace_metadata(trace)
        for key in (
            "parallel_group_id",
            "parallel_worker_id",
            "parallel_group_size",
            "execution_mode",
            "started_at",
            "finished_at",
            "latency_ms",
        ):
            assert_true(key in metadata, f"missing parallel metadata key: {key}")
        assert_true(metadata["execution_mode"] == "planned_parallel", "wrong execution_mode metadata")


def test_default_serial_guard(db) -> None:
    settings.parallel_execution_enabled = False
    run = create_run(db, make_plan())
    summary = run_task_by_mode(db, run.run_id, settings)
    assert_true(summary["status"] == "completed", "serial guard run did not complete")
    traces = store.list_tool_traces(db, run.run_id)
    assert_true(traces, "serial guard wrote no traces")
    assert_true(not parallel_traces(db, run.run_id), "default serial run wrote parallel metadata")


def test_parallel_basic(db) -> str:
    settings.parallel_execution_enabled = True
    settings.parallel_max_workers = 3
    settings.parallel_timeout_seconds = 60
    run = create_run(db, make_plan())
    summary = run_task_by_mode(db, run.run_id, settings)
    final_run = store.get_agent_run(db, run.run_id)
    assert_true(summary["status"] == "completed", "parallel run did not complete")
    assert_parallel_group(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    assert_true("report_writer" not in {trace.tool_name for trace in traces}, "report_writer wrote a tool trace")
    assert_true(bool(final_run.report_path and (ROOT / final_run.report_path).exists()), "parallel report missing")
    return run.run_id


def test_failure_visible(db) -> None:
    settings.parallel_execution_enabled = True
    run = create_run(db, make_plan(bad_file=True))
    summary = run_task_by_mode(db, run.run_id, settings)
    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    file_trace = next(trace for trace in traces if trace.tool_name == "file_reader")
    assert_true(summary["status"] == "completed", "failed tool changed run status")
    assert_true(file_trace.status == "failed", "failed tool was not visible in trace")
    assert_true(trace_metadata(file_trace).get("parallel") is True, "failed parallel trace missing metadata")
    assert_true(bool(final_run.report_path and (ROOT / final_run.report_path).exists()), "failure report missing")


def test_hitl_guard(db) -> None:
    settings.parallel_execution_enabled = True
    run = create_run(db, make_plan(hitl=True))
    summary = run_task_by_mode(db, run.run_id, settings)
    assert_true(summary["status"] == "waiting_human", "parallel executor bypassed HITL")
    assert_parallel_group(db, run.run_id)
    final_run = store.get_agent_run(db, run.run_id)
    assert_true(final_run.current_step == 3, "HITL progress did not stop before report_writer")


def test_async_dispatch() -> str:
    settings.parallel_execution_enabled = True
    settings.llm_planner_enabled = False
    settings.llm_planner_mode = "deterministic"
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            json={
                "task": TASK,
                "report_type": "summary",
                "source_mode": "mock",
                "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
                "execution_mode_override": "planned",
            },
        )
        assert_true(response.status_code == 200, "async dispatch task creation failed")
        run_id = response.json()["run_id"]
        run_response = client.post(f"/api/tasks/{run_id}/run_async", json={})
        assert_true(run_response.status_code == 200, "run_async request failed")
        final_status = None
        for _ in range(20):
            status_response = client.get(f"/api/tasks/{run_id}")
            assert_true(status_response.status_code == 200, "async status failed")
            final_status = status_response.json()["status"]
            if final_status == "completed":
                break
            time.sleep(0.25)
        assert_true(final_status == "completed", f"async run did not complete: {final_status}")
        trace_response = client.get(f"/api/tasks/{run_id}/trace")
        assert_true(trace_response.status_code == 200, "async trace failed")
        traces = trace_response.json()
        parallel = [
            item for item in traces if isinstance(item.get("metadata"), dict) and item["metadata"].get("parallel") is True
        ]
        assert_true(len(parallel) >= 2, "async dispatch did not use parallel executor")
    return run_id


def main() -> None:
    original = {
        "parallel_execution_enabled": settings.parallel_execution_enabled,
        "parallel_max_workers": settings.parallel_max_workers,
        "parallel_group_strategy": settings.parallel_group_strategy,
        "parallel_timeout_seconds": settings.parallel_timeout_seconds,
        "llm_planner_enabled": settings.llm_planner_enabled,
        "llm_planner_mode": settings.llm_planner_mode,
    }
    try:
        prepare_runtime()
        with SessionLocal() as db:
            test_default_serial_guard(db)
            test_parallel_basic(db)
            test_failure_visible(db)
            test_hitl_guard(db)
        test_async_dispatch()
        print(
            json.dumps(
                {
                    "parallel_execution": "ok",
                    "default_serial_guard": "ok",
                    "parallel_group": "ok",
                    "trace_metadata": "ok",
                    "report_writer_guard": "ok",
                    "failure_visible": "ok",
                    "hitl_guard": "ok",
                    "async_dispatch": "ok",
                },
                indent=2,
            )
        )
    finally:
        settings.parallel_execution_enabled = original["parallel_execution_enabled"]
        settings.parallel_max_workers = original["parallel_max_workers"]
        settings.parallel_group_strategy = original["parallel_group_strategy"]
        settings.parallel_timeout_seconds = original["parallel_timeout_seconds"]
        settings.llm_planner_enabled = original["llm_planner_enabled"]
        settings.llm_planner_mode = original["llm_planner_mode"]


if __name__ == "__main__":
    main()
