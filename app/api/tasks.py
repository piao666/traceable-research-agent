"""Task endpoints backed by SQLite run records."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agent.dispatcher import run_task_by_mode
from app.agent.evidence import build_evidence_bundle
from app.agent.evidence_exporter import (
    export_evidence_bundle,
    export_filename,
    export_media_type,
    read_export_text,
    resolve_export_path,
)
from app.agent.file_access_policy import (
    CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS,
    confirmation_details_for_path,
)
from app.agent.planner import plan_task
from app.config import settings
from app.database import SessionLocal, get_db
from app.schemas import (
    AsyncRunResponse,
    EvidenceBundleResponse,
    EvidenceExportContentResponse,
    EvidenceExportResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
    TaskPlanResponse,
    TaskRunResponse,
    TaskStatusResponse,
    ToolTraceResponse,
)
from app.security import require_api_key, require_request_context
from app.trace import store
from app.trace.models import AgentRun, ToolTrace

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)


def _task_status_response(run: AgentRun) -> TaskStatusResponse:
    plan_meta = _plan_metadata(run)
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
        execution_mode=plan_meta["execution_mode"],
        requested_execution_mode=plan_meta.get("requested_execution_mode"),
        planner_source=plan_meta.get("planner_source"),
        llm_provider=plan_meta.get("llm_provider"),
        llm_model=plan_meta.get("llm_model"),
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
        execution_mode=summary.get("execution_mode", "planned"),
        planner_source=summary.get("planner_source"),
        llm_provider=summary.get("llm_provider"),
        llm_model=summary.get("llm_model"),
    )


def _run_summary(run: AgentRun, message: str | None = None) -> dict:
    plan_meta = _plan_metadata(run)
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
        **plan_meta,
    }


def _async_run_response(run: AgentRun, message: str) -> AsyncRunResponse:
    plan_meta = _plan_metadata(run)
    return AsyncRunResponse(
        run_id=run.run_id,
        status=run.status,
        status_url=f"/api/tasks/{run.run_id}",
        trace_url=f"/api/tasks/{run.run_id}/trace",
        report_url=f"/api/reports/{run.run_id}",
        message=message,
        execution_mode=plan_meta["execution_mode"],
    )


def _plan_metadata(run: AgentRun) -> dict:
    plan: dict = {}
    if run.plan_json:
        try:
            parsed = json.loads(run.plan_json)
            if isinstance(parsed, dict):
                plan = parsed
        except json.JSONDecodeError:
            pass
    react_state = plan.get("react_state")
    if not isinstance(react_state, dict):
        react_state = {}
    return {
        "execution_mode": plan.get("execution_mode") or "planned",
        "requested_execution_mode": plan.get("requested_execution_mode")
        or plan.get("execution_mode")
        or "planned",
        "planner_source": plan.get("planner_source"),
        "llm_provider": react_state.get("llm_provider") or plan.get("llm_provider"),
        "llm_model": react_state.get("llm_model") or plan.get("llm_model"),
    }


def _run_task_in_background(run_id: str) -> None:
    """Execute with a fresh session because request-scoped sessions are closed."""

    with SessionLocal() as db:
        try:
            run_task_by_mode(db, run_id)
        except Exception as exc:
            try:
                store.update_agent_run_status(db, run_id, "failed", str(exc))
            except Exception:
                db.rollback()


def _tool_trace_response(trace: ToolTrace) -> ToolTraceResponse:
    output = _parse_trace_output(trace.output_json)
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
        output=output,
        metadata=_extract_trace_metadata(output),
    )


def _parse_trace_output(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _extract_trace_metadata(output) -> dict | None:
    if not isinstance(output, dict):
        return None
    metadata = output.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    keys = {
        "embedding_backend",
        "vector_backend",
        "requested_embedding_backend",
        "requested_vector_backend",
        "fallback_used",
        "dimension",
        "model_path",
        "persist_dir",
        "collection_name",
    }
    selected = {key: output[key] for key in keys if key in output}
    return selected or None


def _parse_run_plan(run: AgentRun) -> dict[str, Any]:
    if not run.plan_json:
        return {}
    try:
        parsed = json.loads(run.plan_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _export_run_evidence(
    db: Session,
    run_id: str,
    export_format: str,
):
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    traces = store.list_tool_traces(db, run_id)
    bundle = build_evidence_bundle(run, _parse_run_plan(run), [], traces)
    return export_evidence_bundle(bundle, export_format)


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
        execution_mode_override=request.execution_mode_override,
    )
    plan.setdefault("requested_execution_mode", plan.get("execution_mode") or settings.execution_mode)
    plan.setdefault("execution_mode", settings.execution_mode)
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

    summary = run_task_by_mode(db, run_id)
    return _task_run_response(summary)


@router.post("/{run_id}/run_async", response_model=AsyncRunResponse)
async def run_task_async(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AsyncRunResponse:
    """Queue the existing synchronous executor as a FastAPI background task."""

    if not settings.async_run_enabled:
        raise HTTPException(status_code=400, detail="Async run is disabled.")

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if not run.plan_json:
        raise HTTPException(status_code=400, detail="Task run does not have a plan")
    if run.status == "completed":
        return _async_run_response(run, "Run already completed; no tools executed.")
    if run.status == "running":
        return _async_run_response(run, "Run is already running; no duplicate task queued.")
    if run.status == "waiting_human":
        return _async_run_response(
            run,
            "Run is waiting for human confirmation. Call POST /api/tasks/{run_id}/confirm.",
        )
    if run.status == "failed":
        raise HTTPException(status_code=409, detail="Failed runs cannot be rerun in Day29")

    if not store.claim_pending_agent_run(db, run_id):
        db.expire_all()
        current = store.get_agent_run(db, run_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Task run not found")
        return _async_run_response(
            current,
            "Run is already running; no duplicate task queued.",
        )

    db.expire_all()
    run = store.get_agent_run(db, run_id)
    background_tasks.add_task(_run_task_in_background, run_id)
    return _async_run_response(run, "Async run started.")


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
    required_confirmation_details = None
    react_state = plan.get("react_state")
    pending = react_state.get("pending_confirmation") if isinstance(react_state, dict) else None
    if isinstance(pending, dict):
        decision = pending.get("decision") or {}
        required_step_no = int(pending.get("step_no") or run.current_step + 1)
        required_tool_name = decision.get("action")
        if required_tool_name == "file_reader":
            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            path = str(args.get("path") or "").strip()
            if path:
                required_confirmation_details = confirmation_details_for_path(path)
    else:
        for step in plan.get("steps") or []:
            step_no = int(step.get("step_no") or 0)
            if step_no > run.current_step and step.get("requires_confirmation"):
                required_step_no = step_no
                required_tool_name = step.get("tool_name")
                details = step.get("confirmation_details")
                if isinstance(details, dict):
                    required_confirmation_details = details
                break

    plan["confirmation"] = {
        "required_step_no": required_step_no,
        "required_tool_name": required_tool_name,
        "confirmation_reason": (
            required_confirmation_details.get("reason")
            if isinstance(required_confirmation_details, dict)
            else None
        ),
        "confirmation_details": required_confirmation_details,
        "approved": request.approved,
        "comment": request.comment,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    if (
        request.approved
        and required_tool_name == "file_reader"
        and isinstance(required_confirmation_details, dict)
        and required_confirmation_details.get("reason")
        == CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS
        and required_confirmation_details.get("resolved_path")
    ):
        plan["confirmation"]["approved_file_reader_paths"] = [
            required_confirmation_details["resolved_path"]
        ]
        plan["confirmation"]["confirmation_scope"] = "single_file_path"
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

    store.update_agent_run_status(db, run_id, "pending", None)
    summary = run_task_by_mode(db, run_id)
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


@router.get("/{run_id}/evidence", response_model=EvidenceBundleResponse)
async def get_task_evidence(
    run_id: str,
    db: Session = Depends(get_db),
) -> EvidenceBundleResponse:
    """Return grouped research evidence derived from persisted traces."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    traces = store.list_tool_traces(db, run_id)
    bundle = build_evidence_bundle(run, _parse_run_plan(run), [], traces)
    return EvidenceBundleResponse(**bundle.to_dict())


@router.get("/{run_id}/evidence/export", response_model=EvidenceExportResponse)
async def export_task_evidence(
    run_id: str,
    format: str = Query(default="json", pattern="^(json|jsonl|markdown|md)$"),
    db: Session = Depends(get_db),
) -> EvidenceExportResponse:
    """Export grouped evidence to a local artifact under workspace/exports."""

    result = _export_run_evidence(db, run_id, format)
    return EvidenceExportResponse(**result.to_dict())


@router.get("/{run_id}/evidence/export/content", response_model=EvidenceExportContentResponse)
async def export_task_evidence_content(
    run_id: str,
    format: str = Query(default="json", pattern="^(json|jsonl|markdown|md)$"),
    db: Session = Depends(get_db),
) -> EvidenceExportContentResponse:
    """Export evidence and return a safe preview/download payload."""

    result = _export_run_evidence(db, run_id, format)
    content = read_export_text(result.export_path)
    return EvidenceExportContentResponse(
        **result.to_dict(),
        content=content,
        content_type=export_media_type(result.format),
    )


@router.get("/{run_id}/evidence/export/download")
async def download_task_evidence_export(
    run_id: str,
    format: str = Query(default="json", pattern="^(json|jsonl|markdown|md)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Export evidence and return the artifact as a file download."""

    result = _export_run_evidence(db, run_id, format)
    export_path = resolve_export_path(result.export_path)
    return FileResponse(
        path=export_path,
        media_type=export_media_type(result.format),
        filename=export_filename(run_id, result.format),
    )
