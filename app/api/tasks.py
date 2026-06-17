"""Task endpoints backed by SQLite run records."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import get_db
from app.schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
    TaskPlanResponse,
    TaskRunResponse,
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


def _task_run_response(summary: dict) -> TaskRunResponse:
    return TaskRunResponse(
        run_id=summary["run_id"],
        status=summary["status"],
        current_step=summary["current_step"],
        total_steps=summary["total_steps"],
        total_tool_calls=summary["total_tool_calls"],
        report_url=summary["report_url"],
        trace_url=summary["trace_url"],
        error_message=summary.get("error_message"),
        message=summary.get("message"),
    )


def _run_summary(run: AgentRun, message: str | None = None) -> dict:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "current_step": run.current_step,
        "total_steps": run.total_steps,
        "total_tool_calls": run.total_tool_calls,
        "report_url": f"/api/reports/{run.run_id}",
        "trace_url": f"/api/tasks/{run.run_id}/trace",
        "error_message": run.error_message,
        "message": message,
    }


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
    """Accept a task, create a pending run, and persist a deterministic plan."""

    run = store.create_agent_run(
        db=db,
        task=request.task,
        report_type=request.report_type,
        source_mode=request.source_mode,
        allowed_tools=request.allowed_tools,
    )
    plan = plan_task(
        task=request.task,
        allowed_tools=request.allowed_tools,
        source_mode=request.source_mode,
    )
    run = store.update_agent_run_plan(db, run.run_id, plan)
    return TaskCreateResponse(
        run_id=run.run_id,
        status=run.status,
        status_url=f"/api/tasks/{run.run_id}",
        trace_url=f"/api/tasks/{run.run_id}/trace",
        report_url=f"/api/reports/{run.run_id}",
        plan_url=f"/api/tasks/{run.run_id}/plan",
        run_url=f"/api/tasks/{run.run_id}/run",
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


@router.get("/{run_id}/plan", response_model=TaskPlanResponse)
async def get_task_plan(
    run_id: str,
    db: Session = Depends(get_db),
) -> TaskPlanResponse:
    """Return the deterministic plan persisted for a run."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if not run.plan_json:
        raise HTTPException(status_code=404, detail="Task run plan not found")
    try:
        plan = json.loads(run.plan_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Task run plan is invalid") from exc
    return TaskPlanResponse(run_id=run.run_id, **plan)


@router.post("/{run_id}/run", response_model=TaskRunResponse)
async def run_task(
    run_id: str,
    db: Session = Depends(get_db),
) -> TaskRunResponse:
    """Manually execute the persisted plan for a run."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if not run.plan_json:
        raise HTTPException(status_code=400, detail="Task run does not have a plan")
    if run.status == "completed":
        return _task_run_response(
            _run_summary(run, "Run already completed; no tools executed.")
        )
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Task run is already running")
    if run.status == "waiting_human":
        return _task_run_response(
            _run_summary(run, "Run is waiting for human confirmation. Call POST /api/tasks/{run_id}/confirm.")
        )
    if run.status == "failed":
        raise HTTPException(status_code=409, detail="Failed runs cannot be rerun in Day13-15")

    summary = run_plan(db, run_id)
    return _task_run_response(summary)


@router.post("/{run_id}/confirm", response_model=TaskConfirmResponse)
async def confirm_task(
    run_id: str,
    request: TaskConfirmRequest,
    db: Session = Depends(get_db),
) -> TaskConfirmResponse:
    """Confirm or reject a run waiting for human approval."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status != "waiting_human":
        raise HTTPException(
            status_code=400,
            detail="Current run is not waiting for human confirmation",
        )
    if not run.plan_json:
        raise HTTPException(status_code=400, detail="Task run does not have a plan")

    try:
        plan = json.loads(run.plan_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Task run plan is invalid") from exc

    required_step_no = None
    required_tool_name = None
    for step in plan.get("steps") or []:
        step_no = int(step.get("step_no") or 0)
        if step_no > run.current_step and step.get("requires_confirmation"):
            required_step_no = step_no
            required_tool_name = step.get("tool_name")
            break

    plan["confirmation"] = {
        "required_step_no": required_step_no,
        "required_tool_name": required_tool_name,
        "approved": request.approved,
        "comment": request.comment,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    store.replace_agent_run_plan(db, run_id, plan)

    if not request.approved:
        run = store.update_agent_run_status(
            db,
            run_id,
            "failed",
            "Human rejected execution.",
        )
        return TaskConfirmResponse(
            run_id=run.run_id,
            status=run.status,
            approved=False,
            comment=request.comment,
            resumed=False,
            message="Human rejected execution.",
            run_result=None,
        )

    if not request.resume:
        run = store.update_agent_run_status(db, run_id, "pending", None)
        return TaskConfirmResponse(
            run_id=run.run_id,
            status=run.status,
            approved=True,
            comment=request.comment,
            resumed=False,
            message="Human confirmation recorded. Run remains pending for manual resume.",
            run_result=None,
        )

    summary = run_plan(db, run_id)
    run_result = _task_run_response(summary)
    return TaskConfirmResponse(
        run_id=run_id,
        status=run_result.status,
        approved=True,
        comment=request.comment,
        resumed=True,
        message="Human confirmation recorded and run resumed.",
        run_result=run_result,
    )


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
