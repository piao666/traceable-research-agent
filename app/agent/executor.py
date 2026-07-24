"""Manual executor for deterministic plans."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.agent.file_access_policy import file_reader_execution_arguments
from app.agent.report_generation import resolve_report_llm_client
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings, settings as _exec_settings
from app.evidence.service import materialize_execution_provenance
from app.llm.base import LLMClient
from app.mcp.policy import MCPChannel, requires_interactive_confirmation, tool_channel
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool
from app.trace import store
from app.trace.logger import record_tool_result
from app.trace.models import AgentRun


EXECUTABLE_TOOLS = {
    "file_reader",
    "sql_query",
    "rag_search",
    "mcp_github_search",
    "tavily_search",
    "web_fetcher",
}


def is_executable_tool(tool_name: str) -> bool:
    """Return whether a tool can be executed by the structured executor."""

    if tool_name == "report_writer":
        return False
    spec = get_tool(tool_name)
    return bool(spec and spec.enabled and tool_channel(spec) != MCPChannel.WRITE.value)


def _step_requires_confirmation(step: dict[str, Any], tool_name: str) -> bool:
    spec = get_tool(tool_name)
    return bool(step.get("requires_confirmation")) or requires_interactive_confirmation(spec)


def _parse_plan(run: AgentRun) -> dict[str, Any]:
    if not run.plan_json:
        raise ValueError("Task run does not have a plan_json.")
    return json.loads(run.plan_json)


def _summary(run: AgentRun) -> dict[str, Any]:
    plan: dict[str, Any] = {}
    if run.plan_json:
        try:
            parsed = json.loads(run.plan_json)
            if isinstance(parsed, dict):
                plan = parsed
        except json.JSONDecodeError:
            pass
    react_state = plan.get("react_state")
    if not isinstance(react_state, dict):
        react_state = {}
    return {
        "run_id": run.run_id,
        "status": run.status,
        "current_step": run.current_step,
        "total_steps": run.total_steps,
        "total_tool_calls": run.total_tool_calls,
        "report_url": f"/api/reports/{run.run_id}",
        "trace_url": f"/api/tasks/{run.run_id}/trace",
        "error_message": run.error_message,
        "message": None,
        "execution_mode": plan.get("execution_mode") or "planned",
        "planner_source": plan.get("planner_source"),
        "llm_provider": react_state.get("llm_provider") or plan.get("llm_provider"),
        "llm_model": react_state.get("llm_model") or plan.get("llm_model"),
    }


def _resolve_arguments_from(
    step: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve `arguments_from` references from previous step outputs.

    Supported syntax:
        arguments_from: {"step_no": 1, "field": "results"}
        → extracts step_results[1].output["results"]

    For tavily_search results (list of dict with "url" key), auto-extracts URLs.
    """
    args_from = step.get("arguments_from")
    if not isinstance(args_from, dict):
        return step.get("arguments") or {}

    source_step_no = args_from.get("step_no")
    field = args_from.get("field")

    if source_step_no is None or not field:
        return step.get("arguments") or {}

    # Find the observation from the referenced step
    source_obs = None
    for obs in observations:
        if obs.get("step_no") == source_step_no:
            source_obs = obs
            break

    if source_obs is None:
        return step.get("arguments") or {}

    source_output = source_obs.get("output")
    if not isinstance(source_output, dict):
        return step.get("arguments") or {}

    resolved_value = source_output.get(field)

    # For tavily_search results → extract URLs
    if field == "results" and isinstance(resolved_value, list):
        urls: list[str] = []
        for item in resolved_value:
            if isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
        if urls:
            merged = dict(step.get("arguments") or {})
            merged["urls"] = urls
            return merged

    # Generic field extraction
    if resolved_value is not None:
        merged = dict(step.get("arguments") or {})
        merged[field] = resolved_value
        return merged

    return step.get("arguments") or {}


def _failed_observation(step: dict[str, Any], result: ToolResult) -> dict[str, Any]:
    return {
        "step_no": step.get("step_no"),
        "tool_name": step.get("tool_name"),
        "success": result.success,
        "output_summary": result.output_summary,
        "error_message": result.error_message,
        "output": result.output,
        "metadata": result.metadata,
    }


def _is_step_confirmed(plan: dict[str, Any], step_no: int) -> bool:
    confirmation = plan.get("confirmation")
    if not isinstance(confirmation, dict):
        return False
    return bool(confirmation.get("approved")) and confirmation.get("required_step_no") == step_no


def _message_summary(run: AgentRun, message: str) -> dict[str, Any]:
    summary = _summary(run)
    summary["message"] = message
    return summary


def run_plan(
    db: Session,
    run_id: str,
    settings_obj: Settings = _exec_settings,
    report_llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Execute a run plan step by step and generate a Markdown report."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    if run.status == "completed":
        return _message_summary(run, "Run already completed; no tools executed.")
    if run.status == "failed":
        return _message_summary(run, "Run is failed and cannot be rerun in Day13-15.")

    plan = _parse_plan(run)
    steps = plan.get("steps") or []
    observations: list[dict[str, Any]] = []
    resume_after_step = run.current_step

    try:
        run = store.update_agent_run_status(db, run_id, "running", None)
        for step in steps:
            step_no = int(step.get("step_no") or 0)
            tool_name = str(step.get("tool_name") or "")
            arguments = step.get("arguments") or {}
            if step_no <= resume_after_step:
                continue

            if _step_requires_confirmation(step, tool_name) and not _is_step_confirmed(plan, step_no):
                message = f"Waiting for human confirmation before step {step_no}: {tool_name}"
                run = store.update_agent_run_progress(db, run_id, max(step_no - 1, 0))
                run = store.update_agent_run_status(db, run_id, "waiting_human", message)
                return _message_summary(run, message)

            if tool_name == "report_writer":
                observations.append(
                    {
                        "step_no": step_no,
                        "tool_name": tool_name,
                        "success": True,
                        "output_summary": "Report writer step handled by the structured Reporter.",
                        "error_message": None,
                        "output": {"handled_by": "app.agent.reporter"},
                        "metadata": {},
                    }
                )
                run = store.update_agent_run_progress(db, run_id, step_no)
                continue

            if not is_executable_tool(tool_name):
                result = ToolResult(
                    success=False,
                    error_message=f"Executor does not support tool '{tool_name}'.",
                    metadata={"error_type": "unsupported_tool", "tool_name": tool_name},
                )
                record_tool_result(db, run_id, step_no, tool_name, arguments, result, 0)
                observations.append(_failed_observation(step, result))
                run = store.update_agent_run_progress(db, run_id, step_no, total_tool_calls_delta=1)
                continue

            # Resolve arguments_from references from previous step outputs
            if step.get("arguments_from"):
                arguments = _resolve_arguments_from(step, observations)

            execution_arguments = (
                file_reader_execution_arguments(arguments, plan)
                if tool_name == "file_reader"
                else arguments
            )
            started = perf_counter()
            result = execute_tool(tool_name, execution_arguments)
            latency_ms = int((perf_counter() - started) * 1000)
            record_tool_result(db, run_id, step_no, tool_name, arguments, result, latency_ms)
            observations.append(
                {
                    "step_no": step_no,
                    "tool_name": tool_name,
                    "success": result.success,
                    "output_summary": result.output_summary,
                    "error_message": result.error_message,
                    "output": result.output,
                    "metadata": result.metadata,
                }
            )
            run = store.update_agent_run_progress(
                db,
                run_id,
                step_no,
                total_tool_calls_delta=1,
                latency_ms_delta=latency_ms,
            )

        traces = store.list_tool_traces(db, run_id)
        run.status = "completed"
        run.error_message = None
        provenance_bundle = materialize_execution_provenance(
            db,
            run,
            plan,
            observations,
            traces,
            settings_obj,
        )
        _llm = resolve_report_llm_client(settings_obj, report_llm_client)
        markdown = generate_markdown_report(
            run,
            plan,
            observations,
            traces,
            llm_client=_llm,
            provenance_bundle=provenance_bundle,
        )
        report_path = save_report(run_id, markdown)
        run = store.update_agent_run_report(db, run_id, report_path)
        run = store.update_agent_run_status(db, run_id, "completed", None)
        return _summary(run)
    except Exception as exc:
        run = store.update_agent_run_status(db, run_id, "failed", str(exc))
        return _summary(run)
