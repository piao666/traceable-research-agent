"""Trace logging helpers for tool execution results."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.security.redaction import redact_sensitive_data, redact_text
from app.tools.base import ToolResult
from app.tools.errors import normalize_error_metadata, normalize_tool_result
from app.trace.models import ToolTrace


_trace_context: ContextVar[dict[str, str | None]] = ContextVar(
    "trace_context", default={"run_id": None, "trace_id": None}
)


@contextmanager
def trace_context(run_id: str | None, trace_id: str | None = None):
    """Bind run/trace identifiers for logs emitted during one operation."""

    token = _trace_context.set({"run_id": run_id, "trace_id": trace_id})
    try:
        yield
    finally:
        _trace_context.reset(token)


def log_context() -> dict[str, str | None]:
    """Return the current identifiers used by Docker-visible log messages."""

    return dict(_trace_context.get())


def trace_logger(name: str) -> logging.LoggerAdapter:
    """Create a logger adapter with stable run/trace context fields."""

    return logging.LoggerAdapter(logging.getLogger(name), log_context())


def _safe_json(data: Any) -> str:
    try:
        return json.dumps(redact_sensitive_data(data), ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(
            {"unserializable": redact_text(data)},
            ensure_ascii=False,
        )


def _summarize_input(input_data: dict[str, Any]) -> str:
    parts: list[str] = []
    safe_input = redact_sensitive_data(input_data)
    for key, value in safe_input.items():
        if isinstance(value, str):
            value_summary = value if len(value) <= 120 else value[:117] + "..."
        else:
            value_summary = repr(value)
        parts.append(f"{key}={value_summary}")
    return ", ".join(parts)[:500]


def _trace_status(result: ToolResult) -> str:
    if result.success:
        return "success"
    if result.metadata.get("error_type") == "safety_rejected":
        return "rejected"
    return "failed"


def record_tool_result(
    db: Session,
    run_id: str,
    step_no: int,
    tool_name: str,
    input_data: dict[str, Any],
    result: ToolResult,
    latency_ms: int | None = None,
    sub_query: str | None = None,
    token_in: int = 0,
    token_out: int = 0,
    estimated_cost: float = 0.0,
    phase: str | None = None,
    parent_trace_id: str | None = None,
    attempt: int = 1,
) -> ToolTrace:
    """Persist one tool execution result as a trace row."""

    result = normalize_tool_result(result)
    if isinstance(result.output, dict):
        output_payload = dict(result.output)
        if result.metadata:
            output_payload["metadata"] = result.metadata
    elif result.output is None:
        output_payload = {"metadata": result.metadata}
    else:
        output_payload = {"result": result.output, "metadata": result.metadata}
    now = datetime.now(timezone.utc)
    trace = ToolTrace(
        trace_id=uuid4().hex,
        run_id=run_id,
        step_no=step_no,
        tool_name=tool_name,
        input_summary=_summarize_input(input_data),
        input_json=_safe_json(input_data),
        output_summary=redact_text(result.output_summary) if result.output_summary else None,
        output_json=_safe_json(output_payload),
        status=_trace_status(result),
        latency_ms=latency_ms,
        error_message=redact_text(result.error_message) if result.error_message else None,
        created_at=now,
        finished_at=now,
        sub_query=sub_query,
        token_in=token_in,
        token_out=token_out,
        estimated_cost=estimated_cost,
        phase=phase,
        parent_trace_id=parent_trace_id,
        attempt=max(1, int(attempt)),
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    logging.getLogger(__name__).info(
        "[run_id=%s trace_id=%s phase=%s attempt=%s] tool=%s status=%s",
        trace.run_id, trace.trace_id, trace.phase or "tool", trace.attempt,
        trace.tool_name, trace.status,
    )
    return trace


def record_trace_event(
    db: Session,
    run_id: str,
    step_no: int,
    tool_name: str,
    status: str,
    input_data: dict[str, Any],
    output_summary: str,
    output_data: dict[str, Any],
    error_message: str | None = None,
    latency_ms: int | None = None,
    sub_query: str | None = None,
    token_in: int = 0,
    token_out: int = 0,
    estimated_cost: float = 0.0,
    phase: str | None = None,
    parent_trace_id: str | None = None,
    attempt: int = 1,
) -> ToolTrace:
    """Persist a non-tool executor event such as finish, fallback, or HITL wait."""

    safe_output = redact_sensitive_data(output_data)
    if isinstance(safe_output, dict) and isinstance(safe_output.get("metadata"), dict):
        safe_output["metadata"] = normalize_error_metadata(
            safe_output["metadata"],
            error_message,
        )
    now = datetime.now(timezone.utc)
    trace = ToolTrace(
        trace_id=uuid4().hex,
        run_id=run_id,
        step_no=step_no,
        tool_name=tool_name,
        input_summary=_summarize_input(input_data),
        input_json=_safe_json(input_data),
        output_summary=redact_text(output_summary),
        output_json=_safe_json(safe_output),
        status=status,
        latency_ms=latency_ms,
        error_message=redact_text(error_message) if error_message else None,
        created_at=now,
        finished_at=now,
        sub_query=sub_query,
        token_in=token_in,
        token_out=token_out,
        estimated_cost=estimated_cost,
        phase=phase,
        parent_trace_id=parent_trace_id,
        attempt=max(1, int(attempt)),
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    logging.getLogger(__name__).info(
        "[run_id=%s trace_id=%s phase=%s attempt=%s] event=%s status=%s",
        trace.run_id, trace.trace_id, trace.phase or "event", trace.attempt,
        trace.tool_name, trace.status,
    )
    return trace


def record_phase_event(
    db: Session,
    run_id: str,
    phase: str,
    status: str,
    *,
    step_no: int = 0,
    parent_trace_id: str | None = None,
    attempt: int = 1,
    details: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> ToolTrace:
    """Persist a lifecycle phase event using the normal trace schema."""

    return record_trace_event(
        db,
        run_id,
        step_no,
        f"phase.{phase}",
        status,
        {"phase": phase},
        f"Phase {phase} {status}.",
        details or {},
        error_message=error_message,
        phase=phase,
        parent_trace_id=parent_trace_id,
        attempt=attempt,
    )
