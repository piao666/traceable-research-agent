"""Mock task endpoints for the Day 1-3 skeleton."""

from uuid import uuid4

from fastapi import APIRouter

from app.schemas import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskCreateResponse)
async def create_task(request: TaskCreateRequest) -> TaskCreateResponse:
    """Accept a task and return mock run links without executing an agent."""

    run_id = f"mock-{uuid4().hex[:12]}"
    return TaskCreateResponse(
        run_id=run_id,
        status="pending",
        status_url=f"/api/tasks/{run_id}",
        trace_url=f"/api/tasks/{run_id}/trace",
        report_url=f"/api/reports/{run_id}",
    )


@router.get("/{run_id}", response_model=TaskStatusResponse)
async def get_task_status(run_id: str) -> TaskStatusResponse:
    """Return a mock status payload until database-backed runs are added."""

    return TaskStatusResponse(
        run_id=run_id,
        status="pending",
        current_step=0,
        total_steps=0,
        report_path=None,
        error_message=None,
        message="Mock task status. Persistent agent_runs will be added in Day 4-5.",
    )
