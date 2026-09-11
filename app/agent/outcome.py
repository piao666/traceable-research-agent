"""Shared research-completion gate. Operational success is not research success."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.agent.evidence import build_evidence_bundle
from app.config import Settings
from app.reporting.integrity import REPORT_INTEGRITY_VERSION
from app.trace import store
from app.trace.logger import record_trace_event

INTEGRITY_VERSION = "research-integrity-v2"
SCOPE_INTEGRITY_VERSION = "research-scope-outcome-v2"


def report_subject(run):
    """Render the intended report status without prematurely committing a terminal run."""
    values = {column.key: getattr(run, column.key) for column in run.__table__.columns}
    return SimpleNamespace(**{**values, "status": "completed", "error_message": None})


def load_observations(traces) -> list[dict[str, Any]]:
    observations = []
    for trace in traces:
        try:
            output = json.loads(trace.output_json or "{}")
        except (ValueError, TypeError):
            output = {}
        observations.append({"trace_id": trace.trace_id, "step_no": trace.step_no,
                             "tool_name": trace.tool_name, "success": trace.status == "success",
                             "output": output, "output_summary": trace.output_summary,
                             "error_message": trace.error_message})
    return observations


def dependency_missing(step: dict, observations: list[dict]) -> bool:
    reference = step.get("arguments_from")
    if not isinstance(reference, dict):
        return False
    field = reference.get("field")
    for obs in observations:
        if obs.get("step_no") != reference.get("step_no") or not obs.get("success"):
            continue
        output = obs.get("output") or {}
        value = output.get(field) if isinstance(output, dict) else None
        if step.get("tool_name") == "web_fetcher" and field in {"results", "papers"}:
            if isinstance(value, list) and any(
                isinstance(item, dict) and any(str(item.get(key) or "").startswith(("https://", "http://"))
                                              for key in ("url", "abstract_url", "openAccessUrl", "pdf_url", "id"))
                for item in value
            ):
                return False
        elif value:
            return False
    return True


def skip_dependency(db: Session, run_id: str, step: dict) -> dict:
    message = "Upstream step returned no usable input; dependent tool was not called."
    trace = record_trace_event(db, run_id, int(step.get("step_no") or 0),
        str(step.get("tool_name")), "skipped", step.get("arguments") or {}, message,
        {"metadata": {"error_type": "dependency_unavailable", "executed": False}}, error_message=message)
    store.update_agent_run_progress(db, run_id, int(step.get("step_no") or 0))
    return {"trace_id": trace.trace_id, "step_no": step.get("step_no"), "tool_name": step.get("tool_name"),
            "success": False, "output": {}, "error_message": message}


def assess_research_outcome(run, plan, observations, traces, settings: Settings) -> dict[str, Any]:
    bundle = build_evidence_bundle(run, plan, observations, traces)
    usable = [item for item in bundle.evidence_items
              if settings.offline_mode or run.source_mode == "mock" or not (item.is_mock or item.is_fallback)]
    warnings = list(bundle.warnings)
    if any(item.is_mock or item.is_fallback for item in usable):
        warnings.append("Demonstration/fallback sources are not verified live research.")
    steps = plan.get("steps") or []
    required_fetch = any(step.get("tool_name") == "web_fetcher" and step.get("required", True) for step in steps)
    missing_required = [str(step.get("tool_name")) for step in steps
                        if step.get("required") is True and step.get("tool_name") != "report_writer"
                        and not any(item.step_no == step.get("step_no") and item.tool_name == step.get("tool_name")
                                    for item in usable)]
    code = None
    if not usable:
        code = "no_usable_evidence"
    elif required_fetch and not any(item.tool_name == "web_fetcher" for item in usable):
        code = "required_fetch_failed"
    elif missing_required:
        code = "required_step_failed"
    from app.agent.research_goal import finish_failure, structured_goal_failure
    state = plan.get("react_state") or {}
    goal_failure = finish_failure(state.get("finish_reason"), state.get("finish_summary", ""),
                                  state.get("goal_status"))
    goal_failure = goal_failure or structured_goal_failure(plan.get("task_contract") or {}, load_observations(traces))
    if goal_failure:
        code = code or goal_failure
        warnings.append("The requested result was not established; available text is not proof of goal completion.")
    if code == "task_requirements_unresolved":
        warnings.append("Clarify these task fields before retrying: " + ", ".join(plan["task_contract"]["unresolved_fields"]))
    research_steps = {step.get("step_no") for step in steps if step.get("tool_name") != "report_writer"}
    failed = [trace for trace in traces if trace.step_no in research_steps
              and trace.status in {"failed", "rejected", "skipped"}]
    if failed:
        warnings.append("Some research steps failed or were skipped; inspect the persisted Trace before using the report.")
    for observation in load_observations(traces):
        output = observation.get("output") or {}
        if isinstance(output, dict) and (
            output.get("failed_count") or output.get("failed_documents")
            or any(isinstance(page, dict) and page.get("error") for page in output.get("pages") or [])
        ):
            warnings.append("Some source pages/documents could not be read; only successfully extracted content supports this report.")
            break
    if (plan.get("react_state") or {}).get("completed_with_limitation"):
        warnings.append("ReAct stopped with a limitation; the available evidence does not imply exhaustive research.")
    warnings.extend((plan.get("preflight") or {}).get("warnings") or [])
    if plan.get("adaptive_upgrade_failed") or (plan.get("react_state") or {}).get("fallback_used"):
        warnings.append("ReAct upgrade/fallback did not complete as requested.")
    warnings.extend(plan.get("deepening_warnings") or [])
    goal_messages = {
        "goal_not_met": "研究目标未完成；现有来源不能证明任务已完成。请检查结束原因和证据。",
        "task_requirements_unresolved": "研究口径尚未明确，请补充问题中的统计间隔、起止日期或复权口径。",
        "structured_data_unavailable": "未取得覆盖目标日期范围、包含所需指标的完整结构化数据；网页正文不能代替数据结果。",
    }
    return {
        "version": INTEGRITY_VERSION, "status": "failed" if code else "passed",
        "error_code": code, "effective_evidence_count": len(usable),
        "warnings": list(dict.fromkeys(warnings)),
        "message": (f"{code}: {goal_messages[code]}" if code in goal_messages else
                    f"Research completion blocked: {code}. See tool traces." if code else "Research evidence gate passed."),
    }


def enforce_research_outcome(db, run, plan, observations, traces, settings) -> bool:
    from app.agent.budget import current_budget
    runtime = current_budget()
    if runtime is not None:
        runtime.reserve()  # A swallowed downstream budget error cannot pass the gate.
    result = assess_research_outcome(run, plan, observations, traces, settings)
    plan["research_outcome"] = result
    if result["status"] == "failed":
        plan["adaptive_gate_pending"] = False
        plan["deepening_pending"] = False
    store.replace_agent_run_plan(db, run.run_id, plan)
    record_trace_event(db, run.run_id, max((t.step_no for t in traces), default=0) + 1,
                       "research_quality_gate", "failed" if result["status"] == "failed" else "success",
                       {}, result["message"], result,
                       error_message=result["message"] if result["status"] == "failed" else None)
    if store.is_agent_run_cancelled(db, run.run_id):
        return False
    if result["status"] == "failed":
        store.update_agent_run_citation_validation(db, run.run_id, total=0, supported=0,
            weakly_supported=0, unsupported=0, accuracy=0.0)
        store.update_agent_run_status(db, run.run_id, "failed", result["message"])
        return False
    return True


def result_integrity(run) -> dict[str, Any]:
    """Read-only legacy classification; never rewrite old tasks or metrics."""
    try:
        plan = json.loads(run.plan_json or "{}")
        outcome = plan.get("research_outcome") or {}
        report_integrity = plan.get("report_integrity") or {}
    except (ValueError, TypeError, AttributeError):
        plan = {}
        outcome = {}
        report_integrity = {}
    deep_v2 = (
        plan.get("execution_mode") == "deep_research_v2"
        or getattr(run, "engine_version", None) == "v2"
    )
    report_gate_passed = bool(
        report_integrity.get("version") == REPORT_INTEGRITY_VERSION
        and report_integrity.get("status") == "passed"
    )
    legacy = run.status == "completed" and outcome.get("version") not in {
        INTEGRITY_VERSION,
        SCOPE_INTEGRITY_VERSION,
    }
    legacy = legacy or bool(run.status == "completed" and deep_v2 and not report_gate_passed)
    mapping_review = bool(run.status == "completed" and plan.get("execution_mode") == "react"
                          and plan.get("steps") and plan.get("evidence_mapping_version") != "trace-source-v2")
    legacy = legacy or mapping_review
    warnings = [
        *list(outcome.get("warnings") or []),
        *list(report_integrity.get("warnings") or []),
    ]
    if outcome.get("status") == "failed" and outcome.get("message"):
        warnings.append(outcome["message"])
    return {"research_outcome": outcome or None, "requires_review": legacy,
            "citation_evaluated": bool(
                run.citation_total
                and not legacy
                and run.status == "completed"
                and outcome.get("status") == "passed"
                and (not deep_v2 or report_gate_passed)
            ),
            "quality_warnings": (["Historical result predates current integrity or trace-to-source mapping checks; re-run before relying on its quality metrics."]
                                 if legacy else warnings)}


def report_block_reason(run) -> str | None:
    """Final-report availability, shared by HTTP downloads and SSE notifications."""
    try:
        plan = json.loads(run.plan_json or "{}")
    except (ValueError, TypeError):
        plan = {}
    if not isinstance(plan, dict):
        plan = {}
    outcome = result_integrity(run)["research_outcome"]
    report_integrity = plan.get("report_integrity") or {}
    deep_v2 = (
        plan.get("execution_mode") == "deep_research_v2"
        or getattr(run, "engine_version", None) == "v2"
    )
    if (run.status in {"failed", "cancelled"}
        or plan.get("adaptive_gate_pending") or plan.get("deepening_pending")
        or (run.report_path and run.status != "completed")
        or (outcome and (run.status != "completed" or outcome.get("status") != "passed"))
        or (
            deep_v2
            and (
                report_integrity.get("version") != REPORT_INTEGRITY_VERSION
                or report_integrity.get("status") != "passed"
            )
        )):
        return "Research has not passed final completion; any intermediate report is retained only as an audit artifact."
    return None


def fail_execution(db: Session, run_id: str, exc: Exception):
    """Persist unexpected failures without leaking provider exception payloads."""
    run = store.get_fresh_agent_run(db, run_id)
    if run is None or run.status == "cancelled":
        return run
    try:
        plan = json.loads(run.plan_json or "{}")
        if not isinstance(plan, dict):
            plan = {}
    except (TypeError, ValueError):
        plan = {}
    code = "report_synthesis_failed" if str(exc).startswith("report_synthesis_failed:") else "execution_failed"
    if type(exc).__name__ == "BudgetExceeded":
        code = "budget_exhausted"
    message = f"{code}: {type(exc).__name__}. Inspect Trace and retry the full run."
    if code == "budget_exhausted":
        message = f"budget_exhausted: {exc.reason}. Existing evidence and Trace are retained; no new operations are admitted."
    plan["research_outcome"] = {**(plan.get("research_outcome") or {}),
        "version": INTEGRITY_VERSION, "status": "failed", "error_code": code, "message": message}
    plan["adaptive_gate_pending"] = False
    plan["deepening_pending"] = False
    if plan.get("adaptive_phase"):
        plan["adaptive_phase"] = "failed"
    if plan.get("deepening_phase"):
        plan["deepening_phase"] = "failed"
    store.replace_agent_run_plan(db, run_id, plan)
    record_trace_event(db, run_id, run.current_step, "execution_failure", "failed", {},
                       message, {"error_type": code}, error_message=message)
    return store.update_agent_run_status(db, run_id, "failed", message)


def trusted_run_ids():
    """SQL subquery shared by quality aggregates; invalid/legacy JSON is excluded."""
    from sqlalchemy import and_, case, func, or_, select
    from app.trace.models import AgentRun
    safe_plan = case((func.json_valid(AgentRun.plan_json), AgentRun.plan_json), else_="{}")
    execution_mode = func.coalesce(
        func.json_extract(safe_plan, "$.execution_mode"), "planned"
    )
    deep_v2 = or_(AgentRun.engine_version == "v2", execution_mode == "deep_research_v2")
    report_gate_passed = and_(
        func.json_extract(safe_plan, "$.report_integrity.version")
        == REPORT_INTEGRITY_VERSION,
        func.json_extract(safe_plan, "$.report_integrity.status") == "passed",
    )
    return select(AgentRun.run_id).where(
        AgentRun.status == "completed",
        func.json_extract(safe_plan, "$.research_outcome.version").in_(
            [INTEGRITY_VERSION, SCOPE_INTEGRITY_VERSION]
        ),
        func.json_extract(safe_plan, "$.research_outcome.status") == "passed",
        func.json_extract(safe_plan, "$.research_outcome.effective_evidence_count") > 0,
        or_(~deep_v2, report_gate_passed),
        ~((execution_mode == "react")
          & (func.coalesce(func.json_array_length(safe_plan, "$.steps"), 0) > 0)
          & (func.coalesce(func.json_extract(safe_plan, "$.evidence_mapping_version"), "legacy") != "trace-source-v2")),
    )
