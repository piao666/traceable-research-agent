"""Bounded observation-driven executor for optional ReAct runs."""

from __future__ import annotations

import copy
import json
import re
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.agent.file_access_policy import (
    CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS,
    confirmation_details_for_path,
    file_reader_execution_arguments,
    is_path_approved,
    resolve_file_reader_path,
)
from app.agent.executor import (
    _persist_final_report_gate,
    _persist_citation_validation,
    _persist_reference_verification,
    run_plan,
)
from app.agent.report_generation import ReportGenerationAudit, check_report_generation_not_cancelled, resolve_report_llm_client
from app.agent.preflight import enforce_execution_readiness, check_plan_readiness
from app.agent.execution_policy import policy_failure
from app.agent.tool_recovery import (
    observe_result,
    prune_completed_fetch_urls,
    recovery_context,
    unavailable_reason,
)
from app.agent.source_context import build_source_context, prompt_source_context
from app.agent.evidence_requirements import assess_required_evidence
from app.agent.budget import (
    FinalizationRequired,
    budget_client,
    budgeted_execution,
    limits as budget_limits,
)
from app.agent.outcome import enforce_research_outcome, fail_execution, finalize_terminal_decision, load_observations, report_subject
from app.agent.react_prompt import build_react_messages
from app.agent.react_schema import (
    ReActDecision,
    ReActDecisionError,
    ReActStepObservation,
    extract_json_object,
    is_finish_action,
    normalize_action,
    validate_react_decision,
)
from app.agent.reporter import generate_markdown_report, save_report
from app.evidence.citation_validator import validator_version_for
from app.agent.source_intake import execute_governed_operation
from app.config import Settings
from app.evidence.service import materialize_execution_provenance
from app.llm.base import LLMClient
from app.llm.providers import create_llm_client
from app.mcp.policy import requires_interactive_confirmation
from app.research.coverage import assess_comparison_coverage
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool, list_tools
from app.trace import store
from app.trace.logger import record_tool_result, record_trace_event
from app.trace.models import AgentRun


# A rejected/non-executed decision gets one chance to be replaced.  A later
# terminal-evidence rejection must not turn into an unbounded extra decision.
MAX_NON_EXECUTION_REPLACEMENTS = 1
MAX_DYNAMIC_REACT_STEPS = 32
ACADEMIC_DISCOVERY_TOOLS = {
    "arxiv_search",
    "crossref_search",
    "openalex_search",
    "semantic_scholar_search",
}


def _parse_plan(run: AgentRun) -> dict[str, Any]:
    if not run.plan_json:
        raise ValueError("Task run does not have a plan_json.")
    plan = json.loads(run.plan_json)
    if not isinstance(plan, dict):
        raise ValueError("Task run plan must be a JSON object.")
    return plan


def _summary(run: AgentRun, plan: dict[str, Any], message: str | None = None) -> dict:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "current_step": run.current_step,
        "total_steps": run.total_steps,
        "total_tool_calls": run.total_tool_calls,
        "report_url": f"/api/reports/{run.run_id}",
        "trace_url": f"/api/tasks/{run.run_id}/trace",
        "error_message": run.error_message,
        "message": message,
        "execution_mode": plan.get("execution_mode") or "react",
        "planner_source": plan.get("planner_source"),
        "llm_provider": plan.get("react_state", {}).get("llm_provider"),
        "llm_model": plan.get("react_state", {}).get("llm_model"),
    }


def _safe_error(message: str | None) -> str:
    text = str(message or "Unknown ReAct error.")
    blocked = ("authorization", "bearer", "api_key", "apikey", "token")
    if any(term in text.lower() for term in blocked):
        return "ReAct provider failed with a redacted error."
    return text[:500]


def _allowed_tools(run: AgentRun, plan: dict[str, Any]) -> list[str]:
    if run.allowed_tools_json:
        try:
            parsed = json.loads(run.allowed_tools_json)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [str(item) for item in plan.get("allowed_tools") or []]


def _research_scenario(plan: dict[str, Any]) -> str | None:
    marker = str(plan.get("scenario_template") or "").strip().lower()
    if "deep_web_research" in marker:
        return "deep_web_research"
    if "technical_docs_research" in marker:
        return "technical_docs_research"
    return None


def _allowed_remote_tools(allowed_tools: list[str]) -> list[str]:
    names: list[str] = []
    for name in allowed_tools:
        spec = get_tool(name)
        if spec and spec.enabled and (spec.metadata or {}).get("tool_source") == "mcp_remote":
            names.append(name)
    return names


def _remote_mcp_attempted(state: dict[str, Any], remote_tools: list[str]) -> bool:
    remote_set = set(remote_tools)
    for observation in state.get("observation_history") or []:
        if not isinstance(observation, dict):
            continue
        metadata = observation.get("tool_result_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("tool_source") == "mcp_remote":
            return True
        if str(observation.get("action") or "") in remote_set:
            return True
    return False


def _early_finish_rejection_reason(
    plan: dict[str, Any],
    allowed_tools: list[str],
    state: dict[str, Any],
    settings: Settings | None = None,
) -> str | None:
    contract = plan.get("task_contract") or {}
    substantive = str(contract.get("evidence_requirement") or "").casefold() == "substantive"
    context = state.get("source_context") or {}
    sources = context.get("sources") if isinstance(context, dict) else []
    if substantive:
        # Source context is rebuilt from persisted traces. Project its fetched
        # records into the same admission contract used by Quick and Deep so a
        # ReAct finish cannot accept a page shell or unrelated body merely
        # because it has a fetched flag. Finalization repeats the check on the
        # complete persisted provenance bundle.
        from app.agent.evidence_requirements import assess_required_evidence

        from app.agent.source_context import source_url
        from app.evidence.policy import classify_source, current_documentation_channel, load_source_policy

        constraints = contract.get("source_constraints") or {}
        official_only = bool(constraints.get("official_only"))
        current_docs = constraints.get("current_official_documentation") is True
        source_policy = load_source_policy(settings.source_policy_path) if (official_only or current_docs) and settings is not None else None
        documents: list[dict[str, object]] = []
        snapshots: list[dict[str, object]] = []
        passages: list[dict[str, object]] = []
        for index, source in enumerate(sources or [], 1):
            if not isinstance(source, dict) or source.get("fetch_status") != "fetched":
                continue
            source_id = str(source.get("source_id") or index)
            snapshot_id = f"react-{source_id}"
            url = source_url(source.get("url"))
            # The source queue is reconstructed from persisted tool traces.
            # Apply the same configured authority policy as materialization;
            # a model-provided official flag or lookalike hostname is not proof.
            source_class = (
                classify_source("web", url, {}, source_policy)
                if source_policy is not None and url is not None else "unknown"
            )
            final_url = source_url(source.get("final_url"))
            current_verified = False
            if source_policy is not None and source_class == "official" and final_url and url:
                final_current, _ = current_documentation_channel(final_url, source_policy)
                canonical_current, _ = current_documentation_channel(url, source_policy)
                current_verified = final_current and canonical_current
            documents.append({
                "document_id": source_id,
                "title": str(source.get("title") or ""),
                "canonical_uri": url or "",
                "metadata": {"evidence_role": "primary_content", "source_class": source_class},
            })
            snapshots.append({"snapshot_id": snapshot_id, "document_id": source_id,
                              "content_hash": str(source.get("content_hash") or ""),
                              "metadata": {"current_channel_verified": current_verified}})
            passages.append({
                "passage_id": f"react-passage-{source_id}", "snapshot_id": snapshot_id,
                "text": str(source.get("snippet") or ""),
                "content_basis": str(source.get("content_basis") or ""),
                "metadata": {"evidence_role": "primary_content"},
            })
        assessment = assess_required_evidence(contract, {
            "source_documents": documents, "source_snapshots": snapshots, "passages": passages,
        })
        if not assessment.passed:
            return (
                "Substantive research cannot finish until task-relevant, content-bearing evidence is available: "
                + ", ".join(gap.code for gap in assessment.gaps)
                + "."
            )
    has_body = any(
        isinstance(source, dict)
        and source.get("fetch_status") == "fetched"
        and str(source.get("content_basis") or "") in {"full_text", "partial", "table", "structured"}
        for source in (sources or [])
    )
    if substantive and not has_body and "web_fetcher" in allowed_tools:
        pending = next((source for source in (sources or []) if isinstance(source, dict)
                        and source.get("fetch_status") in {"pending", "failed"}), None)
        if pending:
            return (
                "Substantive research cannot finish with discovery-only evidence. "
                "Use web_fetcher with the recorded source_id to read a task-relevant page before finishing."
            )
    scenario = _research_scenario(plan)
    if scenario is None:
        return None
    remote_tools = _allowed_remote_tools(allowed_tools)
    if not remote_tools or _remote_mcp_attempted(state, remote_tools):
        return None
    if scenario == "deep_web_research":
        return (
            "Deep web research requires at least one available remote MCP source-pack "
            "tool call before finish. Try one of: "
            + ", ".join(remote_tools[:5])
        )
    return (
        "Technical docs research requires at least one available remote MCP documentation "
        "or source-pack tool call before finish. Try one of: "
        + ", ".join(remote_tools[:5])
    )


def _initial_state(settings: Settings, provider: str, model: str | None) -> dict[str, Any]:
    return {
        "observation_history": [],
        "tool_call_counts": {},
        "pending_confirmation": None,
        "invalid_decisions": 0,
        "max_steps": settings.react_max_steps,
        "same_tool_max_calls": settings.react_same_tool_max_calls,
        "llm_provider": provider,
        "llm_model": model,
        "fallback_used": False,
        "completed_with_limitation": False,
        "finish_reason": None,
        "replacement_steps_granted": 0,
        "structured_output_retries": 0,
    }


def _react_step_capacity(settings_obj: Settings) -> int:
    """Bound decisions by the same root resources used by the budget ledger."""

    configured = budget_limits(settings_obj)
    llm_capacity = configured["max_llm_calls"] - configured.get("final_report_llm_calls", 0)
    time_capacity = configured["max_seconds"] // 5
    # One finish decision does not consume a tool call, while corrections are
    # separately bounded below. Runtime reservations remain the final authority.
    tool_capacity = configured["max_tool_calls"] + 1
    return max(
        1,
        min(
            MAX_DYNAMIC_REACT_STEPS,
            llm_capacity,
            time_capacity,
            tool_capacity,
        ),
    )


def _react_step_allowance(plan: dict[str, Any], settings_obj: Settings) -> int:
    capacity = _react_step_capacity(settings_obj)
    base = min(settings_obj.react_max_steps, capacity)
    extension = 0
    if settings_obj.deep_research_enabled and _research_scenario(plan) is not None:
        extension = max(4, settings_obj.deep_research_max_depth * settings_obj.deep_research_breadth)
    contract = plan.get("task_contract") or {}
    if contract.get("goal_kind") == "comparison":
        # Product/dimension requirements need room for discovery, fetching and
        # gap repair even when optional multi-round deepening is disabled.
        complexity = max(
            len(contract.get("requirements") or []),
            len(contract.get("entities") or []) + len(contract.get("dimensions") or []) + 2,
        )
        extension = max(extension, min(24, complexity))
    allowance = min(capacity, base + extension)
    # PEAR has a shared Scope budget. The discovery node must not spend the
    # entire allowance before required topic branches can run. Each child is
    # likewise bounded; the atomic budget still decides every real call.
    if plan.get("engine_version") == "v2" and plan.get("research_controller") == "pear":
        return min(allowance, 4 if plan.get("run_role") == "root" else 7)
    return allowance


def _tool_call_limit(plan: dict[str, Any], settings_obj: Settings, name: str) -> int:
    """Return a task-aware per-tool allowance below the root safety ceiling."""

    base = max(1, int(settings_obj.react_same_tool_max_calls))
    if (plan.get("engine_version") == "v2" and plan.get("research_controller") == "pear"
            and plan.get("run_role") == "root"):
        # The root discovers candidates. Mandatory topic children own deep
        # retrieval; repeated root fetches starve their shared token reserve.
        if name == "web_fetcher":
            return 1
        if name == "tavily_search":
            return min(base, 2)
    contract = plan.get("task_contract") or {}
    broad_research = _research_scenario(plan) is not None or contract.get("goal_kind") == "comparison"
    if broad_research and name in {"tavily_search", "mcp_github_search", "web_fetcher", "pdf_reader"}:
        requirements = len(contract.get("requirements") or [])
        adaptive = min(12, max(6, (requirements + 1) // 2))
        # Task adaptation may widen an explicit operator setting, never narrow
        # it. The root atomic tool budget remains the authoritative hard cap.
        return max(base, adaptive)
    return base


def _first_branch_web_discovery_only(
    plan: dict[str, Any], state: dict[str, Any], active_tools: list[str]
) -> bool:
    """Give an external-web child a broad URL discovery before niche indexes.

    Academic indexes can be useful later but their landing pages are often
    metadata rather than task-relevant body evidence. Keep explicit URLs and
    unavailable Tavily out of this route; all later decisions retain the
    original allowed-tool set and governed operation checks.
    """
    if (
        plan.get("engine_version") != "v2"
        or plan.get("research_controller") != "pear"
        or plan.get("run_role") == "root"
        or state.get("observation_history")
        or "tavily_search" not in active_tools
    ):
        return False
    contract = plan.get("task_contract") or {}
    requirements = contract.get("evidence_scope_requirements") or []
    if not requirements or not all(
        isinstance(item, dict) and item.get("source_scope") == "external_web"
        for item in requirements
    ):
        return False
    task = str(contract.get("original_task") or plan.get("task") or "")
    return not bool(re.search(r"https?://\S+", task, re.I))


def _tool_is_relevant(plan: dict[str, Any], name: str) -> bool:
    contract = plan.get("task_contract") or {}
    if contract.get("goal_kind") != "comparison" or name not in ACADEMIC_DISCOVERY_TOOLS:
        return True
    task = str(contract.get("original_task") or "")
    return bool(re.search(r"论文|学术|研究文献|paper|academic|literature", task, re.I))


def _grant_non_execution_replacement(
    db: Session,
    run: AgentRun,
    state: dict[str, Any],
    settings_obj: Settings,
) -> bool:
    """Grant the single bounded replacement for a non-executed decision."""

    granted = int(state.get("replacement_steps_granted") or 0)
    offset = int(state.get("step_offset") or 0)
    capacity_limit = offset + _react_step_capacity(settings_obj)
    if granted >= MAX_NON_EXECUTION_REPLACEMENTS or int(state["step_limit"]) >= capacity_limit:
        return False
    state["replacement_steps_granted"] = granted + 1
    state["step_limit"] = int(state["step_limit"]) + 1
    state["max_steps"] = int(state.get("max_steps") or 0) + 1
    run.total_steps = int(state["step_limit"])
    db.commit()
    return True


def _persist_plan(db: Session, run_id: str, plan: dict[str, Any]) -> None:
    # Source records are rebuilt from authoritative traces before every model
    # decision. Avoid duplicating the full queue in plan_json and every /plan
    # polling response; retain only its compact gap summary and coverage matrix.
    persisted = copy.deepcopy(plan)
    state = persisted.get("react_state")
    if isinstance(state, dict):
        context = state.pop("source_context", None)
        if isinstance(context, dict):
            state["source_context_summary"] = {
                "version": context.get("version"),
                "gaps": dict(context.get("gaps") or {}),
                "omitted_count": int(context.get("omitted_count") or 0),
            }
    store.replace_agent_run_plan(db, run_id, persisted)


def _confirmation_required(plan: dict[str, Any], action: str) -> bool:
    spec = get_tool(action)
    if requires_interactive_confirmation(spec):
        return True
    return any(
        step.get("tool_name") == action and bool(step.get("requires_confirmation"))
        for step in plan.get("steps") or []
    )


def _file_reader_confirmation_details(plan: dict[str, Any], decision: ReActDecision) -> dict[str, Any] | None:
    if decision.action != "file_reader":
        return None
    path = str(decision.args.get("path") or "").strip()
    if not path:
        return None
    details = confirmation_details_for_path(path)
    if details.get("allowed") or not details.get("requires_confirmation"):
        return None
    resolved_path = resolve_file_reader_path(path)
    if is_path_approved(plan, resolved_path):
        return None
    return details


def _is_confirmed(plan: dict[str, Any], step_no: int, action: str) -> bool:
    confirmation = plan.get("confirmation")
    return bool(
        isinstance(confirmation, dict)
        and confirmation.get("approved")
        and confirmation.get("required_step_no") == step_no
        and confirmation.get("required_tool_name") == action
    )


def _observation_summary(action: str, result: ToolResult) -> str:
    if not result.success:
        return _safe_error(result.error_message or result.output_summary or "Tool failed.")
    if action == "mcp_github_search" and result.metadata.get("fallback_used"):
        reason = result.metadata.get("fallback_reason") or "public API unavailable"
        return f"GitHub search used read-only mock fallback: {reason}."[:500]
    return str(result.output_summary or "Tool completed successfully.")[:500]


def _react_metadata(
    decision: ReActDecision,
    observation_summary: str,
    count: int,
    state: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    metadata = {
        "execution_mode": "react",
        "thought": decision.thought,
        "action": decision.action,
        "finish_reason": decision.finish_reason,
        "observation_summary": observation_summary,
        "tool_call_count": count,
        "llm_provider": state.get("llm_provider"),
        "llm_model": state.get("llm_model"),
        "fallback_used": state.get("fallback_used", False),
    }
    metadata.update(extra)
    return metadata


def _append_observation(
    state: dict[str, Any],
    step_no: int,
    decision: ReActDecision,
    summary: str,
    success: bool,
    error_message: str | None,
    metadata: dict[str, Any],
    output: Any | None = None,
    trace_id: str | None = None,
) -> None:
    persisted_output = None
    if isinstance(output, dict) and isinstance(output.get("source_content"), dict):
        source_content = dict(output["source_content"])
        source_content["text"] = str(source_content.get("text") or "")[:8000]
        persisted_output = {"source_content": source_content}
    observation = ReActStepObservation(
        trace_id=trace_id,
        step_no=step_no,
        thought=decision.thought,
        action=decision.action,
        args=decision.args,
        observation_summary=summary,
        success=success,
        error_message=error_message,
        tool_result_metadata=metadata,
        finish_reason=decision.finish_reason,
        # Complete tool output is authoritative in tool_traces. Keeping only a
        # bounded snapshot-read excerpt prevents plan_json from duplicating all
        # search and page bodies on every polling response.
        output=persisted_output,
    )
    state.setdefault("observation_history", []).append(observation.model_dump())


def _prompt_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep provider context concise while the persisted state retains evidence."""

    compact: list[dict[str, Any]] = []
    history_limit = 6 if state.get("llm_context_compacted") else 20
    for observation in list(state.get("observation_history") or [])[-history_limit:]:
        metadata = observation.get("tool_result_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        compact.append(
            {
                "step_no": observation.get("step_no"),
                **({"source_content": observation["output"]["source_content"]}
                   if isinstance(observation.get("output"), dict) and observation["output"].get("source_content") else {}),
                "action": observation.get("action"),
                "observation_summary": str(
                    observation.get("observation_summary") or ""
                )[:500],
                "success": observation.get("success"),
                "error_message": _safe_error(observation.get("error_message"))
                if observation.get("error_message")
                else None,
                "metadata": {
                    key: metadata[key]
                    for key in (
                        "error_type",
                        "fallback_used",
                        "tool_source",
                        "data_source",
                        "blocked_reason",
                        "result_count",
                    )
                    if key in metadata
                },
            }
        )
    return compact


def _complete_report(
    db: Session,
    run_id: str,
    plan: dict[str, Any],
    state: dict[str, Any],
    finish_reason: str,
    settings_obj: Settings,
    llm_client: LLMClient | None = None,
    limitation: bool = False,
) -> dict:
    if store.is_agent_run_cancelled(db, run_id):
        cancelled = store.get_fresh_agent_run(db, run_id)
        return _summary(cancelled, plan, "Run cancelled by user.")
    state["finish_reason"] = finish_reason
    traces = store.list_tool_traces(db, run_id)
    # Preserve dynamically discovered official sources across rebuilds
    discovered_domains = (state.get("source_context") or {}).get("discovered_official_domains")
    discovered_repos = (state.get("source_context") or {}).get("discovered_official_repos")
    state["source_context"] = build_source_context(traces)
    if discovered_domains:
        state["source_context"]["discovered_official_domains"] = discovered_domains
    if discovered_repos:
        state["source_context"]["discovered_official_repos"] = discovered_repos
    state["coverage_matrix"] = assess_comparison_coverage(
        plan.get("task_contract"), state["source_context"], traces
    )
    if (
        not plan.get("defer_to_research_scope")
        and state["coverage_matrix"].get("applicable")
        and not state["coverage_matrix"].get("complete")
    ):
        state["goal_status"] = "not_met"
        gaps = "; ".join(list(state["coverage_matrix"].get("gaps") or [])[:6])
        state["finish_summary"] = (
            "Required comparison coverage is incomplete: " + gaps
        )[:500]
    state["completed_with_limitation"] = limitation
    state["pending_confirmation"] = None
    plan["react_state"] = state
    plan["execution_mode"] = "react"
    _persist_plan(db, run_id, plan)
    context = state.pop("source_context", None)
    if isinstance(context, dict):
        state["source_context_summary"] = {
            "version": context.get("version"),
            "gaps": dict(context.get("gaps") or {}),
            "omitted_count": int(context.get("omitted_count") or 0),
        }
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    traces = store.list_tool_traces(db, run_id)
    observations = load_observations(traces)
    if plan.get("defer_to_research_scope"):
        # Research-tree nodes own their traces and Evidence, while completion
        # and reporting are decided exactly once at Scope level.  Persist the
        # node's provenance here, but do not run the single-Run quality gate or
        # create an intermediate report that excludes sibling evidence.
        materialize_execution_provenance(
            db,
            run,
            plan,
            observations,
            traces,
            settings_obj,
        )
        if run.run_role == "root":
            # Root discovery is a completed ResearchNode, not a completed
            # AgentRun.  The orchestrator owns the root's sole terminal
            # transition after scope evaluation and report persistence.
            run = store.get_fresh_agent_run(db, run_id)
            return _summary(
                run,
                plan,
                "Root discovery completed; Scope finalization is deferred.",
            )
        run = store.update_agent_run_status(db, run_id, "completed", None)
        return _summary(run, plan, "Research node completed; Scope finalization is deferred.")
    if not enforce_research_outcome(db, run, plan, observations, traces, settings_obj):
        return _summary(store.get_fresh_agent_run(db, run_id), plan)
    provenance_bundle = materialize_execution_provenance(
        db,
        run,
        plan,
        observations,
        traces,
        settings_obj,
    )
    evidence_assessment = assess_required_evidence(plan.get("task_contract"), provenance_bundle)
    plan["evidence_assessment"] = evidence_assessment.as_dict()
    if not evidence_assessment.passed:
        outcome = dict(plan.get("research_outcome") or {})
        outcome.update({
            "status": "failed",
            "error_code": "required_evidence_coverage_incomplete",
            "message": "Required task-relevant body evidence is incomplete.",
            "evidence_assessment": evidence_assessment.as_dict(),
        })
        plan["research_outcome"] = outcome
        _persist_plan(db, run_id, plan)
        record_trace_event(
            db, run_id, max((trace.step_no for trace in traces), default=0) + 1,
            "required_evidence_gate", "failed", {}, outcome["message"],
            evidence_assessment.as_dict(), error_message=outcome["message"],
        )
        finalize_terminal_decision(db, run, plan, traces=traces)
        return _summary(store.get_fresh_agent_run(db, run_id), plan, outcome["message"])
    _llm = resolve_report_llm_client(settings_obj, llm_client)
    report_audit = ReportGenerationAudit(db, run_id, traces)
    report_llm_responses = report_audit.responses
    citation_validation_reports: list[Any] = []
    reference_verification_reports: list[Any] = []
    try:
        markdown = generate_markdown_report(
            report_subject(run),
            plan,
            observations,
            traces,
            llm_client=_llm,
            provenance_bundle=provenance_bundle,
            report_type=run.report_type,
            usage_callback=report_audit.usage_callback,
            citation_validation_callback=citation_validation_reports.append,
            reference_verification_callback=reference_verification_reports.append,
            revision_attempt_callback=report_audit.persist_attempt,
            cancellation_check=lambda: check_report_generation_not_cancelled(db, run_id),
        )
    except Exception as exc:
        from app.agent.budget import BudgetExceeded
        if isinstance(exc, BudgetExceeded):
            raise
        failed = fail_execution(db, run_id, exc)
        return _summary(failed, plan, failed.error_message)
    draft_result = plan.get("report_draft_result")
    if report_llm_responses:
        # Every retained draft/revision response consumes budget; summing only
        # the last response under-reported a bounded repair loop.
        state["_llm_token_in"] = int(state.get("_llm_token_in") or 0) + sum(
            int(response.usage.prompt_tokens or 0)
            for response in report_llm_responses if response.usage
        )
        state["_llm_token_out"] = int(state.get("_llm_token_out") or 0) + sum(
            int(response.usage.completion_tokens or 0)
            for response in report_llm_responses if response.usage
        )
        traces = store.list_tool_traces(db, run_id)
    if report_audit.attempts:
        plan["report_revision_attempts"] = report_audit.attempts
    plan["report_generation"] = {
        **report_audit.manifest(),
        "adopted": bool((plan.get("report_draft_result") or {}).get("adopted")),
        "validator_version": validator_version_for(citation_validation_reports[-1] if citation_validation_reports else None),
    }
    if isinstance(draft_result, dict):
        plan["report_draft_result"] = draft_result
    store.replace_agent_run_plan(db, run_id, plan)
    report_path = save_report(run_id, markdown)
    store.update_agent_run_report(db, run_id, report_path)
    _persist_citation_validation(
        db,
        run_id,
        citation_validation_reports,
        traces,
    )
    traces = store.list_tool_traces(db, run_id)
    _persist_reference_verification(
        db,
        run_id,
        reference_verification_reports,
        traces,
    )
    traces = store.list_tool_traces(db, run_id)
    run = store.get_fresh_agent_run(db, run_id)
    _persist_final_report_gate(
        db, run, plan, markdown, provenance_bundle, report_path,
        citation_validation_reports,
    )
    run = store.get_fresh_agent_run(db, run_id)
    if store.is_agent_run_cancelled(db, run_id):
        cancelled = store.get_fresh_agent_run(db, run_id)
        return _summary(cancelled, plan, "Run cancelled by user.")
    plan = json.loads(run.plan_json or "{}")
    finalize_terminal_decision(db, run, plan, traces=traces)
    run = store.get_fresh_agent_run(db, run_id)
    # Terminal decisions bind the saved report bytes.  Incomplete state is
    # surfaced by the persisted integrity fields/UI; never append a banner
    # after the terminal hash has been recorded.

    # ── Phase 6: Summarize LLM token/cost ─────────────────────────────
    token_in = int(state.get("_llm_token_in") or 0)
    token_out = int(state.get("_llm_token_out") or 0)
    if token_in or token_out:
        try:
            from app.llm.cost import estimate_cost_from_tokens

            provider = state.get("llm_provider", "unknown")
            model = state.get("llm_model")
            cost = estimate_cost_from_tokens(provider, model, token_in, token_out)
            store.update_agent_run_cost(db, run_id, token_in=token_in, token_out=token_out, estimated_cost=cost)
        except Exception:
            pass

    message = "ReAct run completed with limitation." if limitation else "ReAct run completed."
    return _summary(run, plan, message)


def _fallback_to_plan(
    db: Session,
    run_id: str,
    plan: dict[str, Any],
    state: dict[str, Any],
    step_no: int,
    reason: str,
    settings_obj: Settings,
    error_type: str = "invalid_decision",
) -> dict:
    if plan.get("defer_to_research_scope"):
        # Deep Research V2 has one dynamic node executor. A model failure is a
        # truthful node limitation, not permission to switch to the separate
        # planned runtime and produce an isolated report.
        return _complete_report(
            db,
            run_id,
            plan,
            state,
            reason,
            settings_obj,
            limitation=True,
        )
    state["fallback_used"] = True
    state["finish_reason"] = "react_fallback_to_planned"
    plan.setdefault("requested_execution_mode", "react")
    plan["execution_mode"] = "planned"
    if plan.get("adaptive_upgrade"):
        plan["adaptive_upgrade_failed"] = True
    plan["react_state"] = state
    plan.setdefault("notes", []).append(f"ReAct fallback: {reason}")
    _persist_plan(db, run_id, plan)
    record_trace_event(
        db,
        run_id,
        step_no,
        "react_fallback",
        "failed",
        {"action": "fallback_to_planned"},
        reason,
        {
            "metadata": {
                "execution_mode": "react",
                "fallback_used": True,
                "fallback_target": "planned",
                "error_type": error_type,
                "observation_summary": reason,
            }
        },
        error_message=reason,
    )
    return run_plan(db, run_id, settings_obj=settings_obj)


def _finalize_at_research_boundary(
    db: Session,
    run_id: str,
    plan: dict[str, Any],
    state: dict[str, Any],
    step_no: int,
    settings_obj: Settings,
    llm_client: LLMClient | None,
) -> dict:
    """Hand a root Run from discovery to its protected report budget."""

    coverage = state.get("coverage_matrix") or {}
    incomplete = bool(coverage.get("applicable") and not coverage.get("complete"))
    if incomplete:
        gaps = "; ".join(list(coverage.get("gaps") or [])[:6])
        summary = f"Research budget reached the report boundary with uncovered requirements: {gaps}"
        state["goal_status"] = "not_met"
    else:
        summary = "Research budget reached the report boundary; finalizing from verified evidence."
    state["finish_reason"] = "finalization_reserve_handoff"
    state["finish_summary"] = summary[:500]
    state["budget_handoff"] = "final_report"
    plan["react_state"] = state
    record_trace_event(
        db,
        run_id,
        max(0, step_no - 1),
        "research_finalization_handoff",
        "warning" if incomplete else "success",
        {"action": "finalize", "reason": "finalization_reserve"},
        summary,
        {
            "metadata": {
                "execution_mode": "react",
                "stop_reason_persisted": False,
                "coverage_complete": not incomplete,
                "uncovered_requirements": list(coverage.get("gaps") or [])[:12],
            }
        },
    )
    _persist_plan(db, run_id, plan)
    return _complete_report(
        db,
        run_id,
        plan,
        state,
        "finalization_reserve_handoff",
        settings_obj,
        llm_client,
        limitation=True,
    )


@budgeted_execution
def run_react_task(
    db: Session,
    run_id: str,
    settings: Settings,
    llm_client: LLMClient | None = None,
) -> dict:
    """Execute a run with bounded Thought/Action/Observation decisions."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    plan = _parse_plan(run)
    if run.status == "completed":
        return _summary(run, plan, "Run already completed; no tools executed.")
    if run.status in ("failed", "cancelled"):
        return _summary(run, plan, f"Run is {run.status} and cannot be executed.")
    if run.status in {"waiting_human", "waiting_human_plan"}:
        return _summary(run, plan, "Run is waiting for human confirmation.")
    if not enforce_execution_readiness(db, run_id, plan, settings,
                                      llm_available=bool(llm_client and llm_client.is_available())):
        return _summary(store.get_fresh_agent_run(db, run_id), plan)

    allowed_tools = _allowed_tools(run, plan)
    available_specs = [spec for spec in list_tools() if spec.enabled]
    available_names = [spec.name for spec in available_specs]
    client = budget_client(llm_client or create_llm_client(
        settings,
        settings.react_llm_provider,
        settings.react_llm_model,
    ))
    description = client.describe()
    provider = str(description.get("provider") or settings.react_llm_provider)
    model = description.get("model") or settings.react_llm_model
    state = plan.get("react_state")
    new_state = not isinstance(state, dict)
    if new_state:
        state = _initial_state(settings, provider, model)
    # Static plan progress is not the number of dynamic ReAct decisions already
    # consumed. Persist the offset so human resume cannot reset the allowance.
    state.setdefault("step_offset", run.current_step if plan.get("adaptive_upgrade") else 0)
    if new_state:
        state["max_steps"] = _react_step_allowance(plan, settings)
    else:
        state.setdefault("max_steps", _react_step_allowance(plan, settings))
    state.setdefault("step_limit", int(state["step_offset"]) + int(state["max_steps"]))
    state.setdefault("replacement_steps_granted", 0)
    state["llm_provider"] = provider
    state["llm_model"] = model
    tool_limits = {name: _tool_call_limit(plan, settings, name) for name in allowed_tools}
    state["tool_call_limits"] = tool_limits
    irrelevant_tools = [name for name in allowed_tools if not _tool_is_relevant(plan, name)]
    for name in irrelevant_tools:
        state.setdefault("tool_recovery", {})[name] = {
            "status": "disabled",
            "reason": "task_irrelevant",
            "attempts": int(state.get("tool_call_counts", {}).get(name, 0)),
        }
    plan.setdefault("requested_execution_mode", "react")
    plan["execution_mode"] = "react"
    plan["react_state"] = state
    run.total_steps = state["step_limit"]
    db.commit()
    _persist_plan(db, run_id, plan)
    run = store.mark_agent_run_running_unless_cancelled(db, run_id)
    if run.status == "cancelled":
        return _summary(run, plan, "Run was cancelled before execution started.")

    pending = state.get("pending_confirmation")
    pending_decision: ReActDecision | None = None
    pending_step_no: int | None = None
    if isinstance(pending, dict):
        pending_step_no = int(pending.get("step_no") or run.current_step + 1)
        try:
            pending_decision = ReActDecision.model_validate(pending.get("decision") or {})
        except Exception:
            pending_decision = None

    start_step = pending_step_no or max(run.current_step + 1, 1)
    hard_step_limit = int(state["step_offset"]) + _react_step_capacity(settings)
    for step_no in range(start_step, hard_step_limit + 1):
        if step_no > int(state["step_limit"]):
            break
        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _summary(cancelled, plan, "Run cancelled by user.")
        current_traces = store.list_tool_traces(db, run_id)
        discovered_domains = (state.get("source_context") or {}).get("discovered_official_domains")
        discovered_repos = (state.get("source_context") or {}).get("discovered_official_repos")
        state["source_context"] = build_source_context(current_traces)
        if discovered_domains:
            state["source_context"]["discovered_official_domains"] = discovered_domains
        if discovered_repos:
            state["source_context"]["discovered_official_repos"] = discovered_repos
        state["coverage_matrix"] = assess_comparison_coverage(
            plan.get("task_contract"), state["source_context"], current_traces
        )
        _persist_plan(db, run_id, plan)
        active_tools = []
        for name in allowed_tools:
            if unavailable_reason(state, name, tool_limits[name]):
                continue
            # This is capability admission for a prospective ReAct action,
            # not final-plan validation: its runtime arguments do not exist
            # until the model chooses the action.  Keep the tool in
            # required_tools so permissions/configuration are checked, while
            # the governed-operation boundary validates the concrete args
            # immediately before dispatch.
            readiness = check_plan_readiness({**plan, "steps": [], "required_tools": [name]},
                                            settings, llm_available=client.is_available())
            if readiness["ready"]:
                active_tools.append(name)
            else:
                state.setdefault("tool_recovery", {})[name] = {"status": "disabled", "reason": "capability_unavailable"}
                record_trace_event(db, run_id, step_no, "tool_recovery", "warning", {"tool_name": name},
                    "Optional tool unavailable; continue with other permitted capabilities.",
                    {"tool_name": name, "reason": "capability_unavailable", "executed": False,
                     "blockers": readiness["blockers"]})
        if _first_branch_web_discovery_only(plan, state, active_tools):
            active_tools = ["tavily_search"]
        # Even when tool slots are exhausted the model may assess already-read
        # evidence and explicitly finish. No tool permission is restored here.
        if pending_decision is not None and step_no == pending_step_no:
            decision = pending_decision
            pending_decision = None
        else:
            if not client.is_available():
                reason = _safe_error(description.get("reason") or "ReAct LLM is unavailable.")
                if settings.react_fallback_to_planned and not state.get("observation_history"):
                    return _fallback_to_plan(
                        db, run_id, plan, state, step_no, reason, settings, "llm_unavailable"
                    )
                return _complete_report(db, run_id, plan, state, reason, settings, client, limitation=True)
            messages = build_react_messages(
                run.task,
                run_id,
                active_tools,
                available_specs,
                _prompt_history(state),
                str(plan.get("scenario_template") or "standard"),
                recovery_context(state, allowed_tools, tool_limits, run.source_mode),
                {
                    **prompt_source_context(state["source_context"]),
                    "coverage_matrix": state["coverage_matrix"],
                    "research_goal": plan.get("research_goal"),
                },
                plan.get("task_contract"),
            )
            try:
                response = client.structured_complete(messages, temperature=0.0, max_tokens=3000)
            except FinalizationRequired:
                return _finalize_at_research_boundary(
                    db, run_id, plan, state, step_no, settings, client
                )

            # ── Phase 6: Accumulate LLM token usage ────────────────────
            if response.success and response.usage:
                state.setdefault("_llm_token_in", 0)
                state["_llm_token_in"] += response.usage.prompt_tokens
                state.setdefault("_llm_token_out", 0)
                state["_llm_token_out"] += response.usage.completion_tokens

            raw = extract_json_object(response.content or "") if response.success else None
            llm_error_type = (
                str(response.metadata.get("error_type") or "provider_unavailable")
                if not response.success
                else None
            )
            candidate = normalize_action(str((raw or {}).get("action", "")))
            if candidate in allowed_tools and candidate not in active_tools:
                reason = f"Tool '{candidate}' is unavailable for this run; choose another permitted tool."
                record_trace_event(db, run_id, step_no, "react_decision", "rejected", {"action": candidate},
                    reason, {"metadata": {"error_type": "tool_unavailable", "executed": False}}, error_message=reason)
                state.setdefault("observation_history", []).append({"step_no": step_no, "action": candidate,
                    "success": False, "observation_summary": reason, "error_message": reason})
                _grant_non_execution_replacement(db, run, state, settings)
                plan["react_state"] = state
                _persist_plan(db, run_id, plan)
                store.update_agent_run_progress(db, run_id, step_no)
                continue
            try:
                if raw is None:
                    raise ReActDecisionError(
                        _safe_error(response.error_message or "LLM output was not valid JSON."),
                        llm_error_type or "structured_output_invalid",
                    )
                decision = validate_react_decision(raw, active_tools, available_names)
            except ReActDecisionError as exc:
                reason = _safe_error(str(exc))
                state["invalid_decisions"] = int(state.get("invalid_decisions") or 0) + 1
                invalid_count = int(state.get("invalid_decisions") or 0)
                plan["react_state"] = state
                _persist_plan(db, run_id, plan)
                record_trace_event(
                    db,
                    run_id,
                    step_no,
                    "react_decision",
                    "failed",
                    {"action": "invalid_decision"},
                    reason,
                    {
                        "metadata": {
                            "execution_mode": "react",
                            "error_type": exc.error_type,
                            "observation_summary": reason,
                            "fallback_used": settings.react_fallback_to_planned,
                            "llm_provider": provider,
                            "llm_model": model,
                            "finish_reason": response.metadata.get("finish_reason") if response else None,
                            "prompt_tokens": response.usage.prompt_tokens if response and response.usage else 0,
                            "completion_tokens": response.usage.completion_tokens if response and response.usage else 0,
                        }
                    },
                    error_message=reason,
                    token_in=response.usage.prompt_tokens if response and response.usage else 0,
                    token_out=response.usage.completion_tokens if response and response.usage else 0,
                    phase="react_decision",
                    attempt=invalid_count,
                )
                terminal_provider_errors = {
                    "auth_error",
                    "permission_error",
                    "invalid_request",
                    "model_not_found",
                    "budget_exhausted",
                    "cancelled",
                }
                if exc.error_type == "context_overflow":
                    # Persist this choice so resumed execution also uses the
                    # reduced observation window for its bounded retry.
                    state["llm_context_compacted"] = True
                if exc.error_type == "structured_output_truncated" and not state.get("llm_context_compacted"):
                    # Retry once with compact prompt history. The failed
                    # provider response is already persisted in Trace.
                    state["llm_context_compacted"] = True
                    state["structured_output_retries"] = int(state.get("structured_output_retries") or 0) + 1
                    state.setdefault("observation_history", []).append({
                        "step_no": step_no,
                        "action": "invalid_decision",
                        "thought": "(truncated response)",
                        "observation_summary": "Structured ReAct JSON was truncated; retrying with compact context.",
                        "success": False,
                        "error_message": reason,
                        "tool_result_metadata": {"error_type": "structured_output_truncated"},
                    })
                    plan["react_state"] = state
                    _persist_plan(db, run_id, plan)
                    continue
                if exc.error_type in terminal_provider_errors:
                    # Credentials and provider admission cannot be recovered
                    # by executing a different planned path.  The decision
                    # trace is retained, but a later evidence shortfall must
                    # not downgrade this hard provider failure to retryable
                    # ``incomplete``.
                    if exc.error_type in {
                        "auth_error", "permission_error", "invalid_request", "model_not_found",
                    }:
                        outcome = dict(plan.get("research_outcome") or {})
                        outcome.update({
                            "status": "failed",
                            "error_code": "provider_failure",
                            "message": (
                                f"Provider decision failed with {exc.error_type}. "
                                "Inspect the persisted Trace and correct provider configuration before retrying."
                            ),
                        })
                        plan["research_outcome"] = outcome
                        plan["react_state"] = state
                        _persist_plan(db, run_id, plan)
                        finalize_terminal_decision(db, run, plan, force_failure="provider_failure")
                        return _summary(store.get_fresh_agent_run(db, run_id), plan, outcome["message"])
                    if settings.react_fallback_to_planned and not any(
                        item.get("success") for item in state.get("observation_history") or []
                    ):
                        return _fallback_to_plan(
                            db, run_id, plan, state, step_no, reason, settings, exc.error_type
                        )
                    return _complete_report(
                        db, run_id, plan, state, reason, settings, client, limitation=True
                    )
                # Give LLM one self-correction chance: append the rejection as an observation
                # so it can see why its tool choice was rejected and pick a valid one.
                if invalid_count <= 1:
                    # Inject rejection feedback into observation history
                    state.setdefault("observation_history", []).append({
                        "step_no": step_no,
                        "action": raw.get("action", "unknown") if raw else "invalid",
                        "thought": "(rejected)",
                        "observation_summary": f"Tool rejected: {reason}. Must choose from: {allowed_tools}",
                        "success": False,
                        "error_message": reason,
                        "tool_result_metadata": {"error_type": "disallowed_tool"},
                    })
                    _grant_non_execution_replacement(db, run, state, settings)
                    plan["react_state"] = state
                    _persist_plan(db, run_id, plan)
                    continue  # Let LLM retry with the rejection feedback visible
                # After 2 invalid decisions, fall back
                if settings.react_fallback_to_planned and not state.get("observation_history", [{}])[0].get("success"):
                    return _fallback_to_plan(db, run_id, plan, state, step_no, reason, settings)
                if settings.react_finish_on_invalid_decision:
                    return _complete_report(db, run_id, plan, state, reason, settings, client, limitation=True)
                continue

        if is_finish_action(decision.action):
            rejection_reason = _early_finish_rejection_reason(plan, allowed_tools, state, settings)
            if rejection_reason:
                state["invalid_decisions"] = int(state.get("invalid_decisions") or 0) + 1
                metadata = _react_metadata(
                    decision,
                    rejection_reason,
                    0,
                    state,
                    error_type="early_finish_without_remote_mcp",
                    fallback_used=settings.react_fallback_to_planned,
                )
                record_trace_event(
                    db,
                    run_id,
                    step_no,
                    "react_decision",
                    "failed",
                    {"action": "finish", "args": decision.args},
                    rejection_reason,
                    {"metadata": metadata},
                    error_message=rejection_reason,
                )
                _append_observation(
                    state,
                    step_no,
                    decision,
                    rejection_reason,
                    False,
                    rejection_reason,
                    metadata,
                )
                _grant_non_execution_replacement(db, run, state, settings)
                plan["react_state"] = state
                _persist_plan(db, run_id, plan)
                if int(state.get("invalid_decisions") or 0) >= 2 and settings.react_fallback_to_planned:
                    return _fallback_to_plan(
                        db,
                        run_id,
                        plan,
                        state,
                        step_no,
                        rejection_reason,
                        settings,
                        "early_finish_without_remote_mcp",
                    )
                continue
            summary = str(decision.args.get("summary") or decision.finish_reason or "Task complete.")[:500]
            from app.agent.research_goal import finish_failure
            state["finish_summary"] = summary
            state["goal_status"] = decision.args.get("goal_status")
            unmet = finish_failure(decision.finish_reason, summary, state["goal_status"])
            metadata = _react_metadata(decision, summary, 0, state)
            record_trace_event(
                db,
                run_id,
                step_no,
                "finish",
                "failed" if unmet else "success",
                {"action": "finish", "args": decision.args},
                summary,
                {"summary": summary, "metadata": metadata},
            )
            _append_observation(
                state,
                step_no,
                decision,
                summary,
                True,
                None,
                metadata,
                output={"summary": summary},
            )
            store.update_agent_run_progress(db, run_id, step_no)
            limitation = bool(unmet) or "limitation" in str(decision.finish_reason or "").lower()
            return _complete_report(
                db,
                run_id,
                plan,
                state,
                decision.finish_reason or "completed",
                settings,
                client,
                limitation=limitation,
            )

        skipped_fetched_urls: list[str] = []
        if decision.action == "web_fetcher":
            decision.args, skipped_fetched_urls = prune_completed_fetch_urls(state, decision.args)
        counts = state.setdefault("tool_call_counts", {})
        count = int(counts.get(decision.action) or 0) + 1
        blocked = unavailable_reason(state, decision.action, tool_limits[decision.action], decision.args)
        if blocked or decision.action not in active_tools:
            reason = (
                f"Tool '{decision.action}' cannot execute this request ({blocked or 'unavailable'}); choose another input or tool."
            )
            metadata = _react_metadata(
                decision,
                reason,
                count,
                state,
                error_type=blocked or "tool_unavailable",
                executed=False,
            )
            record_trace_event(
                db,
                run_id,
                step_no,
                decision.action,
                "failed",
                {"action": decision.action, "args": decision.args},
                reason,
                {"metadata": metadata},
                error_message=reason,
            )
            _append_observation(state, step_no, decision, reason, False, reason, metadata)
            _grant_non_execution_replacement(db, run, state, settings)
            store.update_agent_run_progress(db, run_id, step_no)
            plan["react_state"] = state
            _persist_plan(db, run_id, plan)
            continue

        file_confirmation_details = _file_reader_confirmation_details(plan, decision)
        if file_confirmation_details is not None:
            reason = (
                f"Waiting for human confirmation before ReAct step {step_no}: "
                f"file_reader path {file_confirmation_details.get('display_path')}"
            )
            metadata = _react_metadata(
                decision,
                reason,
                count,
                state,
                requires_confirmation=True,
                confirmation_reason=CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS,
                confirmation_details=file_confirmation_details,
            )
            state["pending_confirmation"] = {
                "step_no": step_no,
                "decision": decision.model_dump(),
                "confirmation_details": file_confirmation_details,
            }
            plan["react_state"] = state
            _persist_plan(db, run_id, plan)
            record_trace_event(
                db,
                run_id,
                step_no,
                decision.action,
                "waiting_human",
                {"action": decision.action, "args": decision.args},
                reason,
                {"metadata": metadata},
            )
            store.update_agent_run_progress(db, run_id, max(step_no - 1, 0))
            run = store.update_agent_run_status(db, run_id, "waiting_human", reason)
            return _summary(run, plan, reason)

        if _confirmation_required(plan, decision.action) and not _is_confirmed(
            plan, step_no, decision.action
        ):
            reason = f"Waiting for human confirmation before ReAct step {step_no}: {decision.action}"
            metadata = _react_metadata(
                decision,
                reason,
                count,
                state,
                requires_confirmation=True,
            )
            state["pending_confirmation"] = {
                "step_no": step_no,
                "decision": decision.model_dump(),
            }
            plan["react_state"] = state
            _persist_plan(db, run_id, plan)
            record_trace_event(
                db,
                run_id,
                step_no,
                decision.action,
                "waiting_human",
                {"action": decision.action, "args": decision.args},
                reason,
                {"metadata": metadata},
            )
            store.update_agent_run_progress(db, run_id, max(step_no - 1, 0))
            run = store.update_agent_run_status(db, run_id, "waiting_human", reason)
            return _summary(run, plan, reason)

        if decision.action == "report_writer":
            summary = "Report writer action accepted; structured Reporter generated the final report."
            metadata = _react_metadata(decision, summary, count, state)
            record_trace_event(
                db,
                run_id,
                step_no,
                decision.action,
                "success",
                {"action": decision.action, "args": decision.args},
                summary,
                {"handled_by": "app.agent.reporter", "metadata": metadata},
            )
            _append_observation(
                state,
                step_no,
                decision,
                summary,
                True,
                None,
                metadata,
                output={"handled_by": "app.agent.reporter"},
            )
            state["pending_confirmation"] = None
            plan["react_state"] = state
            _persist_plan(db, run_id, plan)
            store.update_agent_run_progress(db, run_id, step_no)
            return _complete_report(db, run_id, plan, state, "report_generated", settings, client)

        started = perf_counter()
        # Dynamic decisions must also respect runtime configuration, not only the initial plan.
        if not enforce_execution_readiness(db, run_id, plan, settings,
                                          llm_available=client.is_available(), decision_tool=decision.action):
            return _summary(store.get_fresh_agent_run(db, run_id), plan)
        result = execute_governed_operation(
            decision.action,
            decision.args,
            plan,
            settings,
            execute_tool,
            execution_arguments=(
                (lambda prepared: file_reader_execution_arguments(prepared, plan))
                if decision.action == "file_reader"
                else None
            ),
        )
        latency_ms = int((perf_counter() - started) * 1000)
        recovered = observe_result(state, decision.action, decision.args, result, tool_limits[decision.action])
        observation_summary = _observation_summary(decision.action, result)
        metadata = _react_metadata(decision, observation_summary, count, state)
        metadata.update(result.metadata)
        if skipped_fetched_urls:
            metadata["skipped_completed_urls"] = skipped_fetched_urls
        trace_result = ToolResult(
            success=result.success,
            output=result.output,
            output_summary=observation_summary,
            error_message=result.error_message,
            metadata=metadata,
        )
        trace = record_tool_result(
            db,
            run_id,
            step_no,
            decision.action,
            {"action": decision.action, "args": decision.args},
            trace_result,
            latency_ms,
        )
        _append_observation(
            state,
            step_no,
            decision,
            observation_summary,
            result.success,
            result.error_message,
            metadata,
            output=result.output,
            trace_id=trace.trace_id,
        )
        if not result.success or recovered.get("status") in {"disabled", "exhausted"}:
            record_trace_event(db, run_id, step_no, "tool_recovery", "warning", {"tool_name": decision.action},
                f"Tool recovery: {decision.action} {recovered.get('reason')}; other permitted tools remain available.",
                {"tool_name": decision.action, **recovered})
        if result.metadata.get("executed") is False:
            _grant_non_execution_replacement(db, run, state, settings)
        plan["react_state"] = state
        _persist_plan(db, run_id, plan)
        store.update_agent_run_progress(
            db,
            run_id,
            step_no,
            total_tool_calls_delta=0 if result.metadata.get("executed") is False else 1,
            latency_ms_delta=latency_ms,
        )

    if _branch_evidence_goal_met_at_limit(db, run_id, plan, settings):
        # A research-tree child is an evidence-acquisition unit, not the
        # report writer. Its objective can be verified deterministically from
        # persisted, task-eligible body passages even when the actor spent its
        # last decision reading a source rather than emitting a prose finish.
        # The root Scope and final citation/report gates remain unchanged.
        state["goal_status"] = "achieved"
        state["finish_summary"] = "Required trace-backed branch evidence was acquired."
        record_trace_event(
            db, run_id, int(state["step_limit"]), "finish", "success",
            {"action": "finish"}, state["finish_summary"],
            {"metadata": {"execution_mode": "react", "finish_reason": "branch_evidence_goal_met"}},
        )
        return _complete_report(db, run_id, plan, state, "branch_evidence_goal_met", settings, client)

    reason = f"react_max_steps reached: limit={state['max_steps']}."
    record_trace_event(
        db,
        run_id,
        int(state["step_limit"]),
        "finish",
        "failed",
        {"action": "finish"},
        reason,
        {
            "metadata": {
                "execution_mode": "react",
                "finish_reason": "max_steps_reached",
                "observation_summary": reason,
                "completed_with_limitation": True,
            }
        },
    )
    return _complete_report(db, run_id, plan, state, "max_steps_reached", settings, client, limitation=True)


def _branch_evidence_goal_met_at_limit(db: Session, run_id: str, plan: dict[str, Any], settings: Settings) -> bool:
    if not plan.get("defer_to_research_scope") or plan.get("run_role") == "root":
        return False
    from app.agent.evidence_requirements import assess_required_evidence

    contract = plan.get("task_contract") or {}
    if contract.get("evidence_requirement") != "substantive":
        return False
    run = store.get_fresh_agent_run(db, run_id)
    if run is None:
        return False
    traces = store.list_tool_traces(db, run_id)
    if not any(trace.status == "success" and trace.tool_name in {"web_fetcher", "pdf_reader", "file_reader"} for trace in traces):
        return False
    provenance = materialize_execution_provenance(
        db, run, plan, load_observations(traces), traces, settings,
    )
    return bool(provenance and assess_required_evidence(contract, provenance).passed)
