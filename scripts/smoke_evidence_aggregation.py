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
from app.agent.reporter import _repair_tool_only_sources
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
    assert_true("## 6. 证据聚合" not in markdown, "main report should hide evidence aggregation section")
    assert_true("## 6. 证据与工具观察结果" in markdown, "report missing tool observation section")
    assert_true("Trace 汇总" not in markdown, "report should not include Trace summary section")
    assert_true("运行限制与说明" not in markdown, "report should not include runtime limitations section")
    return run.run_id


def test_tool_source_repair() -> None:
    records = [
        {
            "success": True,
            "tool_name": "tavily_search",
            "output": {
                "results": [
                    {
                        "title": "LLM course",
                        "url": "https://example.com/llm-course",
                        "clean_content": "Structured course evidence.",
                    }
                ]
            },
        }
    ]
    repaired = _repair_tool_only_sources("结论。来源：[tavily_search]", records)
    assert_true("[tavily_search]" not in repaired, "tool-only source marker was not repaired")
    assert_true("https://example.com/llm-course" in repaired, "repaired source missing URL")

    remote_records = [
        {
            "success": True,
            "tool_name": "firecrawl.scrape",
            "metadata": {
                "tool_source": "mcp_remote",
                "remote_server": "firecrawl",
                "remote_tool_name": "scrape",
                "remote_registry_name": "firecrawl.scrape",
            },
            "output": {
                "title": "Firecrawl page",
                "url": "https://example.com/firecrawl-page",
                "markdown": "Readable page content.",
            },
        }
    ]
    repaired_remote = _repair_tool_only_sources("结论。来源：[firecrawl.scrape]", remote_records)
    assert_true("[firecrawl.scrape]" not in repaired_remote, "remote tool-only source marker was not repaired")
    assert_true("https://example.com/firecrawl-page" in repaired_remote, "remote repaired source missing URL")


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


def test_remote_source_pack_bundle(client: TestClient, db) -> str:
    plan = {
        "version": "deep-research-source-pack-smoke",
        "task": "Deeply research a topic with Firecrawl, Exa, and Context7 evidence.",
        "source_mode": "mock",
        "allowed_tools": ["firecrawl.search", "firecrawl.scrape", "context7.query-docs"],
        "planner_source": "smoke",
        "execution_mode": "planned",
        "steps": [
            {
                "step_no": 1,
                "goal": "Discover web sources.",
                "tool_name": "firecrawl.search",
                "arguments": {"query": "traceable research agent"},
                "expected_output": "Search discovery results.",
                "completion_criteria": "Discovery sources have URLs.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 2,
                "goal": "Read page body.",
                "tool_name": "firecrawl.scrape",
                "arguments": {"url": "https://example.com/firecrawl-page"},
                "expected_output": "Readable page content.",
                "completion_criteria": "Page content is available.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 3,
                "goal": "Query current technical docs.",
                "tool_name": "context7.query-docs",
                "arguments": {"query": "FastAPI docs", "libraryId": "fastapi"},
                "expected_output": "Documentation snippets.",
                "completion_criteria": "Docs evidence is available.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
        ],
        "notes": [],
    }
    run = create_run(db, plan)
    common_metadata = {
        "tool_source": "mcp_remote",
        "mcp_channel": "readonly",
        "remote_channel": "readonly",
        "headers_env": ["FIRECRAWL_API_KEY"],
    }
    record_tool_result(
        db,
        run.run_id,
        1,
        "firecrawl.search",
        {"query": "traceable research agent"},
        ToolResult(
            success=True,
            output={
                "results": [
                    {
                        "title": "Discovery result",
                        "url": "https://example.com/discovery",
                        "content": "Search discovery snippet.",
                    }
                ]
            },
            output_summary="Firecrawl search returned one discovery result.",
            metadata={
                **common_metadata,
                "remote_server": "firecrawl",
                "remote_tool_name": "search",
                "remote_registry_name": "firecrawl.search",
            },
        ),
        latency_ms=0,
    )
    record_tool_result(
        db,
        run.run_id,
        2,
        "firecrawl.scrape",
        {"url": "https://example.com/firecrawl-page"},
        ToolResult(
            success=True,
            output={
                "title": "Readable Firecrawl page",
                "url": "https://example.com/firecrawl-page",
                "markdown": "Full readable page body with structured evidence.",
            },
            output_summary="Firecrawl scrape returned readable page content.",
            metadata={
                **common_metadata,
                "remote_server": "firecrawl",
                "remote_tool_name": "scrape",
                "remote_registry_name": "firecrawl.scrape",
            },
        ),
        latency_ms=0,
    )
    record_tool_result(
        db,
        run.run_id,
        3,
        "context7.query-docs",
        {"query": "FastAPI docs", "libraryId": "fastapi"},
        ToolResult(
            success=True,
            output={
                "documents": [
                    {
                        "title": "FastAPI current docs",
                        "url": "https://context7.com/fastapi",
                        "content": "Current FastAPI documentation snippet.",
                    }
                ]
            },
            output_summary="Context7 query-docs returned documentation evidence.",
            metadata={
                **common_metadata,
                "remote_server": "context7",
                "remote_tool_name": "query-docs",
                "remote_registry_name": "context7.query-docs",
            },
        ),
        latency_ms=0,
    )
    evidence = get_evidence(client, run.run_id)
    source_types = {group["source_type"] for group in evidence["source_groups"]}
    assert_true("mcp_remote_discovery" in source_types, f"remote discovery group missing: {source_types}")
    assert_true("mcp_remote_support" in source_types, f"remote support group missing: {source_types}")
    refs = [item["source_ref"] for item in evidence["evidence_items"]]
    assert_true("https://example.com/firecrawl-page" in refs, "remote support URL missing")
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert_true("FIRECRAWL_API_KEY" in serialized, "headers_env name should remain auditable")
    assert_true("fake-firecrawl-secret" not in serialized, "secret value leaked into evidence")
    assert_true(
        any(item["metadata"].get("evidence_role") == "support" for item in evidence["evidence_items"]),
        "remote support role metadata missing",
    )
    return run.run_id


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
        markdown = report.json()["markdown"]
        assert_true("## 6. 证据聚合" not in markdown, "ReAct report should hide aggregation")
        assert_true("## 6. 证据与工具观察结果" in markdown, "ReAct report missing observations")
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
        test_tool_source_repair()
        with TestClient(app) as client:
            with SessionLocal() as db:
                test_multi_source_bundle(client, db)
                test_remote_source_pack_bundle(client, db)
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
                    "tool_source_repair": "ok",
                    "remote_source_pack_classification": "ok",
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
