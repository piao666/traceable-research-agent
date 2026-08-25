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
from app.agent.planner import plan_task, plan_task_for_review
from app.agent.plan_guardrails import normalize_plan_arguments
from app.agent.state import WAITING_HUMAN_PLAN
from app.config import settings
from app.database import SessionLocal, get_db
from app.evidence.service import get_provenance_bundle, materialize_execution_provenance
from app.evidence.reasoning_service import materialize_reasoning
from app.schemas import (
    AsyncRunResponse,
    EvidenceBundleResponse,
    EvidenceExportContentResponse,
    EvidenceExportResponse,
    PlanApproveRequest,
    PlanReviewResponse,
    PlanReviewStep,
    ProvenanceBundleResponse,
    TaskCancelRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
    TaskListItem,
    TaskListResponse,
    TaskPlanResponse,
    TaskRetryRequest,
    TaskRunResponse,
    TaskStatusResponse,
    ToolTraceResponse,
)
from app.security import require_api_key
from app.trace import store
from app.trace.models import AgentRun, ToolTrace

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_api_key)],
)


def _record_memory_recall_trace(
    db: Session,
    run_id: str,
    memory_recall_trace: dict[str, Any] | None,
) -> None:
    if not isinstance(memory_recall_trace, dict):
        return
    if memory_recall_trace.get("event_type") != "memory_recall":
        return
    from app.trace.logger import record_trace_event

    record_trace_event(
        db=db,
        run_id=run_id,
        step_no=0,
        tool_name="memory_recall",
        status="success",
        input_data={},
        output_summary=(
            f"Memory recall: {memory_recall_trace.get('recalled', 0)} recalled"
            + (
                f", reason={memory_recall_trace.get('reason')}"
                if memory_recall_trace.get("reason")
                else ""
            )
        ),
        output_data={
            "recalled": memory_recall_trace.get("recalled", 0),
            "injected_chars": memory_recall_trace.get("injected_chars", 0),
            "memory_ids": memory_recall_trace.get("memory_ids", []),
            "reason": memory_recall_trace.get("reason"),
        },
    )


def _merge_approved_steps(
    original_steps: list[Any],
    modified_steps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Apply argument edits while preserving planner-owned step metadata."""
    originals = {
        int(step.get("step_no") or 0): step
        for step in original_steps
        if isinstance(step, dict) and int(step.get("step_no") or 0) > 0
    }
    if modified_steps is None:
        return [dict(step) for step in original_steps if isinstance(step, dict)]
    if not modified_steps:
        raise HTTPException(status_code=422, detail="An approved plan must contain at least one step")

    selected: list[tuple[int, dict[str, Any]]] = []
    seen_step_nos: set[int] = set()
    for requested in modified_steps:
        if not isinstance(requested, dict):
            raise HTTPException(status_code=422, detail="Every modified step must be an object")
        try:
            original_step_no = int(requested.get("step_no") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Every modified step needs a valid step_no") from exc
        if original_step_no in seen_step_nos:
            raise HTTPException(status_code=422, detail=f"Duplicate step_no: {original_step_no}")
        original = originals.get(original_step_no)
        if original is None:
            raise HTTPException(status_code=422, detail=f"Unknown step_no: {original_step_no}")
        requested_tool = str(requested.get("tool_name") or original.get("tool_name") or "")
        original_tool = str(original.get("tool_name") or "")
        if requested_tool != original_tool:
            raise HTTPException(
                status_code=422,
                detail=f"Plan approval cannot change tool_name for step {original_step_no}",
            )
        arguments = requested.get("arguments", original.get("arguments") or {})
        if not isinstance(arguments, dict):
            raise HTTPException(
                status_code=422,
                detail=f"Step {original_step_no} arguments must be an object",
            )
        merged = dict(original)
        merged["arguments"] = arguments
        selected.append((original_step_no, merged))
        seen_step_nos.add(original_step_no)

    selected.sort(key=lambda item: item[0])
    step_no_map = {old_step_no: index for index, (old_step_no, _) in enumerate(selected, 1)}
    result: list[dict[str, Any]] = []
    for new_step_no, (old_step_no, step) in enumerate(selected, 1):
        dependency = step.get("arguments_from")
        if isinstance(dependency, dict) and dependency.get("step_no") is not None:
            try:
                dependency_step_no = int(dependency["step_no"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Step {old_step_no} has an invalid arguments_from dependency",
                ) from exc
            if dependency_step_no not in step_no_map:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Step {old_step_no} depends on disabled step {dependency_step_no}; "
                        "enable the dependency or disable the dependent step"
                    ),
                )
            remapped_dependency = dict(dependency)
            remapped_dependency["step_no"] = step_no_map[dependency_step_no]
            step["arguments_from"] = remapped_dependency
        step["step_no"] = new_step_no
        result.append(step)
    return result


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
        citation_total=run.citation_total,
        citation_supported=run.citation_supported,
        citation_weakly_supported=run.citation_weakly_supported,
        citation_unsupported=run.citation_unsupported,
        citation_accuracy=run.citation_accuracy,
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


def _extract_plan_field(plan_json: str | None, field: str) -> str | None:
    """Extract a field from plan_json without full parse on failure."""
    if not plan_json:
        return None
    try:
        plan = json.loads(plan_json)
        return plan.get(field)
    except (json.JSONDecodeError, TypeError):
        return None


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
        token_in=trace.token_in,
        token_out=trace.token_out,
        estimated_cost=trace.estimated_cost,
        error_message=trace.error_message,
        created_at=trace.created_at,
        finished_at=trace.finished_at,
        output=output,
        metadata=_extract_trace_metadata(output),
        sub_query=trace.sub_query,
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
        "fallback_used",
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


def _persist_plan_config_snapshot(
    db: Session,
    run_id: str,
    safe_config: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    snapshot = dict(safe_config)
    snapshot.update(
        {
            "retrieval_profile": plan.get("retrieval_profile"),
            "source_policy_version": plan.get("policy_version"),
            "profile_constraints": plan.get("profile_constraints") or {},
        }
    )
    store.update_agent_run_config_snapshot(db, run_id, snapshot)


@router.post("", response_model=TaskCreateResponse)
def create_task(
    task_request: TaskCreateRequest,
    db: Session = Depends(get_db),
) -> TaskCreateResponse:
    """Accept a task, create a pending run, and persist a deterministic plan."""

    safe_config = settings.get_safe_runtime_config_summary()
    run_config_snapshot = json.dumps(
        safe_config,
        ensure_ascii=False,
        sort_keys=True,
    )
    run = store.create_agent_run(
        db=db,
        task=task_request.task,
        report_type=task_request.report_type,
        source_mode=task_request.source_mode,
        allowed_tools=task_request.allowed_tools,
        session_id=task_request.session_id,
        run_config_snapshot=run_config_snapshot,
    )

    # ── Phase 7.4: Plan approval mode ────────────────────────────────
    if task_request.require_plan_approval:
        plan = plan_task_for_review(
            task=task_request.task,
            allowed_tools=task_request.allowed_tools,
            source_mode=task_request.source_mode,
            scenario_template=task_request.scenario_template_key or task_request.scenario_template,
            execution_mode_override=task_request.execution_mode_override,
            skill_name=task_request.skill_name,
            retrieval_profile=task_request.retrieval_profile,
        )
        plan.setdefault("requested_execution_mode", plan.get("execution_mode") or settings.execution_mode)
        plan.setdefault("execution_mode", settings.execution_mode)
        memory_recall_trace = plan.pop("memory_recall_trace", None)
        run = store.update_agent_run_plan(db, run.run_id, plan)
        _persist_plan_config_snapshot(db, run.run_id, safe_config, plan)
        _record_memory_recall_trace(db, run.run_id, memory_recall_trace)
        run = store.update_agent_run_status(db, run.run_id, WAITING_HUMAN_PLAN, None)
        return TaskCreateResponse(
            run_id=run.run_id,
            status=run.status,
            status_url=f"/api/tasks/{run.run_id}",
            trace_url=f"/api/tasks/{run.run_id}/trace",
            report_url=f"/api/reports/{run.run_id}",
            plan_url=f"/api/tasks/{run.run_id}/plan",
            run_url=f"/api/tasks/{run.run_id}/run",
        )

    plan = plan_task(
        task=task_request.task,
        allowed_tools=task_request.allowed_tools,
        source_mode=task_request.source_mode,
        scenario_template=task_request.scenario_template_key or task_request.scenario_template,
        execution_mode_override=task_request.execution_mode_override,
        skill_name=task_request.skill_name,
        retrieval_profile=task_request.retrieval_profile,
    )
    plan.setdefault("requested_execution_mode", plan.get("execution_mode") or settings.execution_mode)
    plan.setdefault("execution_mode", settings.execution_mode)
    memory_recall_trace = plan.pop("memory_recall_trace", None)
    run = store.update_agent_run_plan(db, run.run_id, plan)
    _persist_plan_config_snapshot(db, run.run_id, safe_config, plan)

    # ── Phase 5: record memory_recall trace event ─────────────────
    _record_memory_recall_trace(db, run.run_id, memory_recall_trace)
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
def run_task(
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
    if run.status == "waiting_human_plan":
        return _task_run_response(
            _run_summary(run, "Plan is awaiting approval. Call POST /api/tasks/{run_id}/approve-plan.")
        )
    if run.status in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"{run.status.title()} runs cannot be rerun directly")

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
    if run.status == "waiting_human_plan":
        return _async_run_response(
            run,
            "Plan is awaiting approval. Call POST /api/tasks/{run_id}/approve-plan.",
        )
    if run.status in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"{run.status.title()} runs cannot be rerun directly")

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
def confirm_task(
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


# ── Phase 7.4: Plan approval endpoints ──────────────────────────────


@router.get("/{run_id}/review", response_model=PlanReviewResponse)
async def get_plan_review(
    run_id: str,
    db: Session = Depends(get_db),
) -> PlanReviewResponse:
    """Return the plan in a review-friendly format for the approval panel."""
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status != "waiting_human_plan":
        raise HTTPException(
            status_code=400,
            detail="Plan review is only available when status is waiting_human_plan",
        )
    if not run.plan_json:
        raise HTTPException(status_code=400, detail="Task run does not have a plan")

    try:
        plan = json.loads(run.plan_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Task run plan is invalid") from exc

    steps = plan.get("steps") or []
    review_steps: list[PlanReviewStep] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        review_steps.append(PlanReviewStep(
            step_no=int(step.get("step_no") or 0),
            tool_name=str(step.get("tool_name") or ""),
            goal=str(step.get("goal") or ""),
            arguments=step.get("arguments") or {},
            risk_level=str(step.get("risk_level") or "low"),
            requires_confirmation=bool(step.get("requires_confirmation")),
            estimated_tokens=int(step.get("estimated_tokens") or 500),
            raw_step=dict(step),
        ))

    return PlanReviewResponse(
        run_id=run.run_id,
        task=run.task,
        status=run.status,
        execution_mode=plan.get("execution_mode") or "planned",
        steps=review_steps,
        allowed_tools=plan.get("allowed_tools") or [],
        estimated_total_tokens=int(plan.get("estimated_total_tokens") or 0),
        estimated_cost=float(plan.get("estimated_cost") or 0.0),
        risk_summary=plan.get("risk_summary") or {"low": 0, "medium": 0, "high": 0},
        notes=plan.get("notes") or [],
    )


@router.post("/{run_id}/approve-plan", response_model=TaskRunResponse)
def approve_plan(
    run_id: str,
    request: PlanApproveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    start_async: bool = False,
) -> TaskRunResponse:
    """Approve or reject a plan that is waiting for human review.

    When start_async=True, the task is started in the background and the
    response returns immediately with status 'pending' instead of blocking
    until completion.
    """
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status != "waiting_human_plan":
        raise HTTPException(
            status_code=400,
            detail="Plan approval is only available when status is waiting_human_plan",
        )
    if not run.plan_json:
        raise HTTPException(status_code=400, detail="Task run does not have a plan")

    try:
        plan = json.loads(run.plan_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Task run plan is invalid") from exc

    if not request.approved:
        # ── Rejected: mark as failed ────────────────────────────────
        run = store.update_agent_run_status(
            db, run_id, "failed",
            f"Plan rejected by human: {request.comment or 'No comment'}",
        )
        # Record trace event for audit
        from app.trace.logger import record_trace_event
        record_trace_event(
            db=db, run_id=run_id, step_no=0,
            tool_name="plan_approval",
            status="rejected",
            input_data={"approved": False, "comment": request.comment},
            output_summary=f"Plan rejected: {request.comment or 'No comment'}",
            output_data={"approved": False, "comment": request.comment},
        )
        return _task_run_response(
            _run_summary(run, f"Plan rejected: {request.comment or 'No comment'}")
        )

    # ── Approved: apply modified arguments and re-run guardrails ─────
    if request.modified_steps is not None:
        plan["steps"] = _merge_approved_steps(
            list(plan.get("steps") or []),
            request.modified_steps,
        )
        plan = normalize_plan_arguments(plan, run.task, run.source_mode)
        plan["notes"] = list(plan.get("notes") or []) + [
            f"Plan modified during approval: {request.comment}" if request.comment
            else "Plan approved with modifications.",
        ]
        store.replace_agent_run_plan(db, run_id, plan)
    else:
        plan["notes"] = list(plan.get("notes") or []) + ["Plan approved without modifications."]
        store.replace_agent_run_plan(db, run_id, plan)

    # Record trace event
    from app.trace.logger import record_trace_event
    record_trace_event(
        db=db, run_id=run_id, step_no=0,
        tool_name="plan_approval",
        status="approved",
        input_data={
            "approved": True,
            "comment": request.comment,
            "modified": request.modified_steps is not None,
        },
        output_summary=f"Plan approved: {request.comment or 'No comment'}",
        output_data={
            "approved": True,
            "comment": request.comment,
            "step_count": len(plan.get("steps") or []),
        },
    )

    # Move to pending and execute
    store.update_agent_run_status(db, run_id, "pending", None)
    if start_async:
        # Reuse the same failure-aware background wrapper as ``run_async``.
        # It owns a fresh SQLAlchemy session and persists unexpected failures
        # instead of leaving the task indefinitely pending.
        background_tasks.add_task(_run_task_in_background, run_id)
        return _task_run_response(_run_summary(run, "Task started in background."))
    summary = run_task_by_mode(db, run_id)
    return _task_run_response(summary)


@router.get("/{run_id}/trace", response_model=list[ToolTraceResponse])
async def get_task_trace(
    run_id: str,
    db: Session = Depends(get_db),
) -> list[ToolTraceResponse]:
    """Return trace rows for a run."""

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


@router.get("/{run_id}/evidence/v2", response_model=ProvenanceBundleResponse)
async def get_task_provenance(
    run_id: str,
    db: Session = Depends(get_db),
) -> ProvenanceBundleResponse:
    """Return the materialized Claim-level provenance graph for a run."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    try:
        payload = get_provenance_bundle(db, run_id)
    except ValueError:
        traces = store.list_tool_traces(db, run_id)
        payload = materialize_execution_provenance(
            db,
            run,
            _parse_run_plan(run),
            [],
            traces,
            settings,
        )
        if payload is None:
            raise HTTPException(
                status_code=409,
                detail="Evidence Pipeline V2 is disabled by configuration",
            )
    if settings.evidence_reasoning_enabled:
        reasoning = materialize_reasoning(db, run_id, settings.source_policy_path)
        payload = get_provenance_bundle(
            db,
            run_id,
            reasoning_run_id=reasoning["reasoning"]["reasoning_run_id"],
        )
    return ProvenanceBundleResponse(**payload)


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


# ── Task list, cancel, retry ─────────────────────────────────────────


@router.get("", response_model=TaskListResponse)
def list_tasks(
    session_id: str | None = Query(None),
    status: str | None = Query(None),
    execution_mode: str | None = Query(None, pattern="^(planned|react)$"),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> TaskListResponse:
    """List tasks with optional filters and pagination."""
    runs = store.list_agent_runs(
        db,
        session_id=session_id,
        status=status,
        execution_mode=execution_mode,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )
    total = store.count_agent_runs(
        db,
        session_id=session_id,
        status=status,
        execution_mode=execution_mode,
        created_after=created_after,
        created_before=created_before,
    )
    mode_key = "execution_mode"
    return TaskListResponse(
        tasks=[
            TaskListItem(
                run_id=r.run_id,
                task=r.task,
                status=r.status,
                report_type=r.report_type,
                execution_mode=_extract_plan_field(r.plan_json, mode_key) or "planned",
                total_tool_calls=r.total_tool_calls,
                estimated_cost=r.estimated_cost,
                session_id=r.session_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{run_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(
    run_id: str,
    request: TaskCancelRequest | None = None,
    db: Session = Depends(get_db),
) -> TaskStatusResponse:
    """Cancel a running or pending task."""
    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status not in ("pending", "running", "waiting_human", "waiting_human_plan"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel task in status '{run.status}'")
    previous_status = run.status
    reason = (request.reason if request else "") or "Cancelled by user"
    run = store.update_agent_run_status(db, run_id, "cancelled", reason)
    from app.trace.logger import record_trace_event
    record_trace_event(
        db=db, run_id=run_id, step_no=0,
        tool_name="task_cancel",
        status="cancelled",
        input_data={"reason": reason},
        output_summary=reason,
        output_data={"reason": reason, "previous_status": previous_status},
    )
    return _task_status_response(run)


@router.post("/{run_id}/retry", response_model=TaskCreateResponse)
def retry_task(
    run_id: str,
    request: TaskRetryRequest | None = None,
    db: Session = Depends(get_db),
) -> TaskCreateResponse:
    """Retry a failed task, creating a new run with parent_run_id."""
    original = store.get_agent_run(db, run_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Original task run not found")
    if original.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Only failed or cancelled tasks can be retried (current status: '{original.status}')",
        )
    if request and request.from_failed_step:
        raise HTTPException(
            status_code=400,
            detail="Retry from the failed step is not supported safely; retry the full run instead.",
        )
    reuse_plan = request.reuse_plan if request else True
    try:
        allowed_tools = (
            json.loads(original.allowed_tools_json)
            if original.allowed_tools_json
            else None
        )
    except (json.JSONDecodeError, TypeError):
        allowed_tools = None
    plan = None
    if reuse_plan and original.plan_json:
        try:
            plan = json.loads(original.plan_json)
        except json.JSONDecodeError:
            plan = None
    if not isinstance(plan, dict):
        original_plan = _parse_run_plan(original)
        plan = plan_task(
            task=original.task,
            allowed_tools=allowed_tools,
            source_mode=original.source_mode,
            execution_mode_override=(
                original_plan.get("requested_execution_mode")
                or original_plan.get("execution_mode")
            ),
            skill_name="auto",
        )
    # A retry is a fresh execution.  Never inherit an approval token or a
    # partially consumed ReAct state from the original run.
    plan.pop("confirmation", None)
    plan.pop("react_state", None)
    plan["parent_run_id"] = run_id
    plan["notes"] = list(plan.get("notes") or []) + [
        f"Full retry of failed or cancelled run {run_id}."
    ]
    new_run = store.create_agent_run(
        db,
        task=original.task,
        report_type=original.report_type,
        source_mode=original.source_mode,
        allowed_tools=allowed_tools,
        session_id=original.session_id,
        run_config_snapshot=original.run_config_snapshot,
    )
    store.update_agent_run_plan(db, new_run.run_id, plan)
    return TaskCreateResponse(
        run_id=new_run.run_id,
        status=new_run.status,
        status_url=f"/api/tasks/{new_run.run_id}",
        trace_url=f"/api/tasks/{new_run.run_id}/trace",
        report_url=f"/api/reports/{new_run.run_id}",
        plan_url=f"/api/tasks/{new_run.run_id}/plan",
        run_url=f"/api/tasks/{new_run.run_id}/run",
    )
