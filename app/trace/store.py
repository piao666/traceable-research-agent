"""Small persistence helpers for run and trace records."""

import json
import threading
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.trace.models import AgentRun, ToolTrace

# Process-level write lock for concurrent SQLite writes
_TRACE_WRITE_LOCK = threading.Lock()


def create_agent_run(
    db: Session,
    task: str,
    report_type: str,
    source_mode: str,
    allowed_tools: list[str] | None = None,
    session_id: str | None = None,
    run_config_snapshot: str | None = None,
) -> AgentRun:
    """Create a pending run record."""

    run = AgentRun(
        run_id=uuid4().hex,
        task=task,
        report_type=report_type,
        source_mode=source_mode,
        status="pending",
        allowed_tools_json=json.dumps(allowed_tools) if allowed_tools else None,
        session_id=session_id,
        run_config_snapshot=run_config_snapshot,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_agent_run(db: Session, run_id: str) -> AgentRun | None:
    """Fetch one run by id."""

    return db.get(AgentRun, run_id)


def list_agent_runs(
    db: Session,
    session_id: str | None = None,
    status: str | None = None,
    execution_mode: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentRun]:
    """List agent runs with optional filters and pagination."""
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc())
    if session_id:
        stmt = stmt.where(AgentRun.session_id == session_id)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    if execution_mode:
        from app.trace.models import ToolTrace
        # execution_mode is stored in plan_json, fall back to full scan
        pass
    if created_after:
        stmt = stmt.where(AgentRun.created_at >= created_after)
    if created_before:
        stmt = stmt.where(AgentRun.created_at <= created_before)
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def count_agent_runs(
    db: Session,
    session_id: str | None = None,
    status: str | None = None,
) -> int:
    """Count agent runs matching optional filters."""
    from sqlalchemy import func
    stmt = select(func.count()).select_from(AgentRun)
    if session_id:
        stmt = stmt.where(AgentRun.session_id == session_id)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    return db.execute(stmt).scalar() or 0


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


def update_agent_run_config_snapshot(
    db: Session,
    run_id: str,
    snapshot: dict,
) -> AgentRun:
    """Persist the safe runtime and task-specific execution constraints."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.run_config_snapshot = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
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


def claim_pending_agent_run(db: Session, run_id: str) -> bool:
    """Atomically move a pending run to running for background execution."""

    result = db.execute(
        update(AgentRun)
        .where(AgentRun.run_id == run_id, AgentRun.status == "pending")
        .values(
            status="running",
            error_message=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return result.rowcount == 1


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


def update_agent_run_cost(
    db: Session,
    run_id: str,
    token_in: int = 0,
    token_out: int = 0,
    estimated_cost: float = 0.0,
) -> AgentRun:
    """Update aggregated token and cost counters on a run."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.total_tool_calls = max(run.total_tool_calls, 1)
    run.estimated_cost = round(run.estimated_cost + estimated_cost, 6)
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def update_agent_run_citation_validation(
    db: Session,
    run_id: str,
    *,
    total: int,
    supported: int,
    weakly_supported: int,
    unsupported: int,
    accuracy: float,
) -> AgentRun:
    """Persist the final citation-validation metrics for a run."""
    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    run.citation_total = max(0, int(total))
    run.citation_supported = max(0, int(supported))
    run.citation_weakly_supported = max(0, int(weakly_supported))
    run.citation_unsupported = max(0, int(unsupported))
    run.citation_accuracy = min(max(float(accuracy), 0.0), 1.0)
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
    with _TRACE_WRITE_LOCK:
        db.add(trace)
        db.commit()
        db.refresh(trace)
    return trace
