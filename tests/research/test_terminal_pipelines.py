from __future__ import annotations

import json

from unittest.mock import patch

from app.agent.executor import run_plan
from app.config import Settings
from app.tools.base import ToolResult
from app.trace import store

from .conftest import create_root


def _run(db, plan, result, settings):
    from app.tools.defaults import register_default_tools
    register_default_tools()
    root = store.create_agent_run(
        db,
        plan.get("task", "pipeline fixture"),
        "summary",
        "real",
        allowed_tools=["tavily_search", "web_fetcher"],
    )
    store.replace_agent_run_plan(db, root.run_id, plan)
    with (
        patch("app.agent.executor.is_executable_tool", return_value=True),
        patch("app.agent.executor.execute_tool", return_value=result),
    ):
        return run_plan(db, root.run_id, settings_obj=settings)


def test_quick_discovery_pipeline_completes_with_safe_report(db, tmp_path):
    settings = Settings(
        offline_mode=False,
        tavily_api_key="fixture",
        report_generation_mode="deterministic",
        evidence_artifact_root=str(tmp_path / "artifacts"),
        evidence_reasoning_enabled=False,
    )
    plan = {
        "task": "find sources about traceable agents",
        "execution_mode": "planned",
        "research_mode": "quick",
        "quick_mode": True,
        "quick_output_mode": "discovery",
        "task_contract": {"goal_kind": "research", "unresolved_fields": []},
        "steps": [{"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "traceable agents"}}],
    }
    result = _run(
        db,
        plan,
        ToolResult(success=True, output={"results": [{"url": "https://example.com/source", "title": "Example source", "content": "snippet"}]}),
        settings,
    )
    assert result["status"] == "completed", result
    saved = json.loads(store.get_fresh_agent_run(db, result["run_id"]).plan_json)
    assert saved["discovery_report_sha256"]
    assert "正文核验" in open("workspace/reports/" + result["run_id"] + ".md", encoding="utf-8").read()


def test_quick_substantive_snippet_stays_incomplete_without_fetch(db, tmp_path):
    settings = Settings(
        offline_mode=False,
        tavily_api_key="fixture",
        report_generation_mode="deterministic",
        evidence_artifact_root=str(tmp_path / "artifacts"),
        evidence_reasoning_enabled=False,
    )
    plan = {
        "task": "verify the exact performance result",
        "execution_mode": "planned",
        "research_mode": "quick",
        "quick_mode": True,
        "quick_output_mode": "limited_research",
        "task_contract": {"goal_kind": "research", "unresolved_fields": []},
        "steps": [{"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "performance"}}],
    }
    result = _run(
        db,
        plan,
        ToolResult(success=True, output={"results": [{"url": "https://example.com/source", "title": "Example", "content": "performance 99%"}]}),
        settings,
    )
    assert result["status"] == "incomplete", result
    assert "web_fetcher" not in [trace.tool_name for trace in store.list_tool_traces(db, result["run_id"]) ]


def test_planned_full_text_pipeline_completes_with_citation(db, tmp_path):
    settings = Settings(
        offline_mode=False,
        tavily_api_key="fixture",
        report_generation_mode="deterministic",
        evidence_artifact_root=str(tmp_path / "artifacts"),
        evidence_reasoning_enabled=False,
    )
    plan = {
        "task": "verify the documented behavior",
        "execution_mode": "planned",
        "research_mode": "deep",
        "task_contract": {"goal_kind": "research", "unresolved_fields": []},
        "steps": [{"step_no": 1, "tool_name": "web_fetcher", "arguments": {"urls": ["https://example.com/source"]}}],
    }
    result = _run(
        db,
        plan,
        ToolResult(success=True, output={"pages": [{"url": "https://example.com/source", "title": "Example", "content": "The documented behavior is stable and verified.", "content_basis": "full_text"}]}),
        settings,
    )
    assert result["status"] == "completed", result
    assert result.get("terminal_decision")
