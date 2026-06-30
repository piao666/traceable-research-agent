"""Smoke check for deterministic planner rules."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task
from app.agent.plan_guardrails import normalize_plan_arguments


def main() -> None:
    task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
    plan = plan_task(
        task=task,
        allowed_tools=["file_reader", "sql_query", "rag_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    tool_names = [step["tool_name"] for step in plan["steps"]]
    expected = ["file_reader", "sql_query", "rag_search", "report_writer"]
    if tool_names != expected:
        raise SystemExit(f"Unexpected tool sequence: {tool_names}")

    step_numbers = [step["step_no"] for step in plan["steps"]]
    if step_numbers != list(range(1, len(step_numbers) + 1)):
        raise SystemExit(f"Step numbers are not consecutive: {step_numbers}")

    limited = plan_task(
        task="Read local docs and query database then generate report",
        allowed_tools=["file_reader"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    limited_tools = [step["tool_name"] for step in limited["steps"]]
    if limited_tools != ["file_reader"]:
        raise SystemExit(f"allowed_tools restriction failed: {limited_tools}")
    if not limited["notes"]:
        raise SystemExit("Expected allowed_tools restriction notes.")

    empty = plan_task(
        task="Read local docs and query database then generate report",
        allowed_tools=[],
        source_mode="mock",
        planner_mode="deterministic",
    )
    if empty["steps"] or "No executable planning step" not in " ".join(empty["notes"]):
        raise SystemExit(f"Expected empty plan for allowed_tools=[], got {empty}")

    outside_path = ROOT / "workspace" / "tmp" / "planner_outside_allowed_root.md"
    hitl = {
        "version": "planner-smoke",
        "task": "Read an explicit outside file",
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
    }
    hitl = normalize_plan_arguments(hitl, hitl["task"], "mock")
    file_step = hitl["steps"][0]
    if (
        file_step["tool_name"] != "file_reader"
        or file_step["requires_confirmation"] is not True
        or file_step.get("confirmation_reason") != "file_reader_path_outside_allowed_roots"
    ):
        raise SystemExit(f"Expected file_reader path HITL confirmation, got {hitl}")

    external = plan_task(
        task="Search GitHub repository issues and current web sources about traceable research agent and generate a markdown report",
        allowed_tools=["mcp_github_search", "tavily_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    external_tools = [step["tool_name"] for step in external["steps"]]
    if external_tools != ["tavily_search", "mcp_github_search", "report_writer"]:
        raise SystemExit(f"Expected external research planning path, got {external_tools}")

    chinese_web = plan_task(
        task="帮我全网搜集关于 LLM 的学习资料、课程和教程，并生成报告",
        allowed_tools=["tavily_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    chinese_web_tools = [step["tool_name"] for step in chinese_web["steps"]]
    if chinese_web_tools != ["tavily_search", "report_writer"]:
        raise SystemExit(f"Expected Chinese web keywords to trigger Tavily, got {chinese_web_tools}")

    print(
        {
            "planner": "ok",
            "tools": tool_names,
            "limited_tools": limited_tools,
            "limited_notes": limited["notes"],
            "empty_steps": len(empty["steps"]),
            "hitl_file_requires_confirmation": file_step["requires_confirmation"],
            "external_tools": external_tools,
            "chinese_web_tools": chinese_web_tools,
        }
    )


if __name__ == "__main__":
    main()
