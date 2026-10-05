"""Deep Research Engine V2 Research Scope orchestrator."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.agent.budget import BudgetExceeded, FinalizationRequired, budget_client, budgeted_execution, current_budget
from app.agent.executor import (
    _after_run_completed,
    _persist_citation_validation,
    _persist_reference_verification,
)
from app.agent.outcome import fail_execution, finalize_terminal_decision, load_observations, report_subject
from app.agent.react_executor import _summary, run_react_task
from app.agent.report_generation import ReportGenerationAudit, check_report_generation_not_cancelled, resolve_report_llm_client
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings, settings as _settings
from app.evidence.citation_validator import (
    extract_final_answer_section,
    materialize_final_report_occurrences,
    validate_scope_citations,
    validator_version_for,
)
from app.evidence.scope_service import get_scope_provenance_bundle
from app.evidence.reference_verifier import (
    ReferenceVerificationReport,
    ReferenceVerifier,
    extract_cited_academic_references,
    render_reference_verification_section,
)
from app.llm.base import LLMClient
from app.llm.providers import create_llm_client
from app.research.branch_planner import plan_research_branches
from app.research.assessor import (
    assess_requirements,
    persist_plan_contract,
    persist_shadow_assessment,
)
from app.research.models import ResearchNode
from app.research.node_executor import ResearchNodeExecutor
from app.research.branch_executor import SerialPearExecutor
from app.research.outcome import assess_scope_outcome, can_write_partial_report
from app.research.scope import (
    create_research_node,
    create_research_scope,
    list_scope_nodes,
    list_scope_traces,
    resolve_research_scope,
    scope_summary,
    update_node_metadata,
    update_scope_status,
)
from app.reporting.integrity import (
    REPORT_INTEGRITY_VERSION,
    ReportIntegrityResult,
    append_report_integrity_warnings,
    assess_report_integrity,
)
from app.reporting.claim_occurrence import (
    claim_span_for_citation_detail,
    normalize_claim_text,
    segment_final_answer_claims,
)
from app.trace import store
from app.trace.logger import record_phase_event, record_trace_event


BranchPlanner = Callable[..., dict[str, Any]]
ReportGenerator = Callable[..., str]


def _branch_has_budget(runtime: Any, settings_obj: Settings) -> bool:
    """Reserve an entire bounded PEAR child before admitting it.

    Existing usage provides a conservative, run-local prompt-size estimate.
    The atomic budget remains authoritative for every actual provider call.
    """

    # react_executor caps PEAR children at seven decisions. One extra call
    # covers a final handoff without reserving the unbounded generic ReAct
    # allowance, which previously prevented every planned child from starting.
    calls = 8
    snapshot = runtime.snapshot()
    average_tokens = max(
        1024,
        (int(snapshot["accounted_tokens"]) + max(1, int(snapshot["llm_calls"])) - 1)
        // max(1, int(snapshot["llm_calls"])),
    )
    return runtime.can_deepen(
        required_llm_calls=calls,
        required_tokens=calls * average_tokens,
    )


def _explicit_scope_covered(db: Session, scope: Any, plan: dict[str, Any]) -> bool:
    """Stop optional research only after every required node has eligible body evidence.

    This is a scheduling decision, not a substitute for the later Scope,
    citation, or report-integrity gates.
    """
    contract = plan.get("task_contract") or {}
    if not (contract.get("evidence_scope_requirements") or contract.get("requirements")
            or contract.get("evidence_requirement") == "substantive"):
        return False
    required_nodes = [
        node for node in list_scope_nodes(db, scope.scope_id)
        if _json_object(node.metadata_json).get("required", True)
    ]
    if not required_nodes or any(node.status != "completed" for node in required_nodes):
        return False
    from app.agent.evidence_requirements import assess_required_evidence

    bundle = get_scope_provenance_bundle(db, scope)
    assessment = assess_required_evidence(contract, bundle)
    if not assessment.passed:
        return False
    eligible = set(assessment.eligible_passage_ids)
    runs_with_body = {
        str(passage.get("origin_run_id") or "")
        for passage in bundle.get("passages") or []
        if passage.get("passage_id") in eligible
    }
    return all(node.run_id in runs_with_body for node in required_nodes)


def _run_pear_react_adapter(
    db: Session,
    run_id: str,
    settings_obj: Settings,
    actor_client: LLMClient | None,
) -> dict[str, Any]:
    """Bridge the legacy ReAct loop to the PEAR node result contract."""

    result = run_react_task(db, run_id, settings_obj, actor_client)
    return {
        **(result if isinstance(result, dict) else {}),
        "run_id": run_id,
        "status": str((result or {}).get("status") or "failed"),
    }


@budgeted_execution
def run_deep_research_v2(
    db: Session,
    run_id: str,
    settings_obj: Settings = _settings,
    actor_client: LLMClient | None = None,
    *,
    report_llm_client: LLMClient | None = None,
    branch_planner: BranchPlanner = plan_research_branches,
    node_executor: ResearchNodeExecutor | None = None,
    report_generator: ReportGenerator = generate_markdown_report,
) -> dict[str, Any]:
    """Execute Deep Profile as one persisted scope and topic tree."""

    root = store.get_agent_run(db, run_id)
    if root is None:
        raise ValueError("Task run not found")
    plan = _json_object(root.plan_json)
    # A completed V2 run is terminal.  Replaying the orchestrator must be a
    # read-only summary operation: no planner, node runner, report generator,
    # or new audit rows may be created on a second invocation.
    if root.status == "completed" and plan.get("execution_mode") == "deep_research_v2":
        return _summary(root, plan)
    if root.status in {"failed", "cancelled", "waiting_human", "waiting_human_plan"}:
        return _summary(root, plan)
    record_phase_event(db, run_id, "orchestration", "started")
    plan_revision = persist_plan_contract(
        db,
        root_run_id=run_id,
        contract=plan.get("task_contract"),
    )
    plan["plan_revision_id"] = plan_revision.revision_id
    plan["plan_revision_number"] = plan_revision.revision_number
    store.replace_agent_run_plan(db, run_id, plan)
    scope = resolve_research_scope(db, run_id) or create_research_scope(
        db,
        run_id,
        plan.get("task_contract"),
        engine_version=settings_obj.deep_research_engine_version,
    )
    nodes = list_scope_nodes(db, scope.scope_id)
    root_node = next((node for node in nodes if node.parent_node_id is None), None)
    if root_node is None:
        root_node = create_research_node(
            db,
            scope.scope_id,
            parent_node_id=None,
            run_id=run_id,
            node_type="discovery",
            topic=root.task,
            query=root.task,
            research_goal=root.task,
            depth=0,
            priority=0,
            status="running",
            metadata={"required": True},
        )

    plan.update(
        {
            "version": "deep-research-engine-v2",
            "engine_version": scope.engine_version,
            "research_scope_id": scope.scope_id,
            "research_node_id": root_node.node_id,
            "root_run_id": run_id,
            "run_role": "root",
            "execution_mode": "react",
            "requested_execution_mode": "react",
            "defer_to_research_scope": True,
            "research_controller": "pear",
            "deepening_pending": False,
            "deepening_phase": "deprecated",
        }
    )
    store.replace_agent_run_plan(db, run_id, plan)
    actor_client = budget_client(
        actor_client
        or create_llm_client(
            settings_obj,
            settings_obj.react_llm_provider,
            settings_obj.react_llm_model,
        )
    )
    if root_node.status == "completed":
        root_result = _summary(
            store.get_fresh_agent_run(db, run_id),
            plan,
            "Root discovery already completed; resuming Scope orchestration.",
        )
    else:
        try:
            record_phase_event(db, run_id, "root_discovery", "started")
            root_result = _run_pear_react_adapter(
                db, run_id, settings_obj, actor_client
            )
            record_phase_event(db, run_id, "root_discovery", "success")
        except BudgetExceeded:
            root_node.status = "failed"
            db.commit()
            update_scope_status(db, scope.scope_id, "failed")
            record_phase_event(db, run_id, "root_discovery", "failed", error_message="Research root discovery exceeded its budget.")
            raise
        except Exception as exc:
            record_phase_event(db, run_id, "root_discovery", "failed", details={"error_type": type(exc).__name__}, error_message="Research root discovery failed.")
            raise
    root = store.get_fresh_agent_run(db, run_id)
    if root is None:
        raise ValueError("Task run not found")
    if root.status in {"failed", "cancelled", "waiting_human", "waiting_human_plan"}:
        root_node.status = root.status
        db.commit()
        update_scope_status(db, scope.scope_id, root.status)
        return root_result

    # The node pass is complete, but the public root stays running until the
    # scope outcome and single final report are persisted.
    root_node.status = "completed"
    db.commit()

    executor = node_executor or ResearchNodeExecutor()
    serial_executor = SerialPearExecutor(executor)
    nodes = list_scope_nodes(db, scope.scope_id)
    prior_queries = [node.query for node in nodes]
    created_run_ids = [
        node.run_id for node in nodes if node.run_id and node.run_id != run_id
    ]
    finalization_limited = False
    orchestration_incomplete = False
    root_state = (_json_object(root.plan_json).get("react_state") or {})
    if root_state.get("finish_reason") == "finalization_reserve_handoff":
        finalization_limited = True

    # Resume persisted child work before planning any new branches.  The node
    # executor is idempotent for an existing run_id; keeping recovery here
    # closes the orchestration loop instead of merely making the executor
    # recoverable in isolation.
    if not finalization_limited:
        for node in serial_executor.order(nodes):
            if node.parent_node_id is None or node.status not in {"pending", "running"}:
                continue
            if (
                node.status == "pending"
                and not _json_object(node.metadata_json).get("required", True)
                and _explicit_scope_covered(db, scope, plan)
            ):
                update_node_metadata(db, node, deferred_reason="explicit_scope_covered")
                continue
            if store.is_agent_run_cancelled(db, run_id):
                update_scope_status(db, scope.scope_id, "cancelled")
                cancelled = store.get_fresh_agent_run(db, run_id)
                return _summary(cancelled, _json_object(cancelled.plan_json if cancelled else None))
            runtime = current_budget()
            if runtime is not None:
                try:
                    if not _branch_has_budget(runtime, settings_obj):
                        finalization_limited = True
                        break
                except BudgetExceeded:
                    update_scope_status(db, scope.scope_id, "failed")
                    raise
            try:
                result = serial_executor.execute_one(
                    db, scope, node, settings_obj, actor_client
                )
            except BudgetExceeded:
                update_scope_status(db, scope.scope_id, "failed")
                raise
            child_run_id = str(result["run_id"])
            created_run_ids.append(child_run_id)
            _link_deepening_run(db, run_id, child_run_id)
            if result.get("status") != "completed" and _json_object(
                node.metadata_json
            ).get("required", True):
                orchestration_incomplete = True
                break
            child_run = store.get_fresh_agent_run(db, child_run_id)
            child_state = (
                _json_object(child_run.plan_json if child_run else None).get("react_state")
                or {}
            )
            if child_state.get("finish_reason") == "finalization_reserve_handoff":
                finalization_limited = True
                break

    nodes = list_scope_nodes(db, scope.scope_id)
    frontier: deque[ResearchNode] = deque(
        node
        for node in nodes
        if node.status == "completed"
        and _json_object(node.metadata_json).get("branch_planning_status", "pending")
        == "pending"
    )

    while frontier:
        if finalization_limited:
            break
        # An explicit multi-topic contract can be satisfied by the first
        # complete frontier. Do not recursively generate more required work
        # after every topic already has trace-backed body evidence. The
        # report/citation gates still decide whether those bodies support the
        # final claims; this only bounds redundant research branching.
        if frontier[0].depth > 0 and _explicit_scope_covered(db, scope, plan):
            for node in frontier:
                update_node_metadata(db, node, branch_planning_status="completed")
            record_phase_event(
                db, run_id, "branch_planning", "success",
                details={"reason": "explicit_scope_covered", "branch_count": 0},
            )
            break
        parent_node = frontier.popleft()
        planning_status = _json_object(parent_node.metadata_json).get(
            "branch_planning_status", "pending"
        )
        if planning_status == "completed":
            continue
        if planning_status == "failed":
            orchestration_incomplete = True
            break
        if store.is_agent_run_cancelled(db, run_id):
            update_scope_status(db, scope.scope_id, "cancelled")
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _summary(cancelled, _json_object(cancelled.plan_json if cancelled else None))
        runtime = current_budget()
        if runtime is not None:
            try:
                if not runtime.can_deepen():
                    finalization_limited = True
                    break
            except BudgetExceeded:
                update_scope_status(db, scope.scope_id, "failed")
                raise
        parent_traces = store.list_tool_traces(db, parent_node.run_id or run_id)
        try:
            planning_trace = record_phase_event(
                db, run_id, "branch_planning", "started",
                details={"parent_node_id": parent_node.node_id, "depth": parent_node.depth + 1},
            )
            branch_plan = branch_planner(
                actor_client,
                task=parent_node.query,
                observations=load_observations(parent_traces),
                prior_queries=prior_queries,
                breadth=settings_obj.deep_research_breadth,
                depth=parent_node.depth + 1,
                contract=plan.get("task_contract"),
            )
        except FinalizationRequired:
            finalization_limited = True
            record_phase_event(
                db, run_id, "branch_planning", "warning",
                parent_trace_id=planning_trace.trace_id,
                error_message="Research reached the protected final-report budget.",
            )
            break
        except BudgetExceeded:
            update_scope_status(db, scope.scope_id, "failed")
            record_phase_event(
                db, run_id, "branch_planning", "failed",
                parent_trace_id=locals().get("planning_trace").trace_id if locals().get("planning_trace") else None,
                error_message="Research branch planning exceeded its budget.",
            )
            raise
        except Exception as exc:
            parent_node.status = "failed"
            db.commit()
            update_node_metadata(
                db,
                parent_node,
                branch_planning_status="failed",
                branch_planning_error=type(exc).__name__,
            )
            orchestration_incomplete = True
            record_phase_event(
                db,
                run_id,
                "branch_planning",
                "failed",
                parent_trace_id=locals().get("planning_trace").trace_id if locals().get("planning_trace") else None,
                details={"parent_node_id": parent_node.node_id, "error_type": type(exc).__name__},
                error_message="Research branch planning failed.",
            )
            record_trace_event(
                db,
                run_id,
                0,
                "research_branch_planner",
                "failed",
                {"parent_node_id": parent_node.node_id},
                "Research branch planning failed.",
                {"error_type": type(exc).__name__, "parent_node_id": parent_node.node_id},
                error_message="Research branch planning failed.",
                phase="branch_planning",
                parent_trace_id=locals().get("planning_trace").trace_id if locals().get("planning_trace") else None,
            )
            break
        if branch_plan.get("finalization_limited"):
            finalization_limited = True
            break
        if branch_plan.get("planner_failed"):
            parent_node.status = "failed"
            db.commit()
            update_node_metadata(db, parent_node, branch_planning_status="failed")
            orchestration_incomplete = True
            planner_error = str(
                branch_plan.get("error_message") or "Research branch planning failed."
            )
            planner_diagnostics = {
                key: branch_plan.get(key)
                for key in (
                    "parent_node_id",
                    "error_type",
                    "provider",
                    "model",
                    "finish_reason",
                    "prompt_tokens",
                    "completion_tokens",
                    "content_length",
                )
                if branch_plan.get(key) is not None
            }
            planner_diagnostics["parent_node_id"] = parent_node.node_id
            planner_diagnostics["depth"] = parent_node.depth + 1
            record_trace_event(
                db,
                run_id,
                0,
                "research_branch_planner",
                "failed",
                {"parent_node_id": parent_node.node_id},
                planner_error,
                planner_diagnostics,
                error_message=planner_error,
                token_in=int(branch_plan.get("prompt_tokens") or 0),
                token_out=int(branch_plan.get("completion_tokens") or 0),
                phase="branch_planning",
                parent_trace_id=planning_trace.trace_id,
            )
            break
        branches = list(branch_plan.get("branches") or [])
        if not branches and not branch_plan.get("is_comprehensive"):
            parent_node.status = "failed"
            db.commit()
            update_node_metadata(db, parent_node, branch_planning_status="failed")
            orchestration_incomplete = True
            record_trace_event(
                db,
                run_id,
                0,
                "research_branch_planner",
                "failed",
                {"parent_node_id": parent_node.node_id},
                "Research branch planner did not establish completeness.",
                {"parent_node_id": parent_node.node_id, "depth": parent_node.depth + 1},
                error_message="Research completeness was not established.",
                phase="branch_planning",
                parent_trace_id=planning_trace.trace_id,
            )
            break
        if parent_node.depth >= settings_obj.deep_research_max_depth:
            if branches:
                orchestration_incomplete = True
                record_trace_event(
                    db,
                    run_id,
                    0,
                    "research_depth_boundary",
                    "failed",
                    {"parent_node_id": parent_node.node_id},
                    "Further required branches remain at the configured safety depth.",
                    {"remaining_branch_count": len(branches)},
                    error_message="Research depth boundary reached before completeness.",
                )
                break
            update_node_metadata(db, parent_node, branch_planning_status="completed")
            record_phase_event(db, run_id, "branch_planning", "success", parent_trace_id=planning_trace.trace_id, details={"branch_count": 0, "is_comprehensive": True})
            continue
        scheduled_nodes = []
        for branch in branches:
            node = create_research_node(
                db,
                scope.scope_id,
                parent_node_id=parent_node.node_id,
                run_id=None,
                node_type=branch["node_type"],
                topic=branch["topic"],
                query=branch["query"],
                research_goal=branch["research_goal"],
                depth=parent_node.depth + 1,
                priority=branch["priority"],
                metadata={
                    "required": branch.get("required", True),
                    **({"assigned_requirement_ids": branch["assigned_requirement_ids"]}
                       if "assigned_requirement_ids" in branch else {}),
                },
            )
            prior_queries.append(node.query)
            scheduled_nodes.append((branch, node))
        # Persist the whole frontier before spending budget. An unexecuted
        # required branch remains pending and therefore cannot be silently
        # treated as a comprehensive, passed Scope.
        update_node_metadata(db, parent_node, branch_planning_status="completed")
        deferred_optional = 0
        # Required obligations must be admitted before optional exploration,
        # independently of the model's JSON array order.
        scheduled_nodes.sort(key=lambda item: (
            not item[0].get("required", True), item[1].priority,
        ))
        for branch, node in scheduled_nodes:
            if not branch.get("required", True) and _explicit_scope_covered(db, scope, plan):
                # Keep the planned node pending for audit; a deferred optional
                # branch must never be rewritten as successfully researched.
                update_node_metadata(db, node, deferred_reason="explicit_scope_covered")
                deferred_optional += 1
                continue
            runtime = current_budget()
            if runtime is not None:
                try:
                    if not _branch_has_budget(runtime, settings_obj):
                        finalization_limited = True
                        break
                except BudgetExceeded:
                    update_scope_status(db, scope.scope_id, "failed")
                    raise
            try:
                result = serial_executor.execute_one(
                    db, scope, node, settings_obj, actor_client
                )
            except BudgetExceeded:
                update_scope_status(db, scope.scope_id, "failed")
                raise
            created_run_ids.append(result["run_id"])
            _link_deepening_run(db, run_id, result["run_id"])
            if result.get("status") == "completed":
                frontier.append(node)
            elif branch.get("required", True):
                orchestration_incomplete = True
            child_run = store.get_fresh_agent_run(db, result["run_id"])
            child_state = (_json_object(child_run.plan_json if child_run else None).get("react_state") or {})
            if child_state.get("finish_reason") == "finalization_reserve_handoff":
                finalization_limited = True
                break
        record_phase_event(db, run_id, "branch_planning", "success", parent_trace_id=planning_trace.trace_id, details={"branch_count": len(branches), "deferred_optional_count": deferred_optional})
        if orchestration_incomplete:
            break
        if finalization_limited:
            break

    nodes = list_scope_nodes(db, scope.scope_id)
    waiting_nodes = [
        node for node in nodes
        if node.status in {"waiting_human", "waiting_human_plan"}
    ]
    if waiting_nodes:
        waiting_status = "waiting_human_plan" if any(
            node.status == "waiting_human_plan" for node in waiting_nodes
        ) else "waiting_human"
        update_scope_status(db, scope.scope_id, waiting_status)
        waiting_root = store.update_agent_run_status(
            db,
            run_id,
            waiting_status,
            "Research is waiting for confirmation before continuing.",
        )
        return _summary(
            waiting_root,
            _json_object(waiting_root.plan_json if waiting_root else None),
            "Research is waiting for confirmation before continuing.",
        )

    scope_evidence = get_scope_provenance_bundle(db, scope)
    from app.evidence.scope_reasoning import materialize_scope_reasoning
    from app.agent.source_context import build_source_context
    from app.research.coverage import assess_comparison_coverage

    materialize_scope_reasoning(db, scope.scope_id, settings_obj.source_policy_path)
    scope_evidence = get_scope_provenance_bundle(db, scope)
    scope_traces = list_scope_traces(db, scope.scope_id)
    shadow_assessment = assess_requirements(
        plan.get("task_contract"),
        build_source_context(scope_traces),
        traces=scope_traces,
        scope_evidence=scope_evidence,
    )
    shadow_snapshot = persist_shadow_assessment(
        db,
        root_run_id=run_id,
        scope_id=scope.scope_id,
        plan_revision_id=plan_revision.revision_id,
        result=shadow_assessment,
    )
    plan["requirement_assessment_shadow"] = {
        "snapshot_id": shadow_snapshot.snapshot_id,
        "assessor_version": shadow_assessment["assessor_version"],
        "status": shadow_assessment["status"],
        "complete": shadow_assessment["complete"],
    }
    store.replace_agent_run_plan(db, run_id, plan)
    scope_evidence["coverage_matrix"] = assess_comparison_coverage(
        plan.get("task_contract"),
        build_source_context(scope_traces),
        scope_traces,
    )
    plan["coverage_matrix"] = scope_evidence["coverage_matrix"]
    store.replace_agent_run_plan(db, run_id, plan)
    outcome = assess_scope_outcome(
        db,
        scope,
        scope_evidence,
        plan.get("task_contract"),
        finalization_limited=finalization_limited,
        orchestration_incomplete=orchestration_incomplete,
    )
    root = store.get_fresh_agent_run(db, run_id)
    plan = _json_object(root.plan_json if root else None)
    plan.update(
        {
            "execution_mode": "deep_research_v2",
            "defer_to_research_scope": False,
            "research_scope_id": scope.scope_id,
            "research_scope": scope_summary(db, scope),
            "research_outcome": outcome,
            "deepening_pending": False,
            "deepening_phase": "deprecated",
            "deepening_sub_run_ids": list(dict.fromkeys(created_run_ids)),
        }
    )
    store.replace_agent_run_plan(db, run_id, plan)
    traces = list_scope_traces(db, scope.scope_id)
    record_trace_event(
        db,
        run_id,
        max((trace.step_no for trace in traces if trace.run_id == run_id), default=0) + 1,
        "research_scope_quality_gate",
        "success" if outcome["status"] == "passed" else "failed",
        {"scope_id": scope.scope_id},
        outcome["message"],
        outcome,
        error_message=outcome["message"] if outcome["status"] == "failed" else None,
    )
    partial_report = can_write_partial_report(outcome)
    if partial_report:
        outcome["warnings"] = list(outcome.get("warnings") or []) + [
            "部分报告：下列研究要求尚未完成，不得用已取得材料推断缺失结论："
            + ", ".join(outcome.get("errors") or []),
        ]
        plan["research_outcome"] = outcome
        store.replace_agent_run_plan(db, run_id, plan)
    if outcome["status"] != "passed" and not partial_report:
        update_scope_status(db, scope.scope_id, "failed")
        failed_root = store.get_fresh_agent_run(db, run_id)
        finalize_terminal_decision(
            db, failed_root, plan,
            traces=list_scope_traces(db, scope.scope_id),
            scope_outcome=outcome,
            # An empty, non-comprehensive research frontier has an auditable
            # diagnosis but is a recoverable evidence gap, not an execution
            # crash. Keep the scope outcome/errors while allowing the shared
            # terminal arbiter to classify this path as incomplete.
            force_failure=(
                None
                if outcome.get("error_code") == "no_usable_evidence"
                else outcome.get("error_code") or "scope_outcome_failed"
            ),
        )
        failed_root = store.get_fresh_agent_run(db, run_id)
        return _summary(failed_root, _json_object(failed_root.plan_json), outcome["message"])

    traces = list_scope_traces(db, scope.scope_id)
    observations = load_observations(traces)
    report_client = resolve_report_llm_client(settings_obj, report_llm_client)
    report_audit = ReportGenerationAudit(db, run_id, traces)
    report_responses = report_audit.responses
    citation_reports: list[Any] = []
    reference_reports: list[Any] = []
    occurrence_preview: dict[str, list[dict[str, Any]]] | None = None
    audit_report_path: str | None = None
    try:
        report_phase_trace = record_phase_event(db, run_id, "report_generation", "started")
        markdown = report_generator(
            report_subject(root),
            plan,
            observations,
            traces,
            llm_client=report_client,
            provenance_bundle=scope_evidence,
            report_type=root.report_type,
            usage_callback=report_audit.usage_callback,
            citation_validation_callback=citation_reports.append,
            reference_verification_callback=reference_reports.append,
            revision_attempt_callback=report_audit.persist_attempt,
            cancellation_check=lambda: check_report_generation_not_cancelled(db, run_id),
        )
    except BudgetExceeded:
        update_scope_status(db, scope.scope_id, "failed")
        record_phase_event(db, run_id, "report_generation", "failed", error_message="Report generation exceeded its budget.")
        raise
    except Exception as exc:
        update_scope_status(db, scope.scope_id, "failed")
        record_phase_event(db, run_id, "report_generation", "failed", parent_trace_id=locals().get("report_phase_trace").trace_id if locals().get("report_phase_trace") else None, details={"error_type": type(exc).__name__}, error_message="Report generation failed.")
        return _summary(fail_execution(db, run_id, exc), plan)
    record_phase_event(db, run_id, "report_generation", "success", parent_trace_id=report_phase_trace.trace_id)
    expected_report_path = f"workspace/reports/{run_id}.md"
    try:
        citation_validation = (
            citation_reports[-1]
            if citation_reports
            else validate_scope_citations(
                extract_final_answer_section(markdown),
                scope_evidence,
                min_supported_overlap=0.15,
                min_weak_overlap=0.05,
            )
        )
        citation_labels = {
            str(detail.citation_label or "")
            for detail in citation_validation.details
            if detail.citation_label
        }
        cited_academic_references = extract_cited_academic_references(
            scope_evidence,
            citation_labels,
        )
        reference_report = _verify_reference_report(
            cited_academic_references,
            settings_obj,
        )
        reference_reports = [reference_report]
        occurrence_preview = _validation_occurrence_preview(
            citation_validation,
            extract_final_answer_section(markdown),
            scope_evidence,
        )
        report_integrity = assess_report_integrity(
            occurrence_preview,
            reference_report=reference_report,
            enforce_reference_consistency=_requires_strict_reference_gate(plan),
            scope_bundle=scope_evidence,
        )
        repair_attempted = False
        deterministic_fallback_used = False
        if report_integrity.status == "failed" and report_integrity.error_code in {
            "no_final_claim_occurrences",
            "unsupported_citation_rate_exceeded",
            "citation_support_rate_below_threshold",
        }:
            repair_attempted = True
            fallback_markdown = generate_markdown_report(
                report_subject(root),
                plan,
                observations,
                traces,
                llm_client=None,
                provenance_bundle=scope_evidence,
                report_type=root.report_type,
            )
            fallback_final_answer = extract_final_answer_section(fallback_markdown)
            fallback_validation = validate_scope_citations(
                fallback_final_answer,
                scope_evidence,
                min_supported_overlap=0.15,
                min_weak_overlap=0.05,
            )
            fallback_preview = _validation_occurrence_preview(
                fallback_validation,
                fallback_final_answer,
                scope_evidence,
            )
            fallback_integrity = assess_report_integrity(
                fallback_preview,
                reference_report=reference_report,
                enforce_reference_consistency=_requires_strict_reference_gate(plan),
                scope_bundle=scope_evidence,
            )
            if fallback_integrity.status == "passed":
                markdown = fallback_markdown
                citation_validation = fallback_validation
                occurrence_preview = fallback_preview
                # The fallback may change citation labels. Recompute academic
                # reference verification from the adopted final answer rather
                # than carrying the candidate report's reference set forward.
                fallback_labels = {
                    str(detail.citation_label or "")
                    for detail in fallback_validation.details
                    if detail.citation_label
                }
                fallback_references = extract_cited_academic_references(
                    scope_evidence,
                    fallback_labels,
                )
                reference_report = _verify_reference_report(
                    fallback_references,
                    settings_obj,
                )
                fallback_integrity = assess_report_integrity(
                    fallback_preview,
                    reference_report=reference_report,
                    enforce_reference_consistency=_requires_strict_reference_gate(plan),
                    scope_bundle=scope_evidence,
                )
                report_integrity = fallback_integrity
                reference_reports = [reference_report]
                deterministic_fallback_used = True
        plan.setdefault("report_diagnostics", {})
        if report_audit.attempts:
            plan["report_revision_attempts"] = report_audit.attempts
        plan["report_generation"] = {
            **report_audit.manifest(),
            "adopted": bool((plan.get("report_draft_result") or {}).get("adopted")),
            "validator_version": validator_version_for(citation_validation),
        }
        plan["report_diagnostics"].update(
            {
                "repair_attempted": repair_attempted,
                "deterministic_fallback_used": deterministic_fallback_used,
            }
        )
        store.replace_agent_run_plan(db, run_id, plan)
        reference_lines = render_reference_verification_section(reference_report)
        if reference_lines:
            markdown = "\n".join(
                [markdown.rstrip(), "", *reference_lines]
            ).rstrip() + "\n"
        markdown = append_report_integrity_warnings(markdown, report_integrity)
        materialize_final_report_occurrences(
            db,
            root_run_id=run_id,
            scope_id=scope.scope_id,
            markdown=markdown,
            provenance_bundle=scope_evidence,
            report_path=expected_report_path,
            validation_report=citation_validation,
            occurrence_preview=occurrence_preview,
        )
    except Exception:
        # A validator failure is a quality failure, not a reason to discard
        # the generated bytes.  Retain the draft immediately as a local audit
        # artifact before continuing through the failed terminal path.
        audit_report_path = save_report(run_id, markdown)
        store.update_agent_run_report(db, run_id, audit_report_path)
        citation_validation = None
        report_integrity = ReportIntegrityResult(
            version=REPORT_INTEGRITY_VERSION,
            status="failed",
            error_code="citation_validation_failed",
            warnings=["Final citation occurrence validation could not be completed."],
            claim_total=0,
            claim_with_citation=0,
            claim_without_citation=0,
            claim_citation_coverage_rate=0.0,
            occurrence_total=0,
            supported=0,
            weakly_supported=0,
            unsupported=0,
            support_rate=0.0,
            strict_support_rate=0.0,
        )

    root_traces = store.list_tool_traces(db, run_id)
    _persist_citation_validation(
        db,
        run_id,
        [citation_validation] if citation_validation is not None else [],
        root_traces,
    )
    if citation_validation is None:
        store.update_agent_run_citation_validation(
            db,
            run_id,
            total=0,
            supported=0,
            weakly_supported=0,
            unsupported=0,
            accuracy=0.0,
        )
    root_traces = store.list_tool_traces(db, run_id)
    _persist_reference_verification(db, run_id, reference_reports, root_traces)
    plan = _persist_report_integrity(db, run_id, report_integrity)
    report_path = audit_report_path or save_report(run_id, markdown)
    root = store.update_agent_run_report(db, run_id, report_path)
    # The bytes saved for download are the only revision permitted to inform a
    # terminal decision.  Re-materialize after all controlled warnings/indexes
    # are rendered; never mutate the file after this identity is recorded.
    revision_bundle = None
    if citation_validation is not None:
        revision_bundle = materialize_final_report_occurrences(
            db, root_run_id=run_id, scope_id=scope.scope_id, markdown=markdown,
            provenance_bundle=scope_evidence, report_path=report_path,
            validation_report=citation_validation, occurrence_preview=occurrence_preview,
        )
    plan = _json_object((store.get_fresh_agent_run(db, run_id) or root).plan_json)
    report_generation = dict(plan.get("report_generation") or {})
    if revision_bundle is not None:
        revision = revision_bundle["report_revision"]
        # Preserve the pre-adoption audit manifest as a value.  Referencing
        # ``report_generation`` itself and then updating that dictionary would
        # create a recursive plan object that SQLite JSON persistence rejects.
        attempt_manifest = dict(report_generation)
        validation_payload = (
            citation_validation.to_dict()
            if hasattr(citation_validation, "to_dict") else {}
        )
        writing_manifest_hash = (
            report_generation.get("manifest_sha256")
            or report_generation.get("manifest_hash")
        )
        evidence_snapshot_id = __import__("hashlib").sha256(
            json.dumps(scope_evidence, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        validator_version = validator_version_for(citation_validation)
        manifest_payload = {
            "version": "report-generation-v1",
            "report_revision_id": revision["report_revision_id"],
            "content_hash": revision["content_hash"],
            "final_answer_hash": revision["final_answer_hash"],
            "integrity": report_integrity.to_plan_dict(),
            "validation": validation_payload,
            "attempt_manifest": attempt_manifest,
        }
        manifest_sha256 = __import__("hashlib").sha256(
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        report_generation.update({
            **manifest_payload,
            "manifest_sha256": manifest_sha256,
            "report_revision_id": revision["report_revision_id"],
            "content_hash": revision["content_hash"],
            "report_sha256": revision["content_hash"],
            "evidence_snapshot_id": evidence_snapshot_id,
            "writing_manifest_hash": writing_manifest_hash,
            "validator_version": validator_version,
            "validation_identity": __import__("hashlib").sha256(
                json.dumps({"content_hash": revision["content_hash"], "evidence_snapshot_id": evidence_snapshot_id,
                            "writing_manifest_hash": writing_manifest_hash, "validator_version": validator_version,
                            "validation": validation_payload}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            # Persisting a readable audit report is not the same as adopting
            # the writer's final-answer revision for a completed result.
            "adopted": bool(
                deterministic_fallback_used
                or not isinstance(plan.get("report_draft_result"), dict)
                or plan["report_draft_result"].get("adopted")
            ),
        })
    plan["report_generation"] = report_generation
    store.replace_agent_run_plan(db, run_id, plan)
    if report_integrity.status == "failed":
        message = (
            "Report integrity gate failed: "
            f"{report_integrity.error_code}. The report is retained only as an audit artifact."
        )
        failed_root = store.get_fresh_agent_run(db, run_id)
        decision = finalize_terminal_decision(
            db, failed_root, plan,
            traces=store.list_tool_traces(db, run_id),
            report_integrity=report_integrity.to_plan_dict(),
            scope_outcome=outcome,
            # Citation/evidence quality gaps are recoverable and must remain
            # incomplete.  A validator execution failure has no trustworthy
            # quality result and remains a hard failure.
            force_failure=(
                "citation_validation_failed"
                if report_integrity.error_code == "citation_validation_failed"
                else None
            ),
        )
        failed_root = store.get_fresh_agent_run(db, run_id)
        # Failed validation retains the already saved report as a local audit
        # artifact.  A terminal transition must never erase that pointer.
        if failed_root is not None and failed_root.report_path != report_path:
            failed_root = store.update_agent_run_report(db, run_id, report_path)
        decision_status = str(decision.get("status") or "failed")
        update_scope_status(
            db,
            scope.scope_id,
            decision_status if decision_status in {"failed", "incomplete"} else "failed",
        )
        return _summary(failed_root, _json_object(failed_root.plan_json), message)

    root = store.get_fresh_agent_run(db, run_id)
    decision = finalize_terminal_decision(
        db,
        root,
        plan,
        traces=store.list_tool_traces(db, run_id),
        report_integrity=report_integrity.to_plan_dict(),
        scope_outcome=outcome,
    )
    root = store.get_fresh_agent_run(db, run_id)
    if decision.get("status") != "completed":
        decision_status = str(decision.get("status") or "incomplete")
        update_scope_status(
            db,
            scope.scope_id,
            decision_status if decision_status in {"failed", "incomplete"} else "failed",
        )
        return _summary(root, plan, decision.get("error_code"))
    root_traces = store.list_tool_traces(db, run_id)
    _after_run_completed(db, root, markdown, step_no=max((t.step_no for t in root_traces), default=0) + 1)
    update_scope_status(db, scope.scope_id, "completed")
    root = store.get_fresh_agent_run(db, run_id)
    record_phase_event(db, run_id, "orchestration", "success", details={"research_scope_id": scope.scope_id})
    return {
        **_summary(root, plan, "Deep Research Engine V2 completed."),
        "execution_mode": "deep_research_v2",
        "research_scope_id": scope.scope_id,
        "research_node_count": len(list_scope_nodes(db, scope.scope_id)),
    }


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validation_occurrence_preview(
    validation: Any,
    final_answer: str,
    scope_bundle: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build the final-answer-only gate input before persisting its revision."""

    from app.evidence.scope_reasoning import scope_claim_group_key

    report_claims = {
        str(item.get("report_claim_id") or ""): item
        for item in scope_bundle.get("report_claims") or []
    }
    citations_by_label = {
        str(item.get("citation_label") or ""): item
        for item in scope_bundle.get("citations") or []
    }
    groups = list(scope_bundle.get("scope_claim_groups") or [])
    groups_by_claim_id: dict[str, set[str]] = {}
    groups_by_normalized_key: dict[str, set[str]] = {}
    for group in groups:
        group_id = str(group.get("group_id") or "")
        normalized_key = str(group.get("normalized_key") or "")
        if group_id and normalized_key:
            groups_by_normalized_key.setdefault(normalized_key, set()).add(group_id)
        for member in group.get("members") or []:
            claim_id = str(member.get("claim_id") or "")
            if group_id and claim_id:
                groups_by_claim_id.setdefault(claim_id, set()).add(group_id)

    groups_by_claim_text: dict[str, set[str]] = {}
    for claim in scope_bundle.get("claims") or []:
        claim_id = str(claim.get("claim_id") or "")
        normalized_text = normalize_claim_text(str(claim.get("claim_text") or ""))
        if claim_id and normalized_text:
            groups_by_claim_text.setdefault(normalized_text, set()).update(
                groups_by_claim_id.get(claim_id, set())
            )

    claims: list[dict[str, Any]] = []
    spans = segment_final_answer_claims(final_answer)
    details_by_span: dict[tuple[int, int], list[Any]] = {}
    for detail in validation.details:
        span = claim_span_for_citation_detail(spans, detail, final_answer)
        if span is not None:
            details_by_span.setdefault(
                (span.sentence_start, span.sentence_end), []
            ).append(detail)
    for span in spans:
        if not span.is_claim_candidate:
            continue
        details = details_by_span.get((span.sentence_start, span.sentence_end), [])
        group_ids: set[str] = set()
        for label in [str(detail.citation_label or "") for detail in details]:
            citation = citations_by_label.get(label) or {}
            report_claim = report_claims.get(
                str(citation.get("report_claim_id") or "")
            ) or {}
            group_ids.update(
                groups_by_claim_id.get(str(report_claim.get("claim_id") or ""), set())
            )
        mapping_source = "citation_lineage" if group_ids else "none"
        if not group_ids:
            group_ids.update(groups_by_claim_text.get(span.normalized_claim_text, set()))
            if group_ids:
                mapping_source = "claim_member_lineage"
        if not group_ids:
            fallback_key = scope_claim_group_key({"claim_text": span.claim_text})
            group_ids.update(groups_by_normalized_key.get(fallback_key, set()))
            if group_ids:
                mapping_source = "text_fallback"
        claims.append(
            {
                "claim_text": span.claim_text,
                "sentence_start": span.sentence_start,
                "sentence_end": span.sentence_end,
                "normalized_claim_text": span.normalized_claim_text,
                "citation_count": len(details),
                "scope_group_ids": sorted(group_ids),
                "mapping_source": mapping_source,
            }
        )

    citations: list[dict[str, Any]] = []
    for detail in validation.details:
        citations.append(
            {
                "citation_label": detail.citation_label,
                "passage_id": "resolved" if detail.passage_text else None,
                "verdict": detail.verdict,
            }
        )
    return {
        "claim_occurrences": claims,
        "citation_occurrences": citations,
    }


def _link_deepening_run(db: Session, root_run_id: str, child_run_id: str) -> None:
    root = store.get_fresh_agent_run(db, root_run_id)
    plan = _json_object(root.plan_json if root else None)
    plan["deepening_sub_run_ids"] = list(
        dict.fromkeys([*(plan.get("deepening_sub_run_ids") or []), child_run_id])
    )
    store.replace_agent_run_plan(db, root_run_id, plan)


def _persist_report_integrity(
    db: Session,
    run_id: str,
    result: ReportIntegrityResult,
) -> dict[str, Any]:
    """Persist and trace the report gate without overwriting research_outcome."""

    run = store.get_fresh_agent_run(db, run_id)
    plan = _json_object(run.plan_json if run else None)
    plan["report_integrity"] = result.to_plan_dict()
    store.replace_agent_run_plan(db, run_id, plan)
    traces = store.list_tool_traces(db, run_id)
    record_trace_event(
        db,
        run_id,
        max((trace.step_no for trace in traces), default=0) + 1,
        "report_integrity_gate",
        "success" if result.status == "passed" else "failed",
        {"version": result.version},
        (
            f"Report integrity {result.status}: "
            f"{result.supported}/{result.occurrence_total} strictly supported."
        ),
        result.to_plan_dict(),
        error_message=(
            f"Report integrity failed: {result.error_code}."
            if result.status == "failed"
            else None
        ),
    )
    return plan


def _requires_strict_reference_gate(plan: dict[str, Any]) -> bool:
    routing = plan.get("skill_routing") or {}
    selected_skill = str(
        plan.get("skill_name")
        or (routing.get("selected_skill") if isinstance(routing, dict) else "")
        or ""
    )
    return bool(
        selected_skill == "systematic_review"
        or plan.get("retrieval_profile") == "academic_literature"
    )


def _verify_reference_report(
    references: list[dict[str, Any]],
    settings_obj: Settings,
) -> ReferenceVerificationReport:
    """Verify exactly the references present in the adopted report revision."""

    if not settings_obj.reference_verification_enabled or not references:
        return ReferenceVerificationReport()
    return ReferenceVerifier(
        allowed_indexes=[
            item.strip()
            for item in settings_obj.reference_verifier_allowed_indexes.split(",")
            if item.strip()
        ],
        timeout=settings_obj.reference_verifier_timeout_seconds,
        cache_dir=settings_obj.reference_verifier_cache_dir,
        cache_ttl=settings_obj.reference_verifier_cache_ttl_seconds,
    ).verify(references)
