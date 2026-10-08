"""Cause-driven obligation recovery, independent of the report rewrite limit."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from app.agent.budget import acquisition_budget, finalization_budget, current_budget, FinalizationRequired
from app.research.assessor import persist_plan_contract
from app.research.control_store import sync_work, apply_coverage, project_work, ensure_work, begin_action, finish_action
from app.research.recovery import refresh_comparison_contract, recover_answer_evidence, actionable_gaps
from app.trace import store
from app.trace.logger import record_phase_event


def run_work_loop(db: Any, run_id: str, plan: dict, settings: Any, bundle: dict, client: Any,
                  *, loader: Callable[[], dict], traces: list, feedback: dict | None = None,
                  dispatch: Callable[[dict], bool] | None = None, allow_acquisition: bool = True) -> dict:
    """Rejudge after every effect; only verified answers discharge obligations.

    The DB ledger is durable across pauses/restarts. The local bound protects
    against non-progress and is independent of the writer's revision counter.
    Each external operation remains governed by Registry and the root budget.
    """
    contract = plan.get("task_contract") or {}
    if not contract.get("obligation_version") or client is None or not client.is_available():
        return bundle
    sync_work(db, run_id, plan)
    state = plan.setdefault("work_controller", {"version": "research-work-controller-v1", "assessments": []})
    pending_feedback = feedback
    for iteration in range(64):
        # Inventory expansion creates real cells. The initial empty cohort
        # must not freeze the entire task's allowance at three actions.
        count = sum(w["answer_status"] != "superseded" and w["requirement_id"] != "req-original"
                    for w in project_work(db, run_id))
        limit = max(3, min(64, count * (max(1, settings.max_refetch_rounds) + 2)))
        if iteration >= limit:
            state["stop_reason"] = "work_loop_safety_bound"
            break
        if store.is_agent_run_cancelled(db, run_id):
            state["stop_reason"] = "cancelled"
            break
        try:
            previous_entities = list((plan["task_contract"].get("comparison_scope") or {}).get("entities") or [])
            # Iterative research judgments must not consume the writer's
            # reserve. Only a final, acquisition-disabled assessment may use it.
            judgment_budget = acquisition_budget if allow_acquisition else finalization_budget
            with judgment_budget():
                refresh_comparison_contract(db, run_id, plan, bundle, client)
            contract = plan["task_contract"]
            provider_failures = [r for r in ((contract.get("comparison_scope") or {}).get("selection_attempt") or {}).get("rejections", [])
                                 if r.get("cause") == "selection_provider_failure"]
            if provider_failures:
                state["stop_reason"] = "selection_provider_failure"
                record_phase_event(db, run_id, "research_work_adjudication", "failed",
                    details={"failures": provider_failures, "answer_confirmed": False})
                break
            if previous_entities != list((contract.get("comparison_scope") or {}).get("entities") or []):
                # Selection changes the executable cell inventory. Discard a
                # stale selection diagnosis and assess the technical work now.
                pending_feedback = None
            sync_work(db, run_id, plan)
            if pending_feedback is None:
                from app.agent.evidence_requirements import assess_required_evidence
                admission = assess_required_evidence(contract, bundle)
                if not admission.eligible_passage_ids:
                    requirement_map = {r["requirement_id"]: r for r in contract.get("requirements", [])}
                    missing = list(admission.gaps) or []
                    fallback_id = "req-original" if "req-original" in requirement_map else next(iter(requirement_map), "substantive_web_evidence")
                    pending_feedback = {"answer_gaps": [{"requirement_id": g.requirement_id if g.requirement_id in requirement_map else fallback_id,
                        "cause": "body_evidence_missing", "predicate": requirement_map.get(g.requirement_id, {}).get("predicate") or contract.get("original_task"),
                        "detail": g.detail} for g in missing]}
                    if not pending_feedback["answer_gaps"]:
                        state["stop_reason"] = "no_admitted_body"
                        break
            if pending_feedback is None:
                from app.research.findings import assess_research_findings
                from app.reporting.writing_evidence import build_writing_evidence
                from app.agent.budget import final_report_evidence_token_budget
                from app.research.state import semantic_contract
                writing = build_writing_evidence(bundle, contract, final_report_evidence_token_budget())
                projection = hashlib.sha256(json.dumps({"contract": semantic_contract(contract),
                    "focus": [{k: f.get(k) for k in ("requirement_id", "entity", "facet")} for f in contract.get("evidence_focus", [])], "windows": [
                    (u.passage_id, u.text_sha256, u.citation_id) for u in writing.factual_units]},
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                prior = (plan.get("research_findings") or [{}])[-1]
                if prior.get("projection_fingerprint") == projection and not prior.get("provider_failure"):
                    findings = prior
                    record_phase_event(db, run_id, "research_work_projection_unchanged", "warning",
                        details={"projection_fingerprint": projection, "answer_confirmed": False,
                            "action": "retain gaps and attempt another admissible cell"})
                else:
                    with judgment_budget():
                        findings = assess_research_findings(bundle, contract, client, previous=prior if prior else None)
                    findings["projection_fingerprint"] = projection
                    plan.setdefault("research_findings", []).append(findings)
                finding_failure = findings.get("provider_failure") or findings.get("coverage", {}).get("provider_failure")
                if finding_failure:
                    contract["research_provider_failure"] = finding_failure
                    state["stop_reason"] = "findings_provider_failure"
                    persist_plan_contract(db, root_run_id=run_id, contract=contract)
                    record_phase_event(db, run_id, "research_work_adjudication", "failed", details={"failure": finding_failure})
                    break
                inventory_failure = (contract.get("answer_scope_attempt") or {}).get("provider_failure")
                if inventory_failure:
                    state["stop_reason"] = "inventory_provider_failure"
                    record_phase_event(db, run_id, "research_work_adjudication", "failed",
                        details={"failure": inventory_failure, "decision_audit": contract["answer_scope_attempt"].get("decision_audit")})
                    break
                coverage = findings["coverage"]
                apply_coverage(db, run_id, coverage)
                concrete_gaps = coverage.get("gaps") or []
                individual_gaps = [g for g in concrete_gaps if g.get("requirement_id") != "req-original"]
                pending_feedback = {"answer_gaps": individual_gaps or concrete_gaps,
                    "must_remove_or_rewrite_unsupported": [d for d in findings.get("validation", {}).get("details", [])
                                                           if d.get("verdict") != "supported"]}
                digest = hashlib.sha256(json.dumps(coverage, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                state["assessments"].append({"coverage_sha256": digest, "coverage": coverage,
                    "decision_audit": findings.get("decision_audit")})
                sync_work(db, run_id, plan)
                persist_plan_contract(db, root_run_id=run_id, contract=contract)
                project_work(db, run_id, plan)
                store.replace_agent_run_plan(db, run_id, plan)
                record_phase_event(db, run_id, "research_work_assessment", "success" if coverage.get("complete") else "warning",
                    details={"coverage": coverage, "work_items": plan["research_work"]["items"]})
                if coverage.get("complete") and not pending_feedback["must_remove_or_rewrite_unsupported"]:
                    state["stop_reason"] = "candidate_answers_ready"
                    break
            runtime = current_budget()
            if not allow_acquisition or runtime is not None and not runtime.can_deepen(required_llm_calls=3):
                state["stop_reason"] = "protected_finalization_reserve"
                break
            # A Deep adapter can reopen technical branches after selection
            # succeeds during report recovery. It consumes the same cell gaps.
            focused = contract.get("evidence_focus") or []
            local_gaps = [g for g in actionable_gaps(pending_feedback.get("answer_gaps", [])) if g.get("entity")
                and g.get("cause") not in {"source_quality_missing", "comparison_selection_missing"}
                and not str(g.get("facet") or "").startswith("selection")
                and not any(all(f.get(k) == g.get(k) for k in ("requirement_id", "entity", "facet")) for f in focused)]
            if local_gaps:
                # Project all currently missing cells together. Each intent is
                # journaled separately, then one rejudgment uses that view.
                for gap in local_gaps:
                    focus = {k: gap.get(k) for k in ("requirement_id", "entity", "facet")}
                    row = ensure_work(db, run_id, gap["requirement_id"], gap["entity"], gap.get("facet") or "answer")
                    item = next(w for w in project_work(db, run_id) if w["work_item_id"] == row.work_item_id)
                    op = begin_action(db, run_id, item, "reproject", focus, "admitted-context-v1")
                    restored = any(a["kind"] == "reproject" and a["status"] == "succeeded" and a.get("arguments") == focus
                                   for a in item.get("actions", []))
                    if op or restored:
                        contract.setdefault("evidence_focus", []).append(focus)
                    if op:
                        finish_action(db, op, "succeeded", None, {"new_body_units": 0, "requires_rejudgment": True})
                store.replace_agent_run_plan(db, run_id, plan)
                bundle = loader() or bundle
                pending_feedback = None
                continue
            dispatched = dispatch(pending_feedback) if dispatch is not None else False
            prior_attempts = len((plan.get("answer_recovery") or {}).get("attempts") or [])
            changed = dispatched or recover_answer_evidence(db, run_id, plan, settings, pending_feedback, traces=traces)
            if not changed:
                recovery = plan.get("answer_recovery") or {}
                attempts = (recovery.get("attempts") or [])[prior_attempts:]
                if recovery.get("stop_reason") == "no_new_body_evidence" and any(a.get("actions") for a in attempts):
                    # Failure/no new body belongs to this attempted cell. Keep
                    # its gap and try other admissible cells before stopping
                    # the whole task. No unchanged-evidence LLM call is needed.
                    record_phase_event(db, run_id, "research_work_no_body_effect", "warning",
                        details={"attempts": attempts, "answer_confirmed": False})
                    continue
                state["stop_reason"] = (plan.get("answer_recovery") or {}).get("stop_reason") or "no_admissible_action"
                break
            bundle = loader() or bundle
            pending_feedback = None
        except FinalizationRequired:
            state["stop_reason"] = "protected_finalization_reserve"
            break
    else:
        state["stop_reason"] = "work_loop_safety_bound"
    project_work(db, run_id, plan)
    store.replace_agent_run_plan(db, run_id, plan)
    return bundle
