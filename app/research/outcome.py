"""Scope-level research completion gate for Deep Research Engine V2."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.outcome import SCOPE_INTEGRITY_VERSION, load_observations
from app.agent.research_goal import finish_failure, structured_goal_failure
from app.research.models import ResearchScope
from app.research.scope import list_scope_nodes, list_scope_runs, list_scope_traces


SCOPE_OUTCOME_VERSION = SCOPE_INTEGRITY_VERSION


def assess_scope_outcome(
    db: Session,
    scope: ResearchScope,
    scope_evidence: dict[str, Any],
    contract: dict[str, Any] | None,
    *,
    finalization_limited: bool = False,
    orchestration_incomplete: bool = False,
) -> dict[str, Any]:
    """Conservatively decide whether one whole Research Scope may finalize."""

    contract = contract or {}
    nodes = list_scope_nodes(db, scope.scope_id)
    passages_by_run: dict[str, int] = {}
    for passage in scope_evidence.get("passages") or []:
        run_id = str(passage.get("origin_run_id") or "")
        passages_by_run[run_id] = passages_by_run.get(run_id, 0) + 1

    errors: list[str] = []
    warnings: list[str] = []
    if not scope_evidence.get("passages"):
        errors.append("no_usable_evidence")
    if orchestration_incomplete:
        errors.append("research_orchestration_incomplete")
    integrity = scope_evidence.get("integrity") or {}
    if not integrity.get("all_traceability_resolves", True):
        errors.append("evidence_trace_incomplete")
    required_nodes = [node for node in nodes if _node_required(node.metadata_json)]
    run_by_id = {run.run_id: run for run in list_scope_runs(db, scope.scope_id)}
    if any(node.status == "failed" for node in required_nodes):
        errors.append("required_research_branch_failed")
    if any(
        node.status == "completed" and node.run_id and passages_by_run.get(node.run_id, 0) == 0
        for node in required_nodes
    ):
        errors.append("required_research_branch_has_no_evidence")
    if any(node.status in {"pending", "running"} for node in required_nodes):
        errors.append("required_research_branch_incomplete")
    for node in required_nodes:
        run = run_by_id.get(node.run_id or "")
        plan = _json_object(run.plan_json if run else None)
        state = plan.get("react_state") or {}
        if finish_failure(
            state.get("finish_reason"),
            state.get("finish_summary", ""),
            state.get("goal_status"),
        ):
            errors.append("required_research_branch_goal_not_met")
            break
    if contract.get("unresolved_fields"):
        errors.append("task_requirements_unresolved")

    traces = list_scope_traces(db, scope.scope_id)
    structured_failure = structured_goal_failure(contract, load_observations(traces))
    if structured_failure:
        errors.append(structured_failure)
    required_source_classes = [
        str(value) for value in (contract.get("required_source_classes") or []) if value
    ]
    if required_source_classes:
        present = {
            str((item.get("metadata") or {}).get("source_class") or "")
            for item in scope_evidence.get("source_documents") or []
        }
        missing = [value for value in required_source_classes if value not in present]
        if missing:
            errors.append("required_source_class_missing")
            warnings.append("Missing required source classes: " + ", ".join(missing))
    if finalization_limited:
        warnings.append(
            "Research stopped at the protected report boundary; the final report must state its limitations."
        )
    errors = list(dict.fromkeys(errors))
    return {
        "version": SCOPE_OUTCOME_VERSION,
        "status": "failed" if errors else "passed",
        "error_code": errors[0] if errors else None,
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
        "effective_evidence_count": len(scope_evidence.get("passages") or []),
        "run_count": len(scope_evidence.get("runs") or []),
        "node_count": len(nodes),
        "message": (
            "Research Scope passed the evidence and lineage gate."
            if not errors
            else "Research Scope is incomplete: " + ", ".join(errors) + "."
        ),
    }


def _node_required(metadata_json: str) -> bool:
    metadata = _json_object(metadata_json)
    return bool(metadata.get("required", True))


def _json_object(value: str | None) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
