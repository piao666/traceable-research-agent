"""Smoke optional LLM planner and deterministic fallback paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task
from app.agent.plan_guardrails import normalize_plan_arguments


def _tools(plan: dict) -> list[str]:
    return [step["tool_name"] for step in plan.get("steps", [])]


CONFIRMATION_NOTE_PATTERN = re.compile(
    r"(?:requires?|requiring)\s+(?:(?:human|manual|explicit)\s+)?(?:confirmation|approval)"
    r"|confirmation\s+required"
    r"|human\s+(?:approval|confirmation)"
    r"|manual\s+approval"
    r"|\u4eba\u5de5(?:\u786e\u8ba4|\u5ba1\u6279)",
    re.IGNORECASE,
)


def _assert_confirmation_notes_match_steps(plan: dict) -> None:
    required = [step for step in plan.get("steps", []) if step.get("requires_confirmation")]
    confirmation_notes = [
        note
        for note in plan.get("notes", [])
        if CONFIRMATION_NOTE_PATTERN.search(str(note))
    ]
    if required:
        for step in required:
            expected = f"step {step['step_no']}: {step['tool_name']}".lower()
            if not any(expected in str(note).lower() for note in confirmation_notes):
                raise SystemExit(f"missing authoritative confirmation note for {expected}")
    elif any("confirmation required" in str(note).lower() for note in confirmation_notes):
        raise SystemExit("normal plan notes incorrectly claim a confirmation-required step")


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
    auto_hitl = plan_task(
        "Read local docs and generate a markdown report with human approval",
        ["file_reader", "report_writer"],
        "mock",
        planner_mode="auto",
    )
    outside_path = ROOT / "workspace" / "tmp" / "llm_planner_outside_allowed_root.md"
    path_hitl = normalize_plan_arguments(
        {
            "version": "llm-smoke",
            "task": "Read explicit outside file",
            "source_mode": "mock",
            "allowed_tools": ["file_reader"],
            "steps": [
                {
                    "step_no": 1,
                    "tool_name": "file_reader",
                    "arguments": {"path": str(outside_path), "max_chars": 100},
                    "goal": "Read outside file.",
                    "expected_output": "Content.",
                    "completion_criteria": "Requires approval.",
                    "risk_level": "low",
                    "requires_confirmation": False,
                }
            ],
            "notes": [],
            "confirmation": None,
        },
        "Read explicit outside file",
        "mock",
    )

    if deterministic.get("planner_source") != "deterministic":
        raise SystemExit("deterministic planner_source mismatch")
    if auto.get("planner_source") not in {"llm", "deterministic_fallback", "deterministic"}:
        raise SystemExit(f"unexpected auto planner_source: {auto.get('planner_source')}")
    if _tools(limited) != ["file_reader"]:
        raise SystemExit(f"allowed_tools failed: {_tools(limited)}")
    report_steps = [step for step in hitl["steps"] if step["tool_name"] == "report_writer"]
    if report_steps and report_steps[0]["requires_confirmation"]:
        raise SystemExit("report_writer should not be a standalone HITL scene")
    file_step = path_hitl["steps"][0]
    if file_step.get("confirmation_reason") != "file_reader_path_outside_allowed_roots":
        raise SystemExit(f"file path HITL rule failed: {path_hitl}")
    if _tools(github) != ["mcp_github_search", "report_writer"]:
        raise SystemExit(f"GitHub planning failed: {_tools(github)}")
    _assert_confirmation_notes_match_steps(auto)
    _assert_confirmation_notes_match_steps(auto_hitl)
    auto_hitl_report_steps = [
        step for step in auto_hitl["steps"] if step["tool_name"] == "report_writer"
    ]
    if auto_hitl_report_steps and auto_hitl_report_steps[0]["requires_confirmation"]:
        raise SystemExit("auto planner report_writer should not require standalone HITL")

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
        "hitl_file_requires_confirmation": file_step["requires_confirmation"],
        "github_tools": _tools(github),
        "confirmation_notes_consistent": True,
        "auto_hitl_report_requires_confirmation": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
