"""Tool Registry metadata and execution endpoints."""

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolInfo,
    ToolListResponse,
)
from app.security import require_api_key, require_request_context
from app.tools import registry
from app.tools.base import ToolResult, ToolSpec
from app.trace import store
from app.trace.logger import record_tool_result

router = APIRouter(
    prefix="/tools",
    tags=["tools"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)


def _tool_info(spec: ToolSpec) -> ToolInfo:
    return ToolInfo(
        name=spec.name,
        description=spec.description,
        risk_level=spec.risk_level.value,
        requires_confirmation=spec.requires_confirmation,
        enabled=spec.enabled,
        timeout_seconds=spec.timeout_seconds,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        tags=spec.tags,
    )


def _tool_execute_response(result: ToolResult) -> ToolExecuteResponse:
    return ToolExecuteResponse(
        success=result.success,
        output=result.output,
        output_summary=result.output_summary,
        error_message=result.error_message,
        metadata=result.metadata,
    )


@router.get("", response_model=ToolListResponse)
async def list_tools() -> ToolListResponse:
    """Return registered tool metadata."""

    return ToolListResponse(tools=[_tool_info(spec) for spec in registry.list_tools()])


@router.get("/{tool_name}", response_model=ToolInfo)
async def get_tool(tool_name: str) -> ToolInfo:
    """Return one registered tool or 404."""

    spec = registry.get_tool(tool_name)
    if spec is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return _tool_info(spec)


@router.post("/{tool_name}/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    tool_name: str,
    request: ToolExecuteRequest,
    db: Session = Depends(get_db),
) -> ToolExecuteResponse:
    """Execute a registry tool and optionally write one trace row.

    The optional `run_id`/`step_no` path exists for Day6-8 tool verification. It
    is not an Agent Executor implementation.
    """

    if request.run_id:
        run = store.get_agent_run(db, request.run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Task run not found")

    started = perf_counter()
    result = registry.execute_tool(tool_name, request.arguments)
    latency_ms = int((perf_counter() - started) * 1000)

    if request.run_id:
        record_tool_result(
            db=db,
            run_id=request.run_id,
            step_no=request.step_no,
            tool_name=tool_name,
            input_data=request.arguments or {},
            result=result,
            latency_ms=latency_ms,
        )
    return _tool_execute_response(result)
