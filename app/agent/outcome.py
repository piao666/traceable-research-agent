"""Shared research-completion gate. Operational success is not research success."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
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
# These are evidence-quality gates, not execution faults.  They deliberately
# produce an auditable ``incomplete`` terminal state so the caller can retry
# with different sources.  Provider/configuration/budget failures remain hard
# failures through ``force_failure`` and the explicit execution error set.
INCOMPLETE_RESEARCH_CODES = frozenset({
    "required_fetch_failed",
    "uncited_deterministic_claim",
    "no_supported_citations",
    "required_evidence_coverage_incomplete",
    "report_revision_incomplete",
})


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
        finalize_terminal_decision(db, run, plan, force_failure=result["error_code"])
        return False
    return True


TERMINAL_DECISION_VERSION = "terminal-decision-v1"


def _task_allows_discovery_completion(plan: dict[str, Any]) -> bool:
    """Return whether a plan explicitly asks only for source discovery.

    Quick discovery is a valid product result.  It must not be confused with
    substantive research, which requires content-bearing evidence and a
    citation/coverage decision.
    """
    contract = plan.get("task_contract") or {}
    goal_kind = str(contract.get("goal_kind") or plan.get("goal_kind") or "").casefold()
    return bool(
        plan.get("research_mode") == "quick"
        and (
            plan.get("quick_output_mode") == "discovery"
            or goal_kind in {"discovery", "source_discovery", "search", "lookup"}
        )
    )


def _report_hash(run) -> str | None:
    if not getattr(run, "report_path", None):
        return None
    try:
        from app.agent.report_exporter import resolve_report_path
        return hashlib.sha256(resolve_report_path(run.report_path).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


def finalize_terminal_decision(
    db, run, plan: dict[str, Any], *, traces=None,
    report_integrity: dict[str, Any] | None = None,
    scope_outcome: dict[str, Any] | None = None,
    force_failure: str | None = None,
) -> dict[str, Any]:
    """Commit the decision and run state together, using persisted evidence.

    The caller must persist report/diagnostic inputs first. Node-local success
    is not root completion; only the Scope owner calls this for a Deep root.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select, update
    from app.trace.models import AgentRun
    from app.evidence.models import ReportRevision
    from app.evidence.citation_validator import get_report_occurrence_bundle
    from app.reporting.integrity import assess_report_integrity
    from app.evidence.service import get_provenance_bundle

    current = store.get_fresh_agent_run(db, run.run_id)
    if current is None or current.status in {"cancelled", "waiting_human", "waiting_human_plan"}:
        return {"version": TERMINAL_DECISION_VERSION,
                "status": current.status if current else "failed",
                "error_code": "run_not_finalizable", "blockers": ["run_not_finalizable"],
                "warnings": []}
    run = current
    expected_plan = run.plan_json
    expected_status = run.status
    persisted = json.loads(expected_plan or "{}")
    plan = persisted if isinstance(persisted, dict) else {}
    contract = plan.get("task_contract") or {}
    outcome = plan.get("research_outcome") or {}
    integrity = report_integrity if report_integrity is not None else plan.get("report_integrity") or {}
    scope = scope_outcome if scope_outcome is not None else outcome
    report_hash = _report_hash(run)
    blockers: list[str] = []
    warnings = list(outcome.get("warnings") or []) + list(integrity.get("warnings") or [])
    if force_failure:
        blockers.append(force_failure)
    if not report_hash:
        blockers.append("report_missing")
    if integrity.get("version") != REPORT_INTEGRITY_VERSION or integrity.get("status") not in {"passed", "failed"}:
        blockers.append("report_integrity_missing")
    if outcome.get("status") != "passed":
        blockers.append(str(outcome.get("error_code") or "research_outcome_not_passed"))
    draft_result = plan.get("report_draft_result") or {}
    if isinstance(draft_result, dict) and draft_result.get("integrity") == "incomplete" and draft_result.get("adopted") is False:
        blockers.append("report_revision_incomplete")
    if scope.get("status") == "failed":
        blockers.extend(str(error) for error in scope.get("errors") or [scope.get("error_code") or "scope_outcome_failed"])

    evidence: dict[str, Any] = {}
    actual_traces = store.list_tool_traces(db, run.run_id)
    if run.research_scope_id:
        from app.evidence.scope_service import get_scope_provenance_bundle
        from app.research.scope import list_scope_traces
        try:
            evidence = get_scope_provenance_bundle(db, run.research_scope_id)
            actual_traces = list_scope_traces(db, run.research_scope_id)
        except ValueError:
            blockers.append("evidence_snapshot_missing")
    else:
        try:
            evidence = get_provenance_bundle(db, run.run_id)
        except ValueError:
            blockers.append("evidence_snapshot_missing")
    if not evidence.get("passages"):
        blockers.append("no_usable_evidence")

    # Re-run the canonical content-bearing evidence contract at the terminal
    # boundary.  Earlier planner/executor gates may run before provenance is
    # materialized; this is the first point where persisted lineage is
    # authoritative.  Preserve all gap details and use one stable blocker so
    # callers do not accidentally promote a gap's first code to a hard error.
    from app.agent.evidence_requirements import assess_required_evidence
    evidence_assessment = assess_required_evidence(contract, evidence)
    outcome = dict(outcome)
    outcome["evidence_assessment"] = evidence_assessment.as_dict()
    plan["research_outcome"] = outcome
    # When no distinct Scope outcome was supplied, its terminal snapshot is
    # the persisted research outcome including this canonical assessment.
    # Keeping the pre-assessment object here made an otherwise idempotent
    # second finalization compute a different evidence snapshot hash.
    if scope_outcome is None:
        scope = outcome
    if not evidence_assessment.passed:
        blockers.append("required_evidence_coverage_incomplete")

    revision = db.scalar(select(ReportRevision).where(
        ReportRevision.root_run_id == run.run_id,
        ReportRevision.content_hash == report_hash,
        ReportRevision.status == "complete")) if report_hash else None
    occurrences: dict[str, Any] = {}
    if revision is None:
        blockers.append("report_revision_missing")
    else:
        occurrences = get_report_occurrence_bundle(db, revision.report_revision_id)
    discovery = _task_allows_discovery_completion(plan)
    safe_discovery = discovery and bool(report_hash) and plan.get("discovery_report_sha256") == report_hash
    if discovery and not safe_discovery:
        blockers.append("discovery_report_not_verified")
    if not safe_discovery:
        if integrity.get("status") == "failed":
            blockers.append(str(integrity.get("error_code") or "report_integrity_failed"))
        if occurrences:
            actual_integrity = assess_report_integrity(occurrences, scope_bundle=evidence)
            if actual_integrity.status != "passed":
                blockers.append(actual_integrity.error_code or "report_integrity_failed")
            if actual_integrity.supported == 0:
                blockers.append("no_supported_citations")
        else:
            blockers.append("final_claim_validation_missing")
    requirements = contract.get("requirements") or []
    assessment = None
    if requirements:
        from app.research.assessor import assess_requirements
        from app.agent.source_context import build_source_context
        assessment = assess_requirements(contract, build_source_context(actual_traces),
                                         traces=actual_traces, scope_evidence=evidence)
        required_ids = {str(item.get("requirement_id") or "") for item in requirements
                        if isinstance(item, dict) and item.get("required", item.get("mandatory", True))}
        assessed = {str(item.get("requirement_id") or ""): item
                    for item in assessment.get("requirements") or []}
        if any(not rid or assessed.get(rid, {}).get("status") != "satisfied" for rid in required_ids):
            blockers.append("required_evidence_coverage_incomplete")
    coverage = scope.get("coverage_matrix") or plan.get("coverage_matrix") or {}
    if coverage.get("applicable") and coverage.get("complete") is not True:
        blockers.append("required_evidence_coverage_incomplete")

    # Freeze the materialized Source/View/Claim facts, not volatile run status
    # or gate traces. Scope bundles include every contributing child source.
    evidence_facts = {key: evidence.get(key) for key in (
        "source_documents", "source_snapshots", "passages", "claims", "edges",
        "citations", "reliability_scores", "resolutions", "scope_claim_groups",
        "scope_resolutions", "scope_identity")}
    decision_inputs = {"evidence": evidence_facts, "contract": contract,
        "assessment": assessment, "coverage": coverage, "outcome": outcome,
        "evidence_assessment": evidence_assessment.as_dict(),
        "scope_outcome": scope, "report_integrity": integrity,
        "occurrences": occurrences, "force_failure": force_failure}
    evidence_hash = hashlib.sha256(json.dumps(decision_inputs, ensure_ascii=False,
        sort_keys=True, default=str).encode()).hexdigest()
    existing = plan.get("terminal_decision") or {}
    generation = plan.get("report_generation") or {}
    # The public evidence snapshot is the frozen snapshot used by the adopted
    # report validation.  Keep the wider terminal-input digest separately so
    # a changed requirement still reopens a decision without making the
    # report/manifest identity disagree across API entry points.
    decision_evidence_snapshot_id = (
        generation.get("evidence_snapshot_id") or evidence_hash
    )
    if (existing.get("version") == TERMINAL_DECISION_VERSION
        and existing.get("report_sha256") == report_hash
        and existing.get("evidence_snapshot_id") == decision_evidence_snapshot_id
        and existing.get("decision_input_hash") == evidence_hash
        and existing.get("manifest_sha256") == generation.get("manifest_sha256")
        and existing.get("validation_identity") == generation.get("validation_identity")
        and existing.get("status") == run.status):
        return existing
    # A materialized revision from different bytes must never inherit the
    # successful decision of the currently saved report.  Add this blocker
    # before selecting the terminal state so the CAS status, decision status,
    # error message, and persisted plan are all derived from the same facts.
    if revision is not None and generation.get("report_revision_id") not in {
        None, revision.report_revision_id,
    }:
        blockers.append("report_revision_identity_mismatch")
    forced_hard_failure = bool(force_failure) and force_failure not in INCOMPLETE_RESEARCH_CODES
    hard_failure = forced_hard_failure or outcome.get("error_code") in {
        "execution_failed", "report_synthesis_failed", "budget_exhausted",
        "configuration_not_ready", "provider_failure"}
    status = ("failed" if hard_failure else "incomplete") if blockers else "completed"
    error_code = next(iter(blockers), None)
    decision = {"version": TERMINAL_DECISION_VERSION, "status": status,
        "error_code": error_code, "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)), "report_sha256": report_hash,
        "evidence_snapshot_id": decision_evidence_snapshot_id,
        "decision_input_hash": evidence_hash,
        "report_revision_id": revision.report_revision_id if revision else None,
        "task_contract_version": contract.get("version"),
        "research_outcome_version": outcome.get("version"),
        "report_integrity_version": integrity.get("version")}
    decision.update({
        "report_revision_id": revision.report_revision_id if revision else None,
        "manifest_sha256": generation.get("manifest_sha256") or plan.get("report_manifest_sha256"),
        "validation_identity": generation.get("validation_identity"),
        "writing_manifest_hash": generation.get("writing_manifest_hash"),
        "validator_version": generation.get("validator_version"),
        "adopted": bool(generation.get("adopted")),
    })
    plan["terminal_decision"] = decision
    if assessment is not None:
        plan["terminal_requirement_assessment"] = assessment
    message = None if status == "completed" else (
        outcome.get("message") if hard_failure and outcome.get("status") == "failed"
        else f"{status}: {error_code}. Inspect the persisted evidence and Trace.")
    # Compare-and-set avoids overwriting a concurrent cancellation, approval,
    # retry metadata change or another finalizer's terminal transition.
    changed = db.execute(update(AgentRun).where(
        AgentRun.run_id == run.run_id, AgentRun.status == expected_status,
        AgentRun.plan_json == expected_plan).values(
            plan_json=json.dumps(plan, ensure_ascii=False, default=str), status=status,
            error_message=message, updated_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(run)
    if changed.rowcount != 1:
        return (json.loads(run.plan_json or "{}").get("terminal_decision") or
                {"status": run.status, "error_code": "finalization_state_changed"})
    record_trace_event(db, run.run_id, max((t.step_no for t in actual_traces), default=0) + 1,
        "terminal_decision", "success" if status == "completed" else "failed", {},
        message or "Research terminal decision passed.", decision, error_message=message)
    return decision


def result_integrity(run) -> dict[str, Any]:
    """Read-only legacy classification; never rewrite old tasks or metrics."""
    try:
        plan = json.loads(run.plan_json or "{}")
        outcome = plan.get("research_outcome") or {}
        report_integrity = plan.get("report_integrity") or {}
        terminal_decision = plan.get("terminal_decision") or {}
    except (ValueError, TypeError, AttributeError):
        plan = {}
        outcome = {}
        report_integrity = {}
        terminal_decision = {}
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
        *list(terminal_decision.get("warnings") or []),
        *list(terminal_decision.get("blockers") or []),
    ]
    if outcome.get("status") == "failed" and outcome.get("message"):
        warnings.append(outcome["message"])
    is_legacy_result = bool(legacy)
    # Validation is a fact about the adopted revision, not a synonym for a
    # successful run.  Preserve it when a later execution/provider failure
    # changes the terminal outcome to failed.
    generation = plan.get("report_generation") or {}
    citation_evaluated = bool(
        not is_legacy_result
        and terminal_decision.get("version") == TERMINAL_DECISION_VERSION
        and report_integrity.get("version") == REPORT_INTEGRITY_VERSION
        and report_integrity.get("status") in {"passed", "failed"}
        and generation.get("adopted") is True
        and generation.get("report_revision_id") == terminal_decision.get("report_revision_id")
        and generation.get("content_hash") == terminal_decision.get("report_sha256")
        and generation.get("validation_identity") == terminal_decision.get("validation_identity")
        and generation.get("writing_manifest_hash") == terminal_decision.get("writing_manifest_hash")
        and generation.get("validator_version") == terminal_decision.get("validator_version")
    )
    return {"research_outcome": outcome or None,
            "terminal_decision": terminal_decision or None,
            "is_legacy_result": is_legacy_result,
            "requires_review": legacy or terminal_decision.get("status") in {"incomplete", "failed"},
            "citation_evaluated": citation_evaluated,
            "quality_warnings": (["Historical result predates current integrity or trace-to-source mapping checks; re-run before relying on its quality metrics."]
                                 if legacy else list(dict.fromkeys(warnings)))}


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
    terminal_decision = plan.get("terminal_decision") or {}
    deep_v2 = (
        plan.get("execution_mode") == "deep_research_v2"
        or getattr(run, "engine_version", None) == "v2"
    )
    if terminal_decision.get("version") == TERMINAL_DECISION_VERSION:
        if terminal_decision.get("status") != run.status:
            return "Run status does not match its persisted terminal decision."
        current_report_hash = None
        if getattr(run, "report_path", None):
            try:
                from app.agent.report_exporter import resolve_report_path
                path = resolve_report_path(run.report_path)
                current_report_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                current_report_hash = None
        if current_report_hash != terminal_decision.get("report_sha256"):
            return "The persisted report changed after its final integrity decision; re-run final validation."
        if "report_revision_identity_mismatch" in (terminal_decision.get("blockers") or []):
            return "The adopted report revision identity does not match the saved report; re-run final validation."
    if (run.status in {"failed", "cancelled"}
        or plan.get("adaptive_gate_pending") or plan.get("deepening_pending")
        or (run.report_path and run.status not in {"completed", "incomplete"})
        or (outcome and (
            (outcome.get("status") == "failed" and not (
                run.status == "incomplete" and terminal_decision.get("status") == "incomplete"))
            or run.status not in {"completed", "incomplete"}
        ))
        or (
            deep_v2
            and run.status == "completed"
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
    finalize_terminal_decision(db, run, plan, force_failure=code)
    return store.get_fresh_agent_run(db, run_id)


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
        or_(func.json_extract(safe_plan, "$.terminal_decision.version").is_(None),
            and_(func.json_extract(safe_plan, "$.terminal_decision.version") == TERMINAL_DECISION_VERSION,
                 func.json_extract(safe_plan, "$.terminal_decision.status") == "completed")),
        ~((execution_mode == "react")
          & (func.coalesce(func.json_array_length(safe_plan, "$.steps"), 0) > 0)
          & (func.coalesce(func.json_extract(safe_plan, "$.evidence_mapping_version"), "legacy") != "trace-source-v2")),
    )
