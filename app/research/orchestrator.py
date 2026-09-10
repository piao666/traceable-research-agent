"""Deep Research Engine V2 Research Scope orchestrator."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.agent.budget import BudgetExceeded, budget_client, budgeted_execution, current_budget
from app.agent.executor import (
    _after_run_completed,
    _persist_citation_validation,
    _persist_reference_verification,
)
from app.agent.outcome import fail_execution, load_observations, report_subject
from app.agent.react_executor import _summary, run_react_task
from app.agent.report_generation import record_report_synthesis_trace, resolve_report_llm_client
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings, settings as _settings
from app.evidence.scope_service import get_scope_provenance_bundle
from app.llm.base import LLMClient
from app.llm.providers import create_llm_client
from app.research.branch_planner import plan_research_branches
from app.research.models import ResearchNode
from app.research.node_executor import ResearchNodeExecutor
from app.research.outcome import assess_scope_outcome
from app.research.scope import (
    create_research_node,
    create_research_scope,
    list_scope_nodes,
    list_scope_traces,
    resolve_research_scope,
    scope_summary,
    update_scope_status,
)
from app.trace import store
from app.trace.logger import record_trace_event


BranchPlanner = Callable[..., dict[str, Any]]
ReportGenerator = Callable[..., str]


@budgeted_execution
def run_deep_research_v2(
    db: Session,
    run_id: str,
    settings_obj: Settings = _settings,
    llm_client: LLMClient | None = None,
    *,
    branch_planner: BranchPlanner = plan_research_branches,
    node_executor: ResearchNodeExecutor | None = None,
    report_generator: ReportGenerator = generate_markdown_report,
) -> dict[str, Any]:
    """Execute Deep Profile as one persisted scope and topic tree."""

    root = store.get_agent_run(db, run_id)
    if root is None:
        raise ValueError("Task run not found")
    plan = _json_object(root.plan_json)
    if root.status in {"failed", "cancelled", "waiting_human", "waiting_human_plan"}:
        return _summary(root, plan)
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
            "deepening_pending": False,
            "deepening_phase": "deprecated",
        }
    )
    store.replace_agent_run_plan(db, run_id, plan)
    try:
        root_result = run_react_task(db, run_id, settings_obj, llm_client)
    except BudgetExceeded:
        root_node.status = "failed"
        db.commit()
        update_scope_status(db, scope.scope_id, "failed")
        raise
    root = store.get_fresh_agent_run(db, run_id)
    if root is None:
        raise ValueError("Task run not found")
    root_node.status = root.status
    db.commit()
    if root.status in {"failed", "cancelled", "waiting_human", "waiting_human_plan"}:
        update_scope_status(db, scope.scope_id, root.status)
        return root_result

    # The node pass is complete, but the public root stays running until the
    # scope outcome and single final report are persisted.
    root = store.mark_agent_run_running_unless_cancelled(db, run_id)
    if root.status == "cancelled":
        update_scope_status(db, scope.scope_id, "cancelled")
        return _summary(root, plan)
    root_node.status = "completed"
    db.commit()

    client = budget_client(
        llm_client
        or create_llm_client(
            settings_obj,
            settings_obj.react_llm_provider,
            settings_obj.react_llm_model,
        )
    )
    executor = node_executor or ResearchNodeExecutor()
    frontier: deque[ResearchNode] = deque([root_node])
    prior_queries = [root.task]
    created_run_ids: list[str] = []
    finalization_limited = False
    orchestration_incomplete = False
    root_state = (_json_object(root.plan_json).get("react_state") or {})
    if root_state.get("finish_reason") == "finalization_reserve_handoff":
        finalization_limited = True

    while frontier:
        if finalization_limited:
            break
        parent_node = frontier.popleft()
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
            branch_plan = branch_planner(
                client,
                task=parent_node.query,
                observations=load_observations(parent_traces),
                prior_queries=prior_queries,
                breadth=settings_obj.deep_research_breadth,
                depth=parent_node.depth + 1,
                contract=plan.get("task_contract"),
            )
        except BudgetExceeded:
            update_scope_status(db, scope.scope_id, "failed")
            raise
        if branch_plan.get("finalization_limited"):
            finalization_limited = True
            break
        if branch_plan.get("planner_failed"):
            orchestration_incomplete = True
            record_trace_event(
                db,
                run_id,
                0,
                "research_branch_planner",
                "failed",
                {"parent_node_id": parent_node.node_id},
                "Research branch planning failed; completeness was not established.",
                {"parent_node_id": parent_node.node_id, "depth": parent_node.depth + 1},
                error_message="Research branch planning failed.",
            )
            break
        branches = list(branch_plan.get("branches") or [])
        if not branches and not branch_plan.get("is_comprehensive"):
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
            continue
        for branch in branches:
            runtime = current_budget()
            if runtime is not None:
                try:
                    if not runtime.can_deepen():
                        finalization_limited = True
                        break
                except BudgetExceeded:
                    update_scope_status(db, scope.scope_id, "failed")
                    raise
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
                metadata={"required": branch.get("required", True)},
            )
            prior_queries.append(node.query)
            try:
                result = executor.execute(db, scope, node, settings_obj, client)
            except BudgetExceeded:
                update_scope_status(db, scope.scope_id, "failed")
                raise
            created_run_ids.append(result["run_id"])
            fresh_root = store.get_fresh_agent_run(db, run_id)
            linked_plan = _json_object(fresh_root.plan_json if fresh_root else None)
            linked_plan["deepening_sub_run_ids"] = list(
                dict.fromkeys([*(linked_plan.get("deepening_sub_run_ids") or []), result["run_id"]])
            )
            store.replace_agent_run_plan(db, run_id, linked_plan)
            if result.get("status") == "completed":
                frontier.append(node)
            elif branch.get("required", True):
                orchestration_incomplete = True
            child_run = store.get_fresh_agent_run(db, result["run_id"])
            child_state = (_json_object(child_run.plan_json if child_run else None).get("react_state") or {})
            if child_state.get("finish_reason") == "finalization_reserve_handoff":
                finalization_limited = True
                break
        if orchestration_incomplete:
            break
        if finalization_limited:
            break

    scope_evidence = get_scope_provenance_bundle(db, scope)
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
    if outcome["status"] != "passed":
        update_scope_status(db, scope.scope_id, "failed")
        return _summary(
            store.update_agent_run_status(db, run_id, "failed", outcome["message"]),
            plan,
            outcome["message"],
        )

    traces = list_scope_traces(db, scope.scope_id)
    observations = load_observations(traces)
    report_client = resolve_report_llm_client(settings_obj, client)
    report_responses: list[Any] = []
    citation_reports: list[Any] = []
    reference_reports: list[Any] = []
    try:
        markdown = report_generator(
            report_subject(root),
            plan,
            observations,
            traces,
            llm_client=report_client,
            provenance_bundle=scope_evidence,
            report_type=root.report_type,
            usage_callback=report_responses.append,
            citation_validation_callback=citation_reports.append,
            reference_verification_callback=reference_reports.append,
        )
    except BudgetExceeded:
        update_scope_status(db, scope.scope_id, "failed")
        raise
    except Exception as exc:
        update_scope_status(db, scope.scope_id, "failed")
        return _summary(fail_execution(db, run_id, exc), plan)
    if report_responses:
        record_report_synthesis_trace(db, run_id, traces, report_responses[-1], success=True)
    report_path = save_report(run_id, markdown)
    store.update_agent_run_report(db, run_id, report_path)
    root_traces = store.list_tool_traces(db, run_id)
    _persist_citation_validation(db, run_id, citation_reports, root_traces)
    _persist_reference_verification(db, run_id, reference_reports, root_traces)
    _after_run_completed(db, root, markdown, step_no=max((t.step_no for t in root_traces), default=0) + 1)
    update_scope_status(db, scope.scope_id, "completed")
    root = store.update_agent_run_status(db, run_id, "completed", None)
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
