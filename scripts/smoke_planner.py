"""Smoke check for deterministic planner rules."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task


def main() -> None:
    task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
    plan = plan_task(
        task=task,
        allowed_tools=["file_reader", "sql_query", "rag_search", "report_writer"],
        source_mode="mock",
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
    )
    limited_tools = [step["tool_name"] for step in limited["steps"]]
    if limited_tools != ["file_reader"]:
        raise SystemExit(f"allowed_tools restriction failed: {limited_tools}")
    if not limited["notes"]:
        raise SystemExit("Expected allowed_tools restriction notes.")

    print(
        {
            "planner": "ok",
            "tools": tool_names,
            "limited_tools": limited_tools,
            "limited_notes": limited["notes"],
        }
    )


if __name__ == "__main__":
    main()
