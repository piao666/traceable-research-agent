"""Manual executor for deterministic plans."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.agent.reporter import generate_markdown_report, save_report
from app.tools.base import ToolResult
from app.tools.registry import execute_tool
from app.trace import store
from app.trace.logger import record_tool_result
from app.trace.models import AgentRun


EXECUTABLE_TOOLS = {"file_reader", "sql_query", "rag_search", "mcp_github_search"}


def _parse_plan(run: AgentRun) -> dict[str, Any]:
    if not run.plan_json:
        raise ValueError("Task run does not have a plan_json.")
    return json.loads(run.plan_json)


def _summary(run: AgentRun) -> dict[str, Any]:
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
    }


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


def run_plan(db: Session, run_id: str) -> dict[str, Any]:
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

            if step.get("requires_confirmation") and not _is_step_confirmed(plan, step_no):
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

            if tool_name not in EXECUTABLE_TOOLS:
                result = ToolResult(
                    success=False,
                    error_message=f"Executor does not support tool '{tool_name}'.",
                    metadata={"error_type": "unsupported_tool", "tool_name": tool_name},
                )
                record_tool_result(db, run_id, step_no, tool_name, arguments, result, 0)
                observations.append(_failed_observation(step, result))
                run = store.update_agent_run_progress(db, run_id, step_no, total_tool_calls_delta=1)
                continue

            started = perf_counter()
            result = execute_tool(tool_name, arguments)
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
        markdown = generate_markdown_report(run, plan, observations, traces)
        report_path = save_report(run_id, markdown)
        run = store.update_agent_run_report(db, run_id, report_path)
        run = store.update_agent_run_status(db, run_id, "completed", None)
        return _summary(run)
    except Exception as exc:
        run = store.update_agent_run_status(db, run_id, "failed", str(exc))
        return _summary(run)
