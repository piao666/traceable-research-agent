"""Trace logging helpers for tool execution results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.tools.base import ToolResult
from app.trace.models import ToolTrace


def _safe_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"unserializable": str(data)}, ensure_ascii=False)


def _summarize_input(input_data: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in input_data.items():
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
) -> ToolTrace:
    """Persist one tool execution result as a trace row."""

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
        output_summary=result.output_summary,
        output_json=_safe_json(output_payload),
        status=_trace_status(result),
        latency_ms=latency_ms,
        error_message=result.error_message,
        created_at=now,
        finished_at=now,
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
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
) -> ToolTrace:
    """Persist a non-tool executor event such as finish, fallback, or HITL wait."""

    now = datetime.now(timezone.utc)
    trace = ToolTrace(
        trace_id=uuid4().hex,
        run_id=run_id,
        step_no=step_no,
        tool_name=tool_name,
        input_summary=_summarize_input(input_data),
        input_json=_safe_json(input_data),
        output_summary=output_summary,
        output_json=_safe_json(output_data),
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
        created_at=now,
        finished_at=now,
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    return trace
