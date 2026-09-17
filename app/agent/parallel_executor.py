"""Optional parallel executor for safe independent planned tool steps."""

from __future__ import annotations

import json
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.file_access_policy import file_reader_execution_arguments
from app.agent.report_generation import resolve_report_llm_client
from app.agent.executor import (
    EXECUTABLE_TOOLS,
    _after_run_completed,
    _failed_observation,
    _is_step_confirmed,
    _resolve_arguments_from,
    is_executable_tool,
    _step_requires_confirmation,
    _message_summary,
    _parse_plan,
    _persist_citation_validation,
    _persist_reference_verification,
    _summary,
)
from app.agent.reporter import generate_markdown_report, save_report
from app.agent.source_intake import execute_governed_operation, prepare_tool_arguments
from app.config import Settings, settings
from app.evidence.service import materialize_execution_provenance
from app.agent.preflight import enforce_execution_readiness
from app.agent.outcome import dependency_missing, enforce_research_outcome, fail_execution, load_observations, report_subject, skip_dependency
from app.agent.budget import budgeted_execution, reserve_tool, BudgetExceeded
from app.mcp.policy import is_parallel_safe_tool
from app.research.models import ResearchOperation
from app.tools.base import ToolResult
from app.tools.registry import execute_tool, get_tool
from app.trace import store
from app.trace.logger import record_tool_result


PARALLEL_SAFE_TOOLS = {
    "file_reader",
    "mcp_github_search",
    "tavily_search",
    "sql_query",
    "web_fetcher",
    "arxiv_search",
    "semantic_scholar_search",
    "openalex_search",
    "crossref_search",
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


def _operation_key(step: dict[str, Any]) -> str:
    return f"planned-step:{int(step.get('step_no') or 0)}:{str(step.get('tool_name') or '')}"


def _reserve_parallel_operation(db: Session, run_id: str, step: dict[str, Any]) -> ResearchOperation:
    """Reserve an operation before worker submission; workers never share Session."""
    logical_key = _operation_key(step)
    previous = (
        db.query(ResearchOperation)
        .filter(ResearchOperation.root_run_id == run_id, ResearchOperation.logical_key == logical_key)
        .order_by(ResearchOperation.attempt.desc())
        .first()
    )
    attempt = 1
    if previous is not None:
        attempt = int(previous.attempt or 1) + 1
        if previous.status in {"reserved", "running"}:
            previous.status = "interrupted"
            previous.error_message = "Coordinator restarted before operation completion."
    arguments_hash = hashlib.sha256(
        json.dumps(step.get("arguments") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    operation = ResearchOperation(
        operation_id=f"op-{uuid4().hex}", root_run_id=run_id, run_id=run_id,
        operation_kind="planned_tool", logical_key=logical_key, attempt=attempt,
        status="running", arguments_hash=arguments_hash,
        lease_owner="parallel-coordinator", started_at=datetime.now(timezone.utc),
    )
    db.add(operation)
    db.commit()
    return operation


def _finish_parallel_operation(db: Session, operation_id: str, result: ToolResult, *, interrupted: bool = False) -> None:
    operation = db.get(ResearchOperation, operation_id)
    if operation is None:
        return
    operation.status = "interrupted" if interrupted else ("succeeded" if result.success else "failed")
    operation.result_revision = str((result.metadata or {}).get("result_revision") or "") or None
    operation.error_message = result.error_message
    operation.finished_at = datetime.now(timezone.utc)
    db.commit()


def _has_explicit_dependency(step: dict[str, Any]) -> bool:
    if isinstance(step.get("arguments_from"), dict):
        return True
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
    operation_id: str | None = None,
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
    if operation_id:
        metadata["operation_id"] = operation_id
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
    visited_urls: set[str] | None = None,
    visited_urls_lock: Lock | None = None,
    settings_obj: Settings = settings,
    budget_reserved: bool = False,
) -> _StepResult:
    tool_name = str(step.get("tool_name") or "")
    arguments = step.get("arguments") or {}

    # Deduplicate URLs across parallel sub-queries
    if tool_name == "web_fetcher" and visited_urls is not None and visited_urls_lock is not None:
        urls = arguments.get("urls")
        if isinstance(urls, list):
            with visited_urls_lock:
                filtered: list[str] = []
                skipped: list[str] = []
                for url in urls:
                    if isinstance(url, str) and url not in visited_urls:
                        visited_urls.add(url)
                        filtered.append(url)
                    elif isinstance(url, str):
                        skipped.append(url)
                if skipped:
                    step = dict(step)
                    step["metadata"] = dict(step.get("metadata") or {})
                    step["metadata"]["skipped_urls"] = skipped
                arguments = dict(arguments)
                arguments["urls"] = filtered
                step["arguments"] = arguments

    arguments = prepare_tool_arguments(tool_name, arguments, plan or {}, settings_obj)
    step = dict(step)
    step["arguments"] = arguments
    started_at = _utc_iso()
    started = perf_counter()
    policy_plan = plan if plan is not None else {"steps": [step]}
    result = execute_governed_operation(
        tool_name,
        arguments,
        policy_plan,
        settings_obj,
        execute_tool,
        execution_arguments=(
            (lambda prepared: file_reader_execution_arguments(prepared, plan))
            if tool_name == "file_reader"
            else None
        ),
        budget_reserved=budget_reserved,
        arguments_prepared=True,
    )
    latency_ms = int((perf_counter() - started) * 1000)
    finished_at = _utc_iso()
    return _StepResult(step, result, latency_ms, started_at, finished_at, worker_id)


def _run_parallel_group(
    db: Session,
    run_id: str,
    group: list[dict[str, Any]],
    settings_obj: Settings,
    plan: dict[str, Any] | None = None,
    visited_urls: set[str] | None = None,
    visited_urls_lock: Lock | None = None,
) -> list[_StepResult]:
    group_id = f"pg-{uuid4().hex[:12]}"
    group_size = len(group)
    max_workers = min(settings_obj.parallel_max_workers, group_size)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures: dict[Future[_StepResult], tuple[dict[str, Any], int, str, str]] = {}
    group_started_at = _utc_iso()
    results: list[_StepResult] = []
    try:
        for index, step in enumerate(group, 1):
            operation = _reserve_parallel_operation(db, run_id, step)
            try:
                reserve_tool(str(step.get("tool_name")))
            except BudgetExceeded as exc:
                _finish_parallel_operation(
                    db,
                    operation.operation_id,
                    ToolResult(success=False, error_message=str(exc), metadata={"executed": False}),
                )
                results.append(_StepResult(step, ToolResult(success=False, error_message=str(exc),
                    metadata={"error_type": "budget_exhausted", "executed": False}), 0,
                    group_started_at, _utc_iso(), index))
                continue
            futures[executor.submit(
                copy_context().run, _execute_step, step, index, plan, visited_urls, visited_urls_lock, settings_obj, True
            )] = (step, index, group_started_at, operation.operation_id)

        done, pending = wait(set(futures), timeout=settings_obj.parallel_timeout_seconds)
        for future in done:
            step, worker_id, fallback_started_at, operation_id = futures[future]
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
            _finish_parallel_operation(db, operation_id, step_result.result)
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
                        operation_id=operation_id,
                    ),
                    latency_ms=step_result.latency_ms,
                    started_at=step_result.started_at,
                    finished_at=step_result.finished_at,
                    worker_id=step_result.worker_id,
                )
            )

        for future in pending:
            step, worker_id, fallback_started_at, operation_id = futures[future]
            future.cancel()
            finished_at = _utc_iso()
            latency_ms = settings_obj.parallel_timeout_seconds * 1000
            timeout_result = _timeout_result(
                f"Parallel tool timed out after {settings_obj.parallel_timeout_seconds} seconds."
            )
            _finish_parallel_operation(db, operation_id, timeout_result, interrupted=True)
            results.append(
                _StepResult(
                    step=step,
                    result=_with_parallel_metadata(
                        timeout_result,
                        group_id=group_id,
                        worker_id=worker_id,
                        group_size=group_size,
                        started_at=fallback_started_at,
                        finished_at=finished_at,
                        latency_ms=latency_ms,
                        operation_id=operation_id,
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
    visited_urls: set[str] | None = None,
    visited_urls_lock: Lock | None = None,
    settings_obj: Settings = settings,
) -> _StepResult:
    return _execute_step(step, 1, plan, visited_urls, visited_urls_lock, settings_obj)


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


@budgeted_execution
def run_plan_parallel(
    db: Session,
    run_id: str,
    settings_obj: Settings = settings,
    completion_status: str = "completed",
    report_llm_client=None,
) -> dict[str, Any]:
    """Execute independent planned tool steps in bounded parallel groups."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found.")
    if run.status == "completed":
        return _message_summary(run, "Run already completed; no tools executed.")
    if run.status in ("failed", "cancelled"):
        return _message_summary(run, f"Run is {run.status} and cannot be executed.")
    if run.status in {"waiting_human", "waiting_human_plan"}:
        return _message_summary(run, "Run is waiting for human approval.")

    plan = _parse_plan(run)
    if not enforce_execution_readiness(db, run_id, plan, settings_obj,
                                      llm_available=bool(report_llm_client and report_llm_client.is_available())):
        return _summary(store.get_fresh_agent_run(db, run_id))
    steps = plan.get("steps") or []
    observations = load_observations(store.list_tool_traces(db, run_id))
    resume_after_step = run.current_step

    # Shared URL dedup across sub-queries
    visited_urls: set[str] = set()
    visited_urls_lock = Lock()

    try:
        run = store.mark_agent_run_running_unless_cancelled(db, run_id)
        if run.status == "cancelled":
            return _message_summary(run, "Run was cancelled before execution started.")
        for group in _plan_groups(steps):
            if store.is_agent_run_cancelled(db, run_id):
                cancelled = store.get_fresh_agent_run(db, run_id)
                return _message_summary(cancelled, "Run cancelled by user.")
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

                if dependency_missing(step, observations):
                    observations.append(skip_dependency(db, run_id, step))
                    continue

                # Resolve arguments_from references
                if step.get("arguments_from"):
                    arguments = _resolve_arguments_from(step, observations)
                    step = dict(step)
                    step["arguments"] = arguments

                step_result = _run_single_tool_step(
                    step, plan, visited_urls, visited_urls_lock, settings_obj
                )
                actual_arguments = step_result.step.get("arguments") or arguments
                trace = record_tool_result(
                    db,
                    run_id,
                    step_no,
                    tool_name,
                    actual_arguments,
                    step_result.result,
                    step_result.latency_ms,
                )
                observation = _observation(step, step_result.result)
                observation["trace_id"] = trace.trace_id
                observations.append(observation)
                run = store.update_agent_run_progress(
                    db,
                    run_id,
                    step_no,
                    total_tool_calls_delta=0 if step_result.result.metadata.get("executed") is False else 1,
                    latency_ms_delta=step_result.latency_ms,
                )

                continue

            parallel_results = _run_parallel_group(
                db,
                run_id,
                executable_group, settings_obj, plan, visited_urls, visited_urls_lock
            )
            for step_result in parallel_results:
                step = step_result.step
                step_no = int(step.get("step_no") or 0)
                tool_name = str(step.get("tool_name") or "")
                arguments = step.get("arguments") or {}
                trace = record_tool_result(
                    db,
                    run_id,
                    step_no,
                    tool_name,
                    arguments,
                    step_result.result,
                    step_result.latency_ms,
                )
                observation = _observation(step, step_result.result)
                observation["trace_id"] = trace.trace_id
                observations.append(observation)
            if parallel_results:
                max_step = max(int(item.step.get("step_no") or 0) for item in parallel_results)
                run = store.update_agent_run_progress(
                    db,
                    run_id,
                    max_step,
                    total_tool_calls_delta=sum(item.result.metadata.get("executed") is not False for item in parallel_results),
                    latency_ms_delta=sum(item.latency_ms for item in parallel_results),
                )

        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")

        traces = store.list_tool_traces(db, run_id)
        if not enforce_research_outcome(db, run, plan, observations, traces, settings_obj):
            return _summary(store.get_fresh_agent_run(db, run_id))
        provenance_bundle = materialize_execution_provenance(
            db,
            run,
            plan,
            observations,
            traces,
            settings_obj,
        )
        llm_client = resolve_report_llm_client(settings_obj, report_llm_client)
        citation_validation_reports: list[Any] = []
        reference_verification_reports: list[Any] = []
        markdown = generate_markdown_report(
            report_subject(run),
            plan,
            observations,
            traces,
            llm_client=llm_client,
            provenance_bundle=provenance_bundle,
            report_type=run.report_type,
            citation_validation_callback=citation_validation_reports.append,
            reference_verification_callback=reference_verification_reports.append,
        )
        report_path = save_report(run_id, markdown)
        run = store.update_agent_run_report(db, run_id, report_path)
        run = _persist_citation_validation(
            db,
            run_id,
            citation_validation_reports,
            traces,
        )
        traces = store.list_tool_traces(db, run_id)
        run = _persist_reference_verification(
            db,
            run_id,
            reference_verification_reports,
            traces,
        )
        traces = store.list_tool_traces(db, run_id)
        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        run = store.update_agent_run_status(db, run_id, completion_status, None)
        _after_run_completed(db, run, markdown, step_no=0)
        return _summary(run)
    except Exception as exc:
        if store.is_agent_run_cancelled(db, run_id):
            cancelled = store.get_fresh_agent_run(db, run_id)
            return _message_summary(cancelled, "Run cancelled by user.")
        db.rollback()
        run = fail_execution(db, run_id, exc)
        return _summary(run)
