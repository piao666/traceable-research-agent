"""Run local eval cases without requiring a live Uvicorn service."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.rag.build_index import build_local_index
from app.rag.embedding_backends import create_embedding_backend
from app.rag.vector_backends import create_vector_backend
from app.config import Settings, settings
from app.tools.registry import execute_tool
from app.tools.defaults import register_default_tools
from app.trace import store
from app.trace.logger import record_tool_result


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("cases.jsonl")
OUTPUT_DIR = ROOT / "workspace" / "eval_outputs"
OUTPUT_PATH = OUTPUT_DIR / "eval_report.json"


def _load_cases() -> list[dict[str, Any]]:
    cases = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def _prepare_runtime() -> None:
    from scripts.init_demo_db import init_demo_db

    init_db()
    register_default_tools()
    init_demo_db()
    build_local_index()


def _run_task_case(db, case: dict[str, Any]) -> dict[str, Any]:
    run = store.create_agent_run(
        db=db,
        task=case["task"],
        report_type="summary",
        source_mode="mock",
        allowed_tools=case.get("allowed_tools"),
    )
    plan = plan_task(case["task"], case.get("allowed_tools"), "mock", planner_mode="deterministic")
    store.update_agent_run_plan(db, run.run_id, plan)
    planned_tools = [step["tool_name"] for step in plan.get("steps", [])]
    summary = run_plan(db, run.run_id)
    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    expected_status = case.get("checks", {}).get("expected_status", "completed")
    expected_tools = set(case.get("expected_tools", []))
    traced_tools = {trace.tool_name for trace in traces}
    report_exists = bool(final_run.report_path and (ROOT / final_run.report_path).exists())
    trace_complete = expected_tools.issubset(traced_tools)
    passed = summary["status"] == expected_status and trace_complete and (
        not case.get("checks", {}).get("report_exists") or report_exists
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": run.run_id,
        "status": summary["status"],
        "planned_tools": planned_tools,
        "trace_count": len(traces),
        "trace_statuses": Counter(trace.status for trace in traces),
        "trace_complete": trace_complete,
        "report_exists": report_exists,
        "failure_reason": None if passed else "task checks did not match expectations",
    }


def _run_direct_tool_case(db, case: dict[str, Any]) -> dict[str, Any]:
    run = store.create_agent_run(
        db=db,
        task=f"Eval direct tool case {case['case_id']}",
        report_type="summary",
        source_mode="mock",
        allowed_tools=[case["tool_name"]],
    )
    result = execute_tool(case["tool_name"], case.get("arguments") or {})
    trace = record_tool_result(
        db=db,
        run_id=run.run_id,
        step_no=1,
        tool_name=case["tool_name"],
        input_data=case.get("arguments") or {},
        result=result,
        latency_ms=0,
    )
    expected_status = case.get("expected_trace_status")
    passed = trace.status == expected_status and result.success is case.get("should_succeed")
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": run.run_id,
        "status": "completed",
        "planned_tools": [case["tool_name"]],
        "trace_count": 1,
        "trace_statuses": Counter([trace.status]),
        "trace_complete": True,
        "report_exists": False,
        "failure_reason": None if passed else f"expected trace status {expected_status}, got {trace.status}",
    }


def _run_hitl_case(db, case: dict[str, Any]) -> dict[str, Any]:
    run = store.create_agent_run(
        db=db,
        task=case["task"],
        report_type="summary",
        source_mode="mock",
        allowed_tools=case.get("allowed_tools"),
    )
    plan = plan_task(case["task"], case.get("allowed_tools"), "mock", planner_mode="deterministic")
    store.update_agent_run_plan(db, run.run_id, plan)
    waiting = run_plan(db, run.run_id)
    report_step = next(step for step in plan["steps"] if step["tool_name"] == "report_writer")
    plan["confirmation"] = {
        "required_step_no": report_step["step_no"],
        "required_tool_name": "report_writer",
        "approved": True,
        "comment": "Approved by eval runner.",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    store.replace_agent_run_plan(db, run.run_id, plan)
    completed = run_plan(db, run.run_id)
    final_run = store.get_agent_run(db, run.run_id)
    traces = store.list_tool_traces(db, run.run_id)
    report_exists = bool(final_run.report_path and (ROOT / final_run.report_path).exists())
    passed = waiting["status"] == "waiting_human" and completed["status"] == "completed" and report_exists
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": run.run_id,
        "status": completed["status"],
        "planned_tools": [step["tool_name"] for step in plan.get("steps", [])],
        "trace_count": len(traces),
        "trace_statuses": Counter(trace.status for trace in traces),
        "trace_complete": True,
        "report_exists": report_exists,
        "failure_reason": None if passed else "HITL waiting/confirmation flow failed",
    }


def _run_repeated_case(db, case: dict[str, Any]) -> dict[str, Any]:
    result = _run_task_case(db, case)
    run_id = result["run_id"]
    before = len(store.list_tool_traces(db, run_id))
    repeated = run_plan(db, run_id)
    after = len(store.list_tool_traces(db, run_id))
    passed = result["passed"] and repeated["status"] == "completed" and before == after
    result["passed"] = passed
    result["repeated_message"] = repeated.get("message")
    result["failure_reason"] = None if passed else "Repeated run wrote duplicate trace or changed status"
    return result


def _run_llm_planner_case(case: dict[str, Any]) -> dict[str, Any]:
    plan = plan_task(case["task"], case.get("allowed_tools"), "mock", planner_mode="auto")
    expected_sources = set(case.get("expected_planner_sources") or [])
    planner_source = plan.get("planner_source")
    steps = plan.get("steps") or []
    passed = planner_source in expected_sources and all(
        step.get("tool_name") in set(case.get("allowed_tools") or []) for step in steps
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": None,
        "status": "planned",
        "planned_tools": [step.get("tool_name") for step in steps],
        "planner_source": planner_source,
        "trace_count": 0,
        "trace_statuses": Counter(),
        "trace_complete": True,
        "report_exists": False,
        "failure_reason": None if passed else f"Unexpected planner_source {planner_source}",
    }


def _run_rag_backend_case(case: dict[str, Any]) -> dict[str, Any]:
    default_settings = Settings(
        rag_embedding_backend="deterministic",
        rag_vector_backend="json",
        rag_real_backend_enabled=False,
    )
    embedding = create_embedding_backend(default_settings)
    vector = create_vector_backend(default_settings)
    query_result = embedding.embed_query("trace tool registry")
    build_result = build_local_index(settings_obj=default_settings)
    search_result = vector.search(query_result.vectors[0], top_k=3)
    passed = (
        embedding.name == "deterministic"
        and vector.name == "json"
        and query_result.success
        and build_result.get("success")
        and search_result.success
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": None,
        "status": "validated" if passed else "failed",
        "planned_tools": ["rag_search"],
        "trace_count": 0,
        "trace_statuses": Counter(),
        "trace_complete": True,
        "report_exists": False,
        "failure_reason": None if passed else "Default RAG backend abstraction failed",
    }


def _run_real_rag_optional_case(case: dict[str, Any]) -> dict[str, Any]:
    enabled = os.getenv("RUN_REAL_RAG_EVAL", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return {
            "case_id": case["case_id"],
            "passed": True,
            "run_id": None,
            "status": "skipped",
            "planned_tools": ["rag_search"],
            "trace_count": 0,
            "trace_statuses": Counter(),
            "trace_complete": True,
            "report_exists": False,
            "failure_reason": None,
        }

    build_result = build_local_index(settings_obj=settings)
    result = execute_tool(
        "rag_search",
        {"query": "trace persistence tool registry report", "top_k": 3},
    )
    passed = (
        build_result.get("success")
        and result.success
        and result.metadata.get("embedding_backend") == "sentence_transformers"
        and result.metadata.get("vector_backend") == "chroma"
        and result.metadata.get("fallback_used") is False
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": None,
        "status": "validated" if passed else "failed",
        "planned_tools": ["rag_search"],
        "trace_count": 0,
        "trace_statuses": Counter(),
        "trace_complete": True,
        "report_exists": False,
        "failure_reason": None if passed else "Optional real RAG validation failed",
    }


def _run_auth_async_default_case(case: dict[str, Any]) -> dict[str, Any]:
    default_settings = Settings()
    safe_summary = default_settings.get_safe_auth_config_summary()
    passed = (
        default_settings.auth_enabled is False
        and default_settings.async_run_enabled is True
        and safe_summary["demo_api_key_configured"] is False
        and "demo_api_key" not in safe_summary
    )
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "run_id": None,
        "status": "validated" if passed else "failed",
        "planned_tools": [],
        "trace_count": 0,
        "trace_statuses": Counter(),
        "trace_complete": True,
        "report_exists": False,
        "failure_reason": None if passed else "Default auth/async configuration is unsafe",
    }


def _run_case(db, case: dict[str, Any]) -> dict[str, Any]:
    try:
        mode = case.get("mode", "task_run")
        if mode == "direct_tool":
            return _run_direct_tool_case(db, case)
        if mode == "hitl":
            return _run_hitl_case(db, case)
        if mode == "repeated_run":
            return _run_repeated_case(db, case)
        if mode == "llm_planner":
            return _run_llm_planner_case(case)
        if mode == "rag_backend":
            return _run_rag_backend_case(case)
        if mode == "real_rag_optional":
            return _run_real_rag_optional_case(case)
        if mode == "auth_async_default":
            return _run_auth_async_default_case(case)
        return _run_task_case(db, case)
    except Exception as exc:
        return {
            "case_id": case.get("case_id"),
            "passed": False,
            "status": "exception",
            "trace_count": 0,
            "trace_statuses": Counter(),
            "trace_complete": False,
            "report_exists": False,
            "failure_reason": str(exc),
        }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    failed = total - passed
    trace_complete = sum(1 for result in results if result.get("trace_complete"))
    report_exists = sum(1 for result in results if result.get("report_exists"))
    safety_hit = sum(
        1 for result in results if result.get("trace_statuses", Counter()).get("rejected", 0) > 0
    )
    failure_visible = sum(
        1
        for result in results
        if result.get("trace_statuses", Counter()).get("failed", 0) > 0
        or result.get("trace_statuses", Counter()).get("rejected", 0) > 0
    )
    return {
        "total_cases": total,
        "passed": passed,
        "failed": failed,
        "task_success_rate": round(passed / total, 4) if total else 0,
        "trace_complete_rate": round(trace_complete / total, 4) if total else 0,
        "report_exists_count": report_exists,
        "safety_hit_count": safety_hit,
        "failure_visible_count": failure_visible,
    }


def main() -> None:
    _prepare_runtime()
    cases = _load_cases()
    with SessionLocal() as db:
        results = [_run_case(db, case) for case in cases]
    summary = _summarize(results)
    payload = {"summary": summary, "results": results}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
