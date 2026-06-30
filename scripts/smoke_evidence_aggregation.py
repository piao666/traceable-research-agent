"""Smoke checks for Day41 research evidence aggregation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.agent.react_executor import run_react_task
from app.config import Settings, settings
from app.database import SessionLocal, init_db
from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.main import app
from app.mcp.client import MCPRemoteServer, register_remote_mcp_server
from app.rag.build_index import build_local_index
from app.tools.base import ToolResult
from app.tools.defaults import register_default_tools
from app.trace import store
from app.trace.logger import record_tool_result
from scripts.init_demo_db import init_demo_db
from scripts.smoke_mcp_client import start_fake_server


TASK = "Aggregate file, SQL, RAG, GitHub, Tavily, and remote MCP evidence into an auditable report"


class ScriptedLLMClient(LLMClient):
    def __init__(self, decisions: list[dict[str, Any]]):
        self.decisions = list(decisions)

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {"provider": "scripted", "model": "evidence-smoke", "available": True}

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        decision = self.decisions.pop(0) if self.decisions else {
            "thought": "Finish after scripted evidence checks.",
            "action": "finish",
            "args": {"summary": "Evidence aggregation smoke finished."},
            "finish_reason": "completed",
        }
        return LLMResponse(
            success=True,
            content=json.dumps(decision),
            provider="scripted",
            model="evidence-smoke",
        )


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def prepare_runtime() -> None:
    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()


def multi_source_plan() -> dict[str, Any]:
    return {
        "version": "day41-smoke",
        "task": TASK,
        "source_mode": "mock",
        "allowed_tools": [
            "file_reader",
            "sql_query",
            "rag_search",
            "mcp_github_search",
            "tavily_search",
            "report_writer",
        ],
        "planner_source": "smoke",
        "execution_mode": "planned",
        "steps": [
            {
                "step_no": 1,
                "goal": "Read local file evidence.",
                "tool_name": "file_reader",
                "arguments": {"path": "demo_research_note.md", "max_chars": 1200},
                "expected_output": "Local text evidence.",
                "completion_criteria": "File content is available.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 2,
                "goal": "Collect read-only database evidence.",
                "tool_name": "sql_query",
                "arguments": {"query": "SELECT title FROM documents", "limit": 3},
                "expected_output": "SQLite rows.",
                "completion_criteria": "Read-only query returns rows.",
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
                "goal": "Collect GitHub mock evidence.",
                "tool_name": "mcp_github_search",
                "arguments": {"query": "traceable research agent", "mode": "mock", "limit": 2},
                "expected_output": "GitHub mock results.",
                "completion_criteria": "Mock GitHub evidence is marked as mock.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 5,
                "goal": "Collect Tavily mock evidence.",
                "tool_name": "tavily_search",
                "arguments": {"query": "traceable research agent", "mode": "mock", "max_results": 2},
                "expected_output": "Tavily mock results.",
                "completion_criteria": "Mock Tavily evidence is marked as mock.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 6,
                "goal": "Generate report.",
                "tool_name": "report_writer",
                "arguments": {},
                "expected_output": "Markdown report.",
                "completion_criteria": "Report includes evidence aggregation.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
        ],
        "notes": ["Day41 evidence aggregation smoke plan."],
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


def get_evidence(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{run_id}/evidence")
    assert_true(response.status_code == 200, f"evidence endpoint failed: {response.status_code}")
    return response.json()


def test_multi_source_bundle(client: TestClient, db) -> str:
    run = create_run(db, multi_source_plan())
    summary = run_plan(db, run.run_id)
    assert_true(summary["status"] == "completed", "multi-source run failed")
    evidence = get_evidence(client, run.run_id)
    source_types = {group["source_type"] for group in evidence["source_groups"]}
    assert_true({"file", "sql", "rag", "mock"} <= source_types, f"missing source groups: {source_types}")
    assert_true(evidence["total_evidence_items"] >= 5, "not enough evidence items extracted")
    assert_true(evidence["claims"], "claim-evidence map missing")
    assert_true(any(item["is_mock"] for item in evidence["evidence_items"]), "mock evidence was not marked")
    assert_true(any("mock 证据" in warning for warning in evidence["warnings"]), "mock warning missing")

    report = client.get(f"/api/reports/{run.run_id}")
    assert_true(report.status_code == 200, "report endpoint failed")
    markdown = report.json()["markdown"]
    assert_true("## 6. 证据聚合" in markdown, "report missing evidence aggregation section")
    assert_true("结论-证据映射" in markdown, "report missing claim map")
    assert_true("Trace 汇总" not in markdown, "report should not include Trace summary section")
    assert_true("运行限制与说明" not in markdown, "report should not include runtime limitations section")
    return run.run_id


def test_fallback_trace_bundle(client: TestClient, db) -> str:
    plan = {
        "version": "day41-fallback-smoke",
        "task": "Represent fallback GitHub evidence without claiming it is fresh external truth.",
        "source_mode": "mock",
        "allowed_tools": ["mcp_github_search"],
        "planner_source": "smoke",
        "execution_mode": "planned",
        "steps": [
            {
                "step_no": 1,
                "goal": "Collect fallback GitHub evidence.",
                "tool_name": "mcp_github_search",
                "arguments": {"query": "traceable research agent"},
                "expected_output": "Fallback results.",
                "completion_criteria": "Fallback is visibly marked.",
                "risk_level": "low",
                "requires_confirmation": False,
            }
        ],
        "notes": [],
    }
    run = create_run(db, plan)
    record_tool_result(
        db,
        run.run_id,
        1,
        "mcp_github_search",
        {"query": "traceable research agent"},
        ToolResult(
            success=True,
            output={
                "query": "traceable research agent",
                "results": [
                    {
                        "full_name": "fallback/example",
                        "url": "https://github.com/fallback/example",
                        "description": "Fallback repository fixture.",
                        "stars": 42,
                    }
                ],
            },
            output_summary="mcp_github_search returned fallback mock results.",
            metadata={
                "tool_source": "local",
                "data_source": "fallback",
                "fallback_used": True,
                "fallback_reason": "network_error",
            },
        ),
        latency_ms=0,
    )
    evidence = get_evidence(client, run.run_id)
    assert_true(any(item["is_fallback"] for item in evidence["evidence_items"]), "fallback evidence not marked")
    assert_true(any("fallback 证据" in warning for warning in evidence["warnings"]), "fallback warning missing")
    return run.run_id


def test_remote_failure_bundle(client: TestClient, db) -> str:
    server, url = start_fake_server()
    try:
        register_remote_mcp_server(MCPRemoteServer("fake_ea", url, 2))
        run = store.create_agent_run(
            db,
            "Use remote MCP tool that fails and keep the failure auditable.",
            "summary",
            "mock",
            ["fake_ea.unstable", "report_writer"],
        )
        plan = plan_task(
            run.task,
            ["fake_ea.unstable", "report_writer"],
            "mock",
            planner_mode="deterministic",
        )
        store.update_agent_run_plan(db, run.run_id, plan)
        summary = run_plan(db, run.run_id)
        assert_true(summary["status"] == "completed", "remote failure caused API-level run failure")
        evidence = get_evidence(client, run.run_id)
        failed = [
            item for item in evidence["evidence_items"]
            if item["tool_name"] == "fake_ea.unstable" and item["unsupported_reason"]
        ]
        assert_true(failed, "remote failure unsupported evidence missing")
        assert_true(failed[0]["metadata"].get("tool_source") == "mcp_remote", "remote metadata missing")
        assert_true(evidence["unsupported_claims"], "unsupported claim map missing")
        return run.run_id
    finally:
        server.shutdown()
        server.server_close()


def test_react_limitation_bundle(client: TestClient, db) -> str:
    server, url = start_fake_server()
    try:
        register_remote_mcp_server(MCPRemoteServer("fake_ea_react", url, 2))
        run = store.create_agent_run(
            db,
            "Use remote MCP evidence, then finish with a limitation if it fails.",
            "summary",
            "mock",
            ["fake_ea_react.unstable", "report_writer"],
        )
        plan = plan_task(
            run.task,
            ["fake_ea_react.unstable", "report_writer"],
            "mock",
            planner_mode="deterministic",
            execution_mode_override="react",
        )
        store.update_agent_run_plan(db, run.run_id, plan)
        summary = run_react_task(
            db,
            run.run_id,
            Settings(execution_mode="react", react_max_steps=4, react_same_tool_max_calls=2),
            ScriptedLLMClient(
                [
                    {
                        "thought": "Try the remote MCP tool.",
                        "action": "fake_ea_react.unstable",
                        "args": {"text": "please fail"},
                        "finish_reason": None,
                    },
                    {
                        "thought": "Finish with a transparent limitation.",
                        "action": "finish",
                        "args": {"summary": "Remote MCP failed, so the report is completed with limitation."},
                        "finish_reason": "completed_with_limitation",
                    },
                ]
            ),
        )
        assert_true(summary["status"] == "completed", "ReAct limitation run failed")
        evidence = get_evidence(client, run.run_id)
        assert_true(any(item["source_type"] == "llm_finish" for item in evidence["evidence_items"]), "finish evidence missing")
        assert_true(evidence["unsupported_claims"], "ReAct unsupported claims missing")
        report = client.get(f"/api/reports/{run.run_id}")
        assert_true(report.status_code == 200 and report.json()["exists"], "ReAct limitation report missing")
        assert_true("## 6. 证据聚合" in report.json()["markdown"], "ReAct report missing aggregation")
        return run.run_id
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    original = {
        "parallel_execution_enabled": settings.parallel_execution_enabled,
        "llm_planner_enabled": settings.llm_planner_enabled,
        "llm_planner_mode": settings.llm_planner_mode,
    }
    try:
        prepare_runtime()
        settings.parallel_execution_enabled = False
        settings.llm_planner_enabled = False
        settings.llm_planner_mode = "deterministic"
        with TestClient(app) as client:
            with SessionLocal() as db:
                test_multi_source_bundle(client, db)
                test_fallback_trace_bundle(client, db)
                test_remote_failure_bundle(client, db)
                test_react_limitation_bundle(client, db)
        print(
            json.dumps(
                {
                    "evidence_aggregation": "ok",
                    "evidence_api": "ok",
                    "multi_source_groups": "ok",
                    "mock_and_fallback_marked": "ok",
                    "remote_mcp_failure_auditable": "ok",
                    "react_limitation_report": "ok",
                },
                indent=2,
            )
        )
    finally:
        settings.parallel_execution_enabled = original["parallel_execution_enabled"]
        settings.llm_planner_enabled = original["llm_planner_enabled"]
        settings.llm_planner_mode = original["llm_planner_mode"]


if __name__ == "__main__":
    main()
