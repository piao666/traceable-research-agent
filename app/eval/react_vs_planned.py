"""Reproducible quantitative comparison for planned and ReAct execution."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import URLError

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.agent.file_access_policy import confirmation_details_for_path
from app.agent.react_executor import run_react_task
from app.config import Settings
from app.eval.fake_react_llm import FakeReActLLMClient
from app.llm.providers import create_llm_client
from app.tools.base import ToolResult
from app.tools.defaults import register_default_tools
from app.tools.mcp_github import github_search
from app.tools.registry import get_tool, register_tool
from app.trace import store


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("react_vs_planned_cases.jsonl")
DEFAULT_OUTPUT_PATH = ROOT / "workspace" / "eval_outputs" / "react_vs_planned_results.json"
DEFAULT_REPORT_PATH = ROOT / "docs" / "eval_react_vs_planned.md"


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if len(cases) < 15 or any(not case_id for case_id in case_ids):
        raise ValueError("ReAct vs planned evaluation requires at least 15 named cases.")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("ReAct vs planned evaluation case_id values must be unique.")
    return cases


def _trace_payload(trace: Any) -> dict[str, Any]:
    try:
        payload = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _trace_metadata(trace: Any) -> dict[str, Any]:
    payload = _trace_payload(trace)
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _report_text(run: Any) -> str:
    if not run or not run.report_path:
        return ""
    path = ROOT / run.report_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _failure_signal(trace: Any) -> bool:
    metadata = _trace_metadata(trace)
    summary = str(trace.output_summary or "").lower()
    return bool(
        trace.status in {"failed", "rejected"}
        or metadata.get("error_type")
        or metadata.get("fallback_used")
        or metadata.get("no_hits")
        or "no hits" in summary
    )


def _recovered(traces: list[Any], completed: bool, limitation: bool) -> bool:
    problem_indexes = [index for index, trace in enumerate(traces) if _failure_signal(trace)]
    if not problem_indexes or not completed:
        return False
    if limitation:
        return True
    first_problem = problem_indexes[0]
    if any(trace.status == "success" for trace in traces[first_problem + 1 :]):
        return True
    return any(_trace_metadata(trace).get("fallback_used") for trace in traces)


def trace_quality_score(mode: str, traces: list[Any], plan: dict[str, Any], recovered: bool) -> float:
    """Score trace completeness from 1-5 using deterministic structural rules."""

    if not traces:
        return 1.0
    score = 1.0
    if all(trace.input_summary is not None and trace.output_summary is not None for trace in traces):
        score += 1.0
    if all(trace.step_no >= 1 and trace.tool_name for trace in traces):
        score += 1.0
    if mode == "planned":
        planned_tools = [
            step.get("tool_name")
            for step in plan.get("steps") or []
            if step.get("tool_name") != "report_writer"
        ]
        traced_tools = [trace.tool_name for trace in traces]
        if all(tool in traced_tools for tool in planned_tools):
            score += 1.0
    else:
        react_traces = [trace for trace in traces if _trace_metadata(trace).get("execution_mode") == "react"]
        if react_traces and all(
            _trace_metadata(trace).get("thought")
            and _trace_metadata(trace).get("action")
            and _trace_metadata(trace).get("observation_summary") is not None
            for trace in react_traces
        ):
            score += 1.0
        state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
        if recovered or state.get("completed_with_limitation"):
            score += 1.0
    return min(score, 5.0)


def _no_hit_handler(arguments: dict[str, Any]) -> ToolResult:
    query = str(arguments.get("query") or "")
    return ToolResult(
        success=True,
        output={"query": query, "top_k": arguments.get("top_k", 3), "hits": []},
        output_summary="RAG search completed with no hits.",
        metadata={"retrieval_mode": "dense", "no_hits": True, "result_count": 0},
    )


def _unreachable_opener(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    raise URLError("day34 deterministic network failure")


def _github_fallback_handler(arguments: dict[str, Any]) -> ToolResult:
    forced = dict(arguments)
    forced["mode"] = "public_api"
    return github_search(
        forced,
        settings_obj=Settings(
            github_search_cache_enabled=False,
            github_public_api_timeout_seconds=1,
            github_public_api_max_retries=0,
            github_public_api_fallback_to_mock=True,
        ),
        opener=_unreachable_opener,
        sleeper=lambda _seconds: None,
    )


def _configure_scenario_tools(scenario: str) -> None:
    register_default_tools()
    if scenario == "rag_no_hit_recovery":
        spec = get_tool("rag_search")
        if spec is not None:
            register_tool(spec, _no_hit_handler)
    if scenario == "github_fallback_recovery":
        spec = get_tool("mcp_github_search")
        if spec is not None:
            register_tool(spec, _github_fallback_handler)


def _planned_plan(case: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for index, raw_step in enumerate(case.get("planned_steps") or [], start=1):
        step = dict(raw_step)
        step["step_no"] = index
        step.setdefault("arguments", {})
        step.setdefault("requires_confirmation", False)
        steps.append(step)
    return {
        "version": "day34-eval-planned-v1",
        "task": case["task"],
        "source_mode": "mock",
        "allowed_tools": case["allowed_tools"],
        "steps": steps,
        "notes": ["Deterministic Day34 planned baseline."],
        "confirmation": None,
        "execution_mode": "planned",
        "planner_source": "day34_eval",
    }


def _confirm_waiting_run(db: Session, run_id: str, mode: str) -> dict[str, Any]:
    run = store.get_agent_run(db, run_id)
    plan = json.loads(run.plan_json or "{}")
    if mode == "react":
        pending = (plan.get("react_state") or {}).get("pending_confirmation") or {}
        decision = pending.get("decision") or {}
        step_no = int(pending.get("step_no") or run.current_step + 1)
        tool_name = decision.get("action") or "report_writer"
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    else:
        step = next(
            item for item in plan.get("steps") or [] if item.get("requires_confirmation")
        )
        step_no = int(step["step_no"])
        tool_name = str(step["tool_name"])
        args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
    details = None
    if tool_name == "file_reader" and str(args.get("path") or "").strip():
        details = confirmation_details_for_path(str(args.get("path")))
    plan["confirmation"] = {
        "required_step_no": step_no,
        "required_tool_name": tool_name,
        "confirmation_reason": details.get("reason") if isinstance(details, dict) else None,
        "confirmation_details": details,
        "approved": True,
        "comment": "Approved by Day34 deterministic evaluation.",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(details, dict) and details.get("resolved_path"):
        plan["confirmation"]["approved_file_reader_paths"] = [details["resolved_path"]]
        plan["confirmation"]["confirmation_scope"] = "single_file_path"
    store.replace_agent_run_plan(db, run_id, plan)
    store.update_agent_run_status(db, run_id, "pending", None)
    return plan


def _run_mode(db: Session, case: dict[str, Any], mode: str, real_llm: bool) -> dict[str, Any]:
    _configure_scenario_tools(str(case["scenario"]))
    run = store.create_agent_run(
        db,
        case["task"],
        "summary",
        "mock",
        case["allowed_tools"],
    )
    plan = _planned_plan(case)
    if mode == "react":
        plan["requested_execution_mode"] = "react"
        plan["execution_mode"] = "react"
    store.update_agent_run_plan(db, run.run_id, plan)

    started = perf_counter()
    if mode == "planned":
        summary = run_plan(db, run.run_id)
    else:
        react_settings = Settings(
            execution_mode="react",
            react_enabled=True,
            react_max_steps=int(case.get("react_max_steps", 8)),
            react_same_tool_max_calls=int(case.get("same_tool_max_calls", 3)),
            react_fallback_to_planned=True,
        )
        client = (
            create_llm_client(
                Settings.from_env(),
                Settings.from_env().react_llm_provider,
                Settings.from_env().react_llm_model,
            )
            if real_llm
            else FakeReActLLMClient(case.get("react_decisions") or [])
        )
        summary = run_react_task(db, run.run_id, react_settings, client)

    waiting_seen = summary["status"] == "waiting_human"
    if waiting_seen:
        _confirm_waiting_run(db, run.run_id, mode)
        summary = (
            run_plan(db, run.run_id)
            if mode == "planned"
            else run_react_task(db, run.run_id, react_settings, client)
        )
    latency_ms = round((perf_counter() - started) * 1000, 3)

    final_run = store.get_agent_run(db, run.run_id)
    final_plan = json.loads(final_run.plan_json or "{}")
    traces = store.list_tool_traces(db, run.run_id)
    report = _report_text(final_run)
    keywords = [str(item).lower() for item in case.get("success_keywords") or []]
    keyword_matches = [keyword for keyword in keywords if keyword in report.lower()]
    report_exists = bool(report)
    completed = summary["status"] == "completed" and report_exists and (
        not keywords or bool(keyword_matches)
    )
    state = final_plan.get("react_state") if isinstance(final_plan.get("react_state"), dict) else {}
    limitation = bool(state.get("completed_with_limitation"))
    recovered = _recovered(traces, completed, limitation)
    fallback_count = sum(
        1 for trace in traces if _trace_metadata(trace).get("fallback_used")
    ) + int(bool(state.get("fallback_used")))
    quality = trace_quality_score(mode, traces, final_plan, recovered)
    return {
        "case_id": case["case_id"],
        "scenario": case["scenario"],
        "mode": mode,
        "run_id": run.run_id,
        "status": summary["status"],
        "task_completed": completed,
        "report_exists": report_exists,
        "keyword_matches": keyword_matches,
        "steps": len(traces),
        "trace_statuses": dict(Counter(trace.status for trace in traces)),
        "failure_signal": any(_failure_signal(trace) for trace in traces),
        "recovered": recovered,
        "expected_recovery": bool(case.get("expected_recovery")),
        "trace_quality_score": quality,
        "latency_ms": latency_ms,
        "fallback_count": fallback_count,
        "hitl_required": bool(case.get("requires_hitl")),
        "hitl_success": waiting_seen and completed if case.get("requires_hitl") else None,
        "completed_with_limitation": limitation,
        "trace_tools": [trace.tool_name for trace in traces],
    }


def summarize_mode(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    recovery_cases = [result for result in results if result["expected_recovery"]]
    hitl_cases = [result for result in results if result["hitl_required"]]
    return {
        "task_completion_rate": round(sum(item["task_completed"] for item in results) / total, 4),
        "report_exists_rate": round(sum(item["report_exists"] for item in results) / total, 4),
        "avg_steps": round(sum(item["steps"] for item in results) / total, 3),
        "recovery_count": sum(item["recovered"] for item in recovery_cases),
        "failed_tool_recovery_rate": round(
            sum(item["recovered"] for item in recovery_cases) / len(recovery_cases), 4
        ) if recovery_cases else 0.0,
        "trace_quality_score": round(
            sum(item["trace_quality_score"] for item in results) / total, 3
        ),
        "avg_latency_ms": round(sum(item["latency_ms"] for item in results) / total, 3),
        "fallback_count": sum(item["fallback_count"] for item in results),
        "hitl_success_rate": round(
            sum(bool(item["hitl_success"]) for item in hitl_cases) / len(hitl_cases), 4
        ) if hitl_cases else 0.0,
    }


def build_markdown(payload: dict[str, Any]) -> str:
    planned = payload["modes"]["planned"]
    react = payload["modes"]["react"]
    rows = []
    for case in payload["cases"]:
        planned_result = next(
            item for item in payload["case_results"] if item["case_id"] == case["case_id"] and item["mode"] == "planned"
        )
        react_result = next(
            item for item in payload["case_results"] if item["case_id"] == case["case_id"] and item["mode"] == "react"
        )
        notes = []
        if react_result["recovered"] and not planned_result["recovered"]:
            notes.append("ReAct adapted after the observed failure.")
        if react_result["latency_ms"] > planned_result["latency_ms"]:
            notes.append("Planned was faster in this local run.")
        if react_result["completed_with_limitation"]:
            notes.append("ReAct ended with a bounded limitation.")
        rows.append(
            f"| {case['scenario']} | {planned_result['status']} / {planned_result['trace_quality_score']:.1f} | "
            f"{react_result['status']} / {react_result['trace_quality_score']:.1f} | {' '.join(notes) or 'Comparable outcome.'} |"
        )

    def percent(value: float) -> str:
        return f"{value * 100:.1f}%"

    return "\n".join(
        [
            "# ReAct vs Planned Quantitative Evaluation",
            "",
            "## Purpose",
            "",
            "The planned executor is the stable sequential baseline. The optional ReAct executor chooses each next action from Thought/Action/Observation state. This evaluation compares completion, recovery, trace structure, and latency without adding new production behavior.",
            "",
            "## Evaluation Setup",
            "",
            f"* total cases: `{payload['total_cases']}`",
            f"* modes: `planned`, `react`",
            f"* decision source: `{payload['decision_source']}`",
            "* tools: `file_reader`, `sql_query`, `rag_search`, `mcp_github_search`, `report_writer`",
            "* data: repository demo documents, demo SQLite data, deterministic RAG, and offline GitHub mock/fallback",
            "* runtime: local Python process with the existing executors and Tool Registry",
            "",
            "## Metrics",
            "",
            "* `task_completion_rate`: completed run, report present, and at least one configured success keyword found.",
            "* `report_exists_rate`: runs with a persisted Markdown report.",
            "* `avg_steps`: average persisted trace rows.",
            "* `recovery_count`: expected failure scenarios that produced a report after a failure, rejection, empty result, fallback, or bounded limitation.",
            "* `failed_tool_recovery_rate`: recovered expected-recovery cases divided by all expected-recovery cases.",
            "* `trace_quality_score`: deterministic 1-5 structural score; planned is capped at 4 and ReAct can reach 5 when recovery/limitation is explicit.",
            "* `avg_latency_ms`: local wall-clock execution time per case, including HITL resume inside the harness.",
            "",
            "## Summary Table",
            "",
            "| Mode | Completion | Report Exists | Avg Steps | Recovery | Failed Recovery | Trace Quality | Avg Latency |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| Planned | {percent(planned['task_completion_rate'])} | {percent(planned['report_exists_rate'])} | {planned['avg_steps']:.3f} | {planned['recovery_count']} | {percent(planned['failed_tool_recovery_rate'])} | {planned['trace_quality_score']:.3f} | {planned['avg_latency_ms']:.3f} ms |",
            f"| ReAct | {percent(react['task_completion_rate'])} | {percent(react['report_exists_rate'])} | {react['avg_steps']:.3f} | {react['recovery_count']} | {percent(react['failed_tool_recovery_rate'])} | {react['trace_quality_score']:.3f} | {react['avg_latency_ms']:.3f} ms |",
            "",
            "## Scenario Breakdown",
            "",
            "Result cells show `status / trace quality`.",
            "",
            "| Scenario | Planned Result | ReAct Result | Notes |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
            "## Key Findings",
            "",
            f"* ReAct recovered `{react['recovery_count']}` expected failure scenarios versus `{planned['recovery_count']}` for planned execution.",
            f"* ReAct trace quality averaged `{react['trace_quality_score']:.3f}` versus `{planned['trace_quality_score']:.3f}` because decision rationale and observations are persisted.",
            f"* Planned averaged `{planned['avg_steps']:.3f}` steps and `{planned['avg_latency_ms']:.3f}` ms; ReAct averaged `{react['avg_steps']:.3f}` steps and `{react['avg_latency_ms']:.3f}` ms. The dynamic loop can trade a longer path for recovery context.",
            "* Results are reported as measured; this harness does not force ReAct to outperform the baseline.",
            "",
            "## Limitations",
            "",
            "* The default run uses a fake/mock deterministic ReAct decision policy for reproducibility.",
            "* Real Qwen/DeepSeek evaluation requires `RUN_REACT_REAL_LLM_EVAL=true` and a locally configured provider key.",
            "* The case set is intentionally small and is not a large-scale benchmark.",
            "* Trace quality is a rule-based structural score, not a blinded human rating.",
            "* Local millisecond latency is useful for regression comparison but not provider-scale performance modeling.",
            "",
            "## Next Step",
            "",
            "Extend optional real-LLM evaluation, add more domain tasks, and introduce human trace-quality review before treating the results as a broader benchmark.",
            "",
        ]
    )


def run_evaluation(
    cases: list[dict[str, Any]] | None = None,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    active_cases = cases or load_cases()
    real_llm_requested = os.getenv("RUN_REACT_REAL_LLM_EVAL", "false").strip().lower() in {"1", "true", "yes", "on"}
    real_settings = Settings.from_env()
    real_client = create_llm_client(real_settings, real_settings.react_llm_provider, real_settings.react_llm_model)
    real_llm = real_llm_requested and real_client.is_available()
    results: list[dict[str, Any]] = []
    from app.database import SessionLocal

    with SessionLocal() as db:
        for case in active_cases:
            results.append(_run_mode(db, case, "planned", False))
            results.append(_run_mode(db, case, "react", real_llm))
    modes = {
        mode: summarize_mode([result for result in results if result["mode"] == mode])
        for mode in ("planned", "react")
    }
    payload = {
        "react_vs_planned_eval": "ok",
        "total_cases": len(active_cases),
        "decision_source": "real_llm" if real_llm else "fake_deterministic",
        "real_llm_requested": real_llm_requested,
        "real_llm_executed": real_llm,
        "modes": modes,
        "cases": [
            {"case_id": case["case_id"], "scenario": case["scenario"]}
            for case in active_cases
        ],
        "case_results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_markdown(payload), encoding="utf-8")
    register_default_tools()
    return payload
