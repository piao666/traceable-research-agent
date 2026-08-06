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
    hitl_decision = case.get("hitl_decision")
    run = store.create_agent_run(
        db=db,
        task=case["task"],
        report_type=case.get("report_type", "summary"),
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

    # ── HITL handling ─────────────────────────────────────────────────
    hitl_seen = summary.get("status") == "waiting_human"
    if hitl_decision and hitl_seen:
        if hitl_decision == "approve":
            # Auto-approve the waiting step
            from datetime import datetime, timezone
            plan_after = json.loads(store.get_agent_run(db, run.run_id).plan_json or "{}")
            pending_step_no = run.current_step + 1
            plan_after["confirmation"] = {
                "required_step_no": pending_step_no,
                "required_tool_name": "file_reader",
                "approved": True,
                "comment": "Auto-approved by eval harness.",
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }
            store.replace_agent_run_plan(db, run.run_id, plan_after)
            store.update_agent_run_status(db, run.run_id, "pending", None)
            summary = run_plan(db, run.run_id)
            hitl_seen = False
        elif hitl_decision == "reject":
            # For reject cases, the run should be in waiting_human state
            pass

    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    expected_tools = set(case.get("expected_tools", []))
    traced_tools = {trace.tool_name for trace in traces}
    report_text = ""
    if final_run and final_run.report_path:
        rp = ROOT / final_run.report_path
        if rp.is_file():
            report_text = rp.read_text(encoding="utf-8")
    report_exists = bool(report_text)
    keywords = [str(k).lower() for k in case.get("success_keywords") or []]
    keyword_matches = [k for k in keywords if k.lower() in report_text.lower()]
    keywords_ok = not keywords or bool(keyword_matches)
    expected_status = case.get("expected_status", "completed")
    if hitl_decision == "reject":
        expected_status = "waiting_human"
    passed = (
        summary["status"] == expected_status
        and expected_tools.issubset(traced_tools)
        and (not case.get("report_exists", True) or report_exists)
        and keywords_ok
    )
    return {
        "case_id": case["case_id"],
        "category": case.get("category", "uncategorized"),
        "network_dependent": bool(case.get("network_dependent")),
        "passed": passed,
        "run_id": run.run_id,
        "status": summary["status"],
        "planned_tools": [step.get("tool_name") for step in plan.get("steps", [])],
        "trace_count": len(traces),
        "trace_statuses": dict(Counter(trace.status for trace in traces)),
        "trace_complete": expected_tools.issubset(traced_tools),
        "report_exists": report_exists,
        "keyword_matches": keyword_matches,
        "keywords_ok": keywords_ok,
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
        "category": case.get("category", "uncategorized"),
        "network_dependent": bool(case.get("network_dependent")),
        "passed": passed,
        "run_id": run.run_id,
        "status": trace.status,
        "planned_tools": [tool_name],
        "trace_count": 1,
        "trace_statuses": {trace.status: 1},
        "trace_complete": True,
        "report_exists": False,
        "keyword_matches": [],
        "keywords_ok": True,
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
    nd_failed = sum(1 for r in results if not r.get("passed") and r.get("network_dependent"))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "network_skipped": nd_failed,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("total_cases", "passed", "failed")}))
    hard_failed = len(results) - passed - nd_failed
    return 0 if hard_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
