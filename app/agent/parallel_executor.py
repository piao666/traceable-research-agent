"""Optional parallel executor for safe independent planned tool steps."""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.file_access_policy import file_reader_execution_arguments
from app.agent.report_generation import resolve_report_llm_client
from app.agent.executor import (
    EXECUTABLE_TOOLS,
    _failed_observation,
    _is_step_confirmed,
    is_executable_tool,
    _step_requires_confirmation,
    _message_summary,
    _parse_plan,
    _summary,
)
from app.agent.reporter import generate_markdown_report, save_report
from app.config import Settings, settings
from app.evidence.service import materialize_execution_provenance
from app.mcp.policy import is_parallel_safe_tool
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool
from app.trace import store
from app.trace.logger import record_tool_result


PARALLEL_SAFE_TOOLS = {
    "file_reader",
    "rag_search",
    "mcp_github_search",
    "tavily_search",
    "sql_query",
}
BARRIER_TOOLS = {"report_writer"}
DEPENDENCY_KEYS = {
    "depends_on",
    "depends_on_step",
    "depends_on_steps",
    "after_step",
    "after_steps",
    "requires_step",
    "requires_steps",
}


@dataclass(frozen=True)
class _StepResult:
    step: dict[str, Any]
    result: ToolResult
    latency_ms: int
    started_at: str
    finished_at: str
    worker_id: int


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_explicit_dependency(step: dict[str, Any]) -> bool:
    if any(key in step for key in DEPENDENCY_KEYS):
        return True
    arguments = step.get("arguments")
    return isinstance(arguments, dict) and any(key in arguments for key in DEPENDENCY_KEYS)


def _is_parallel_candidate(step: dict[str, Any]) -> bool:
    tool_name = str(step.get("tool_name") or "")
    spec = get_tool(tool_name)
    return (
        (tool_name in PARALLEL_SAFE_TOOLS or (spec is not None and "mcp_remote" in spec.tags))
        and is_parallel_safe_tool(spec)
        and not _step_requires_confirmation(step, tool_name)
        and not _has_explicit_dependency(step)
    )


def _plan_groups(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            groups.append(pending)
            pending = []

    for step in steps:
        tool_name = str(step.get("tool_name") or "")
        if _is_parallel_candidate(step):
            pending.append(step)
            continue
        flush_pending()
        groups.append([step])
        if tool_name == "report_writer":
            trailing = [s for s in steps if int(s.get("step_no") or 0) > int(step.get("step_no") or 0)]
            if trailing:
                for trailing_step in trailing:
                    groups.append([trailing_step])
            break
    flush_pending()
    return groups


def _with_parallel_metadata(
    result: ToolResult,
    *,
    group_id: str,
    worker_id: int,
    group_size: int,
    started_at: str,
    finished_at: str,
    latency_ms: int,
) -> ToolResult:
    metadata = dict(result.metadata or {})
    metadata.update(
        {
            "parallel": True,
            "parallel_group_id": group_id,
            "parallel_worker_id": worker_id,
            "parallel_group_size": group_size,
            "execution_mode": "planned_parallel",
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": latency_ms,
        }
    )
    return ToolResult(
        success=result.success,
        output=result.output,
        output_summary=result.output_summary,
        error_message=result.error_message,
        metadata=metadata,
    )


def _timeout_result(message: str) -> ToolResult:
    return ToolResult(
        success=False,
        error_message=message,
        metadata={"error_type": "parallel_timeout"},
    )


def _execute_step(
    step: dict[str, Any],
    worker_id: int,
    plan: dict[str, Any] | None = None,
) -> _StepResult:
    tool_name = str(step.get("tool_name") or "")
    arguments = step.get("arguments") or {}
    execution_arguments = (
        file_reader_execution_arguments(arguments, plan)
        if tool_name == "file_reader"
        else arguments
    )
    started_at = _utc_iso()
    started = perf_counter()
    result = execute_tool(tool_name, execution_arguments)
    latency_ms = int((perf_counter() - started) * 1000)
    finished_at = _utc_iso()
    return _StepResult(step, result, latency_ms, started_at, finished_at, worker_id)


def _run_parallel_group(
    group: list[dict[str, Any]],
    settings_obj: Settings,
    plan: dict[str, Any] | None = None,
) -> list[_StepResult]:
    group_id = f"pg-{uuid4().hex[:12]}"
    group_size = len(group)
    max_workers = min(settings_obj.parallel_max_workers, group_size)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures: dict[Future[_StepResult], tuple[dict[str, Any], int, str]] = {}
    group_started_at = _utc_iso()
    try:
        for index, step in enumerate(group, 1):
            futures[executor.submit(_execute_step, step, index, plan)] = (step, index, group_started_at)

        done, pending = wait(set(futures), timeout=settings_obj.parallel_timeout_seconds)
        results: list[_StepResult] = []
        for future in done:
            step, worker_id, fallback_started_at = futures[future]
            try:
                step_result = future.result()
            except Exception as exc:
                finished_at = _utc_iso()
                step_result = _StepResult(
                    step=step,
                    result=ToolResult(
                        success=False,
                        error_message=str(exc),
                        metadata={"error_type": "parallel_worker_error"},
                    ),
                    latency_ms=0,
                    started_at=fallback_started_at,
                    finished_at=finished_at,
                    worker_id=worker_id,
                )
            results.append(
                _StepResult(
                    step=step_result.step,
                    result=_with_parallel_metadata(
                        step_result.result,
                        group_id=group_id,
                        worker_id=step_result.worker_id,
                        group_size=group_size,
                        started_at=step_result.started_at,
                        finished_at=step_result.finished_at,
                        latency_ms=step_result.latency_ms,
                    ),
                    latency_ms=step_result.latency_ms,
                    started_at=step_result.started_at,
                    finished_at=step_result.finished_at,
                    worker_id=step_result.worker_id,
                )
            )

        for future in pending:
            step, worker_id, fallback_started_at = futures[future]
            future.cancel()
            finished_at = _utc_iso()
            latency_ms = settings_obj.parallel_timeout_seconds * 1000
            results.append(
                _StepResult(
                    step=step,
                    result=_with_parallel_metadata(
                        _timeout_result(
                            f"Parallel tool timed out after {settings_obj.parallel_timeout_seconds} seconds."
                        ),
                        group_id=group_id,
                        worker_id=worker_id,
                        group_size=group_size,
                        started_at=fallback_started_at,
                        finished_at=finished_at,
                        latency_ms=latency_ms,
                    ),
                    latency_ms=latency_ms,
                    started_at=fallback_started_at,
                    finished_at=finished_at,
                    worker_id=worker_id,
                )
            )
        return sorted(results, key=lambda item: int(item.step.get("step_no") or 0))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_single_tool_step(
    step: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> _StepResult:
    return _execute_step(step, 1, plan)


def _observation(step: dict[str, Any], result: ToolResult) -> dict[str, Any]:
    return {
        "step_no": step.get("step_no"),
        "tool_name": step.get("tool_name"),
        "success": result.success,
        "output_summary": result.output_summary,
        "error_message": result.error_message,
        "output": result.output,
        "metadata": result.metadata,
    }


def run_plan_parallel(
    db: Session,
    run_id: str,
    settings_obj: Settings = settings,
) -> dict[str, Any]:
    """Execute independent planned tool steps in bounded parallel groups."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    if run.status == "completed":
        return _message_summary(run, "Run already completed; no tools executed.")
    if run.status == "failed":
        return _message_summary(run, "Run is failed and cannot be rerun in Day37.")

    plan = _parse_plan(run)
    steps = plan.get("steps") or []
    observations: list[dict[str, Any]] = []
    resume_after_step = run.current_step

    try:
        run = store.update_agent_run_status(db, run_id, "running", None)
        for group in _plan_groups(steps):
            executable_group = [
                step
                for step in group
                if int(step.get("step_no") or 0) > resume_after_step
            ]
            if not executable_group:
                continue

            if len(executable_group) == 1:
                step = executable_group[0]
                step_no = int(step.get("step_no") or 0)
                tool_name = str(step.get("tool_name") or "")
                arguments = step.get("arguments") or {}

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

                step_result = _run_single_tool_step(step, plan)
                record_tool_result(
                    db,
                    run_id,
                    step_no,
                    tool_name,
                    arguments,
                    step_result.result,
                    step_result.latency_ms,
                )
                observations.append(_observation(step, step_result.result))
                run = store.update_agent_run_progress(
                    db,
                    run_id,
                    step_no,
                    total_tool_calls_delta=1,
                    latency_ms_delta=step_result.latency_ms,
                )
                continue

            parallel_results = _run_parallel_group(executable_group, settings_obj, plan)
            for step_result in parallel_results:
                step = step_result.step
                step_no = int(step.get("step_no") or 0)
                tool_name = str(step.get("tool_name") or "")
                arguments = step.get("arguments") or {}
                record_tool_result(
                    db,
                    run_id,
                    step_no,
                    tool_name,
                    arguments,
                    step_result.result,
                    step_result.latency_ms,
                )
                observations.append(_observation(step, step_result.result))
            if parallel_results:
                max_step = max(int(item.step.get("step_no") or 0) for item in parallel_results)
                run = store.update_agent_run_progress(
                    db,
                    run_id,
                    max_step,
                    total_tool_calls_delta=len(parallel_results),
                    latency_ms_delta=sum(item.latency_ms for item in parallel_results),
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
        llm_client = resolve_report_llm_client(settings_obj)
        markdown = generate_markdown_report(
            run,
            plan,
            observations,
            traces,
            llm_client=llm_client,
            provenance_bundle=provenance_bundle,
        )
        report_path = save_report(run_id, markdown)
        run = store.update_agent_run_report(db, run_id, report_path)
        run = store.update_agent_run_status(db, run_id, "completed", None)
        return _summary(run)
    except Exception as exc:
        run = store.update_agent_run_status(db, run_id, "failed", str(exc))
        return _summary(run)
