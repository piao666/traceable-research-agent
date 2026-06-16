"""Task endpoints backed by SQLite run records."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskStatusResponse,
    ToolTraceResponse,
)
from app.trace import store
from app.trace.models import AgentRun, ToolTrace

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_status_response(run: AgentRun) -> TaskStatusResponse:
    return TaskStatusResponse(
        run_id=run.run_id,
        task=run.task,
        report_type=run.report_type,
        source_mode=run.source_mode,
        status=run.status,
        current_step=run.current_step,
        total_steps=run.total_steps,
        report_path=run.report_path,
        error_message=run.error_message,
        total_tool_calls=run.total_tool_calls,
        total_latency_ms=run.total_latency_ms,
        estimated_cost=run.estimated_cost,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _tool_trace_response(trace: ToolTrace) -> ToolTraceResponse:
    return ToolTraceResponse(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        step_no=trace.step_no,
        tool_name=trace.tool_name,
        input_summary=trace.input_summary,
        output_summary=trace.output_summary,
        status=trace.status,
        latency_ms=trace.latency_ms,
        error_message=trace.error_message,
        created_at=trace.created_at,
        finished_at=trace.finished_at,
    )


@router.post("", response_model=TaskCreateResponse)
async def create_task(
    request: TaskCreateRequest,
    db: Session = Depends(get_db),
) -> TaskCreateResponse:
    """Accept a task and create a pending database run record."""

    run = store.create_agent_run(
        db=db,
        task=request.task,
        report_type=request.report_type,
        source_mode=request.source_mode,
        allowed_tools=request.allowed_tools,
    )
    return TaskCreateResponse(
        run_id=run.run_id,
        status=run.status,
        status_url=f"/api/tasks/{run.run_id}",
        trace_url=f"/api/tasks/{run.run_id}/trace",
        report_url=f"/api/reports/{run.run_id}",
    )


@router.get("/{run_id}", response_model=TaskStatusResponse)
async def get_task_status(
    run_id: str,
    db: Session = Depends(get_db),
) -> TaskStatusResponse:
    """Return task status from the database."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    return _task_status_response(run)


@router.get("/{run_id}/trace", response_model=list[ToolTraceResponse])
async def get_task_trace(
    run_id: str,
    db: Session = Depends(get_db),
) -> list[ToolTraceResponse]:
    """Return trace rows for a run. Day4 permits an empty trace list."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")

    traces = store.list_tool_traces(db, run_id)
    return [_tool_trace_response(trace) for trace in traces]
