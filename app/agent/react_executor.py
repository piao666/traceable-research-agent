"""Bounded observation-driven executor for optional ReAct runs."""

from __future__ import annotations

import json
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
from app.agent.executor import run_plan
from app.agent.react_prompt import build_react_messages
from app.agent.react_schema import (
    ReActDecision,
    ReActDecisionError,
    ReActStepObservation,
    extract_json_object,
    is_finish_action,
    validate_react_decision,
)
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings
from app.llm.base import LLMClient
from app.llm.providers import create_llm_client
from app.mcp.policy import requires_interactive_confirmation
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool, list_tools
from app.trace import store
from app.trace.logger import record_tool_result, record_trace_event
from app.trace.models import AgentRun


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
) -> str | None:
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
    }


def _persist_plan(db: Session, run_id: str, plan: dict[str, Any]) -> None:
    store.replace_agent_run_plan(db, run_id, plan)


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
    if action == "rag_search" and isinstance(result.output, dict) and not result.output.get("hits"):
        return "RAG search completed with no hits."
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
) -> None:
    observation = ReActStepObservation(
        step_no=step_no,
        thought=decision.thought,
        action=decision.action,
        args=decision.args,
        observation_summary=summary,
        success=success,
        error_message=error_message,
        tool_result_metadata=metadata,
        finish_reason=decision.finish_reason,
        output=output,
    )
    state.setdefault("observation_history", []).append(observation.model_dump())


def _prompt_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep provider context concise while the persisted state retains evidence."""

    compact: list[dict[str, Any]] = []
    for observation in list(state.get("observation_history") or [])[-20:]:
        metadata = observation.get("tool_result_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        compact.append(
            {
                "step_no": observation.get("step_no"),
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
    state["finish_reason"] = finish_reason
    state["completed_with_limitation"] = limitation
    state["pending_confirmation"] = None
    plan["react_state"] = state
    plan["execution_mode"] = "react"
    _persist_plan(db, run_id, plan)
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    observations = list(state.get("observation_history") or [])
    traces = store.list_tool_traces(db, run_id)
    run.status = "completed"
    run.error_message = None
    _llm = llm_client or create_llm_client(settings_obj)  # Phase A: LLM synthesis
    markdown = generate_markdown_report(run, plan, observations, traces, llm_client=_llm)
    report_path = save_report(run_id, markdown)
    store.update_agent_run_report(db, run_id, report_path)
    run = store.update_agent_run_status(db, run_id, "completed", None)
    message = "ReAct run completed with limitation." if limitation else "ReAct run completed."
    return _summary(run, plan, message)


def _fallback_to_plan(
    db: Session,
    run_id: str,
    plan: dict[str, Any],
    state: dict[str, Any],
    step_no: int,
    reason: str,
    error_type: str = "invalid_decision",
) -> dict:
    state["fallback_used"] = True
    state["finish_reason"] = "react_fallback_to_planned"
    plan["requested_execution_mode"] = "react"
    plan["execution_mode"] = "planned"
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
    return run_plan(db, run_id)


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
    if run.status == "failed":
        return _summary(run, plan, "Run is failed and cannot be rerun.")
    if run.status == "waiting_human":
        return _summary(run, plan, "Run is waiting for human confirmation.")

    allowed_tools = _allowed_tools(run, plan)
    available_specs = [spec for spec in list_tools() if spec.enabled]
    available_names = [spec.name for spec in available_specs]
    client = llm_client or create_llm_client(
        settings,
        settings.react_llm_provider,
        settings.react_llm_model,
    )
    description = client.describe()
    provider = str(description.get("provider") or settings.react_llm_provider)
    model = description.get("model") or settings.react_llm_model
    state = plan.get("react_state")
    if not isinstance(state, dict):
        state = _initial_state(settings, provider, model)
    state["llm_provider"] = provider
    state["llm_model"] = model
    plan["requested_execution_mode"] = "react"
    plan["execution_mode"] = "react"
    plan["react_state"] = state
    run.total_steps = settings.react_max_steps
    db.commit()
    _persist_plan(db, run_id, plan)
    run = store.update_agent_run_status(db, run_id, "running", None)

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
    for step_no in range(start_step, settings.react_max_steps + 1):
        if pending_decision is not None and step_no == pending_step_no:
            decision = pending_decision
            pending_decision = None
        else:
            if not client.is_available():
                reason = _safe_error(description.get("reason") or "ReAct LLM is unavailable.")
                if settings.react_fallback_to_planned and not state.get("observation_history"):
                    return _fallback_to_plan(
                        db, run_id, plan, state, step_no, reason, "llm_unavailable"
                    )
                return _complete_report(db, run_id, plan, state, reason, settings, client, limitation=True)
            messages = build_react_messages(
                run.task,
                run_id,
                allowed_tools,
                available_specs,
                _prompt_history(state),
                str(plan.get("scenario_template") or "standard"),
            )
            response = client.complete(messages, temperature=0.0, max_tokens=800)
            raw = extract_json_object(response.content or "") if response.success else None
            try:
                if raw is None:
                    raise ReActDecisionError(
                        _safe_error(response.error_message or "LLM output was not valid JSON.")
                    )
                decision = validate_react_decision(raw, allowed_tools, available_names)
            except ReActDecisionError as exc:
                reason = _safe_error(str(exc))
                state["invalid_decisions"] = int(state.get("invalid_decisions") or 0) + 1
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
                        }
                    },
                    error_message=reason,
                )
                invalid_count = int(state.get("invalid_decisions") or 0)
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
                    plan["react_state"] = state
                    _persist_plan(db, run_id, plan)
                    continue  # Let LLM retry with the rejection feedback visible
                # After 2 invalid decisions, fall back
                if settings.react_fallback_to_planned and not state.get("observation_history", [{}])[0].get("success"):
                    return _fallback_to_plan(db, run_id, plan, state, step_no, reason)
                if settings.react_finish_on_invalid_decision:
                    return _complete_report(db, run_id, plan, state, reason, settings, client, limitation=True)
                continue

        if is_finish_action(decision.action):
            rejection_reason = _early_finish_rejection_reason(plan, allowed_tools, state)
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
                        "early_finish_without_remote_mcp",
                    )
                continue
            summary = str(decision.args.get("summary") or decision.finish_reason or "Task complete.")[:500]
            metadata = _react_metadata(decision, summary, 0, state)
            record_trace_event(
                db,
                run_id,
                step_no,
                "finish",
                "success",
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
            limitation = "limitation" in str(decision.finish_reason or "").lower()
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

        counts = state.setdefault("tool_call_counts", {})
        count = int(counts.get(decision.action) or 0) + 1
        if count > settings.react_same_tool_max_calls:
            reason = (
                f"same_tool_max_calls reached for {decision.action}: "
                f"limit={settings.react_same_tool_max_calls}."
            )
            metadata = _react_metadata(
                decision,
                reason,
                count,
                state,
                error_type="tool_call_limit",
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
            store.update_agent_run_progress(db, run_id, step_no)
            return _complete_report(db, run_id, plan, state, reason, settings, client, limitation=True)

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

        counts[decision.action] = count
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

        execution_args = (
            file_reader_execution_arguments(decision.args, plan)
            if decision.action == "file_reader"
            else decision.args
        )
        started = perf_counter()
        result = execute_tool(decision.action, execution_args)
        latency_ms = int((perf_counter() - started) * 1000)
        observation_summary = _observation_summary(decision.action, result)
        metadata = _react_metadata(decision, observation_summary, count, state)
        metadata.update(result.metadata)
        trace_result = ToolResult(
            success=result.success,
            output=result.output,
            output_summary=observation_summary,
            error_message=result.error_message,
            metadata=metadata,
        )
        record_tool_result(
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
        )
        plan["react_state"] = state
        _persist_plan(db, run_id, plan)
        store.update_agent_run_progress(
            db,
            run_id,
            step_no,
            total_tool_calls_delta=1,
            latency_ms_delta=latency_ms,
        )

    reason = f"react_max_steps reached: limit={settings.react_max_steps}."
    record_trace_event(
        db,
        run_id,
        settings.react_max_steps,
        "finish",
        "success",
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
