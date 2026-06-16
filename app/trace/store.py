"""Small persistence helpers for run and trace records."""

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.trace.models import AgentRun, ToolTrace


def create_agent_run(
    db: Session,
    task: str,
    report_type: str,
    source_mode: str,
    allowed_tools: list[str] | None = None,
) -> AgentRun:
    """Create a pending run record."""

    run = AgentRun(
        run_id=uuid4().hex,
        task=task,
        report_type=report_type,
        source_mode=source_mode,
        status="pending",
        allowed_tools_json=json.dumps(allowed_tools) if allowed_tools else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_agent_run(db: Session, run_id: str) -> AgentRun | None:
    """Fetch one run by id."""

    return db.get(AgentRun, run_id)


def list_tool_traces(db: Session, run_id: str) -> list[ToolTrace]:
    """Return traces for a run in step order."""

    stmt = (
        select(ToolTrace)
        .where(ToolTrace.run_id == run_id)
        .order_by(ToolTrace.step_no.asc(), ToolTrace.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def create_tool_trace(
    db: Session,
    run_id: str,
    step_no: int,
    tool_name: str,
    status: str,
    input_summary: str | None = None,
    output_summary: str | None = None,
    error_message: str | None = None,
) -> ToolTrace:
    """Create a reserved trace record for future tool execution paths."""

    trace = ToolTrace(
        trace_id=uuid4().hex,
        run_id=run_id,
        step_no=step_no,
        tool_name=tool_name,
        status=status,
        input_summary=input_summary,
        output_summary=output_summary,
        error_message=error_message,
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    return trace
