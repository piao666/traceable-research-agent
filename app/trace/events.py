"""Serialize run and trace rows into realtime stream events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.trace import store
from app.trace.models import AgentRun, ToolTrace


TERMINAL_STREAM_STATUSES = {"completed", "failed", "waiting_human"}


@dataclass
class TraceEventCursor:
    """Small in-memory cursor for one SSE connection."""

    seen_trace_ids: set[str] = field(default_factory=set)
    last_status: str | None = None
    report_ready_sent: bool = False
    done_sent: bool = False


def format_sse(event: dict[str, Any], event_id: str | None = None) -> str:
    """Return one Server-Sent Event frame."""

    event_type = str(event.get("event_type") or "message")
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(event, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def heartbeat_event(run_id: str) -> dict[str, Any]:
    return _base_event(run_id, "heartbeat")


def build_incremental_events(
    db: Session,
    run_id: str,
    cursor: TraceEventCursor,
    *,
    replay_existing: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    """Return new stream events and whether the stream can close."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        return [_base_event(run_id, "done", status="not_found", error_message="Task run not found.")], True

    events: list[dict[str, Any]] = []
    is_initial_poll = cursor.last_status is None
    if cursor.last_status is None or cursor.last_status != run.status:
        events.append(_run_status_event(run))
        if run.status == "waiting_human":
            events.append(_waiting_human_event(run))
        cursor.last_status = run.status

    for trace in store.list_tool_traces(db, run_id):
        if trace.trace_id in cursor.seen_trace_ids:
            continue
        if not replay_existing and is_initial_poll:
            cursor.seen_trace_ids.add(trace.trace_id)
            continue
        cursor.seen_trace_ids.add(trace.trace_id)
        events.append(_trace_event(trace))

    if run.report_path and not cursor.report_ready_sent:
        events.append(_report_ready_event(run))
        cursor.report_ready_sent = True

    should_close = run.status in TERMINAL_STREAM_STATUSES
    if should_close and not cursor.done_sent:
        events.append(_done_event(run))
        cursor.done_sent = True
    return events, should_close


def _base_event(
    run_id: str,
    event_type: str,
    *,
    trace_id: str | None = None,
    step_no: int | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    output_summary: str | None = None,
    error_message: str | None = None,
    latency_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: datetime | str | None = None,
    finished_at: datetime | str | None = None,
    current_step: int | None = None,
    total_steps: int | None = None,
    report_path: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "event_type": event_type,
        "trace_id": trace_id,
        "step_no": step_no,
        "tool_name": tool_name,
        "status": status,
        "output_summary": output_summary,
        "error_message": error_message,
        "latency_ms": latency_ms,
        "metadata": metadata or {},
        "created_at": _iso(created_at),
        "finished_at": _iso(finished_at),
        "current_step": current_step,
        "total_steps": total_steps,
        "report_path": report_path,
    }


def _run_status_event(run: AgentRun) -> dict[str, Any]:
    return _base_event(
        run.run_id,
        "run_status",
        status=run.status,
        error_message=run.error_message,
        created_at=run.created_at,
        finished_at=run.updated_at if run.status in TERMINAL_STREAM_STATUSES else None,
        current_step=run.current_step,
        total_steps=run.total_steps,
        report_path=run.report_path,
    )


def _waiting_human_event(run: AgentRun) -> dict[str, Any]:
    return _base_event(
        run.run_id,
        "waiting_human",
        status=run.status,
        output_summary=run.error_message or "Run is waiting for human confirmation.",
        error_message=run.error_message,
        created_at=run.updated_at,
        current_step=run.current_step,
        total_steps=run.total_steps,
        report_path=run.report_path,
    )


def _report_ready_event(run: AgentRun) -> dict[str, Any]:
    return _base_event(
        run.run_id,
        "report_ready",
        status=run.status,
        output_summary="Report is ready.",
        created_at=run.updated_at,
        finished_at=run.updated_at,
        current_step=run.current_step,
        total_steps=run.total_steps,
        report_path=run.report_path,
    )


def _done_event(run: AgentRun) -> dict[str, Any]:
    return _base_event(
        run.run_id,
        "done",
        status=run.status,
        error_message=run.error_message,
        created_at=run.updated_at,
        finished_at=run.updated_at,
        current_step=run.current_step,
        total_steps=run.total_steps,
        report_path=run.report_path,
    )


def _trace_event(trace: ToolTrace) -> dict[str, Any]:
    output = _parse_json(trace.output_json)
    metadata = _extract_metadata(output)
    if trace.status == "waiting_human":
        event_type = "waiting_human"
    elif trace.finished_at:
        event_type = "trace_finished"
    else:
        event_type = "trace_created"
    return _base_event(
        trace.run_id,
        event_type,
        trace_id=trace.trace_id,
        step_no=trace.step_no,
        tool_name=trace.tool_name,
        status=trace.status,
        output_summary=trace.output_summary,
        error_message=trace.error_message,
        latency_ms=trace.latency_ms,
        metadata=metadata,
        created_at=trace.created_at,
        finished_at=trace.finished_at,
    )


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_metadata(output: Any) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    metadata = output.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
