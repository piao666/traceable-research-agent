"""Small persistence helpers for run and trace records."""

import json
from datetime import datetime, timezone
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


def update_agent_run_plan(db: Session, run_id: str, plan: dict) -> AgentRun:
    """Persist a deterministic plan on an existing run."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.plan_json = json.dumps(plan, ensure_ascii=False, default=str)
    run.total_steps = len(plan.get("steps") or [])
    run.current_step = 0
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def update_agent_run_status(
    db: Session,
    run_id: str,
    status: str,
    error_message: str | None = None,
) -> AgentRun:
    """Update run status and optional error message."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.status = status
    run.error_message = error_message
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def update_agent_run_progress(
    db: Session,
    run_id: str,
    current_step: int,
    total_tool_calls_delta: int = 0,
    latency_ms_delta: int = 0,
) -> AgentRun:
    """Advance run progress counters."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.current_step = current_step
    run.total_tool_calls += total_tool_calls_delta
    run.total_latency_ms += latency_ms_delta
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def update_agent_run_report(db: Session, run_id: str, report_path: str) -> AgentRun:
    """Save report path on an existing run."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.report_path = report_path
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def replace_agent_run_plan(db: Session, run_id: str, plan: dict) -> AgentRun:
    """Replace plan JSON without resetting progress counters."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.plan_json = json.dumps(plan, ensure_ascii=False, default=str)
    run.total_steps = len(plan.get("steps") or [])
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


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
