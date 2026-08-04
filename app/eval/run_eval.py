"""Run deterministic local evaluation cases without a live API service."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.tools.defaults import register_default_tools
from app.tools.registry import execute_tool
from app.trace import store
from app.trace.logger import record_tool_result


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("cases.jsonl")
OUTPUT_PATH = ROOT / "workspace" / "eval_outputs" / "eval_report.json"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def prepare_runtime() -> None:
    from scripts.init_demo_db import init_demo_db

    init_db()
    register_default_tools()
    init_demo_db()


def run_task_case(db, case: dict[str, Any]) -> dict[str, Any]:
    run = store.create_agent_run(
        db=db,
        task=case["task"],
        report_type="summary",
        source_mode="mock",
        allowed_tools=case.get("allowed_tools"),
    )
    plan = plan_task(
        case["task"],
        case.get("allowed_tools"),
        "mock",
        planner_mode="deterministic",
    )
    store.update_agent_run_plan(db, run.run_id, plan)
    summary = run_plan(db, run.run_id)
    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    expected_tools = set(case.get("expected_tools", []))
    traced_tools = {trace.tool_name for trace in traces}
    report_exists = bool(
        final_run
        and final_run.report_path
        and (ROOT / final_run.report_path).is_file()
    )
    expected_status = case.get("expected_status", "completed")
    passed = (
        summary["status"] == expected_status
        and expected_tools.issubset(traced_tools)
        and (not case.get("report_exists", True) or report_exists)
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": run.run_id,
        "status": summary["status"],
        "planned_tools": [step.get("tool_name") for step in plan.get("steps", [])],
        "trace_count": len(traces),
        "trace_statuses": dict(Counter(trace.status for trace in traces)),
        "trace_complete": expected_tools.issubset(traced_tools),
        "report_exists": report_exists,
    }


def run_direct_tool_case(db, case: dict[str, Any]) -> dict[str, Any]:
    tool_name = case["tool_name"]
    arguments = case.get("arguments") or {}
    run = store.create_agent_run(
        db=db,
        task=f"Evaluation: {case['case_id']}",
        report_type="summary",
        source_mode="mock",
        allowed_tools=[tool_name],
    )
    result = execute_tool(tool_name, arguments)
    trace = record_tool_result(
        db=db,
        run_id=run.run_id,
        step_no=1,
        tool_name=tool_name,
        input_data=arguments,
        result=result,
        latency_ms=0,
    )
    expected_status = case["expected_trace_status"]
    passed = trace.status == expected_status and result.success is case["should_succeed"]
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": run.run_id,
        "status": trace.status,
        "planned_tools": [tool_name],
        "trace_count": 1,
        "trace_statuses": {trace.status: 1},
        "trace_complete": True,
        "report_exists": False,
    }


def run_case(db, case: dict[str, Any]) -> dict[str, Any]:
    try:
        if case.get("mode") == "direct_tool":
            return run_direct_tool_case(db, case)
        return run_task_case(db, case)
    except Exception as exc:
        return {
            "case_id": case.get("case_id", "unknown"),
            "passed": False,
            "status": "failed",
            "failure_reason": str(exc),
        }


def main() -> int:
    prepare_runtime()
    with SessionLocal() as db:
        results = [run_case(db, case) for case in load_cases()]

    passed = sum(1 for result in results if result.get("passed"))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("total_cases", "passed", "failed")}))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
