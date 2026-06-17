"""Smoke optional LLM planner and deterministic fallback paths."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task


def _tools(plan: dict) -> list[str]:
    return [step["tool_name"] for step in plan.get("steps", [])]


def main() -> None:
    task = (
        "Read local docs, query database metrics, retrieve trace evidence, "
        "search GitHub repository issues, and generate a markdown report"
    )
    deterministic = plan_task(
        task,
        ["file_reader", "sql_query", "rag_search", "mcp_github_search", "report_writer"],
        "mock",
        planner_mode="deterministic",
    )
    auto = plan_task(
        task,
        ["file_reader", "sql_query", "rag_search", "mcp_github_search", "report_writer"],
        "mock",
        planner_mode="auto",
    )
    limited = plan_task(
        "Read local docs and query database then generate report",
        ["file_reader"],
        "mock",
        planner_mode="deterministic",
    )
    hitl = plan_task(
        "Read local docs and generate a markdown report with human approval",
        ["file_reader", "report_writer"],
        "mock",
        planner_mode="deterministic",
    )
    github = plan_task(
        "Search GitHub repository issues about traceable research agent and generate a markdown report",
        ["mcp_github_search", "report_writer"],
        "mock",
        planner_mode="deterministic",
    )

    if deterministic.get("planner_source") != "deterministic":
        raise SystemExit("deterministic planner_source mismatch")
    if auto.get("planner_source") not in {"llm", "deterministic_fallback", "deterministic"}:
        raise SystemExit(f"unexpected auto planner_source: {auto.get('planner_source')}")
    if _tools(limited) != ["file_reader"]:
        raise SystemExit(f"allowed_tools failed: {_tools(limited)}")
    report_steps = [step for step in hitl["steps"] if step["tool_name"] == "report_writer"]
    if not report_steps or not report_steps[0]["requires_confirmation"]:
        raise SystemExit("HITL rule failed")
    if _tools(github) != ["mcp_github_search", "report_writer"]:
        raise SystemExit(f"GitHub planning failed: {_tools(github)}")

    payload = {
        "deterministic": {
            "planner_source": deterministic.get("planner_source"),
            "tools": _tools(deterministic),
        },
        "auto": {
            "planner_source": auto.get("planner_source"),
            "llm_provider": auto.get("llm_provider"),
            "llm_model": auto.get("llm_model"),
            "tools": _tools(auto),
            "notes_tail": list(auto.get("notes") or [])[-2:],
        },
        "allowed_tools": _tools(limited),
        "hitl_report_requires_confirmation": report_steps[0]["requires_confirmation"],
        "github_tools": _tools(github),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
