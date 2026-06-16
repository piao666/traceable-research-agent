"""Tool Registry metadata endpoints."""

from fastapi import APIRouter, HTTPException

from app.schemas import (
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolInfo,
    ToolListResponse,
)
from app.tools import registry
from app.tools.base import ToolResult, ToolSpec

router = APIRouter(prefix="/tools", tags=["tools"])


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
) -> ToolExecuteResponse:
    """Execute a registry stub without writing trace records."""

    result = registry.execute_tool(tool_name, request.arguments)
    return _tool_execute_response(result)
