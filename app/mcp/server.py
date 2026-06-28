"""HTTP and JSON-RPC foundation for exposing read-only MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.mcp.policy import is_tool_exposable, mcp_policy_metadata
from app.mcp.schemas import (
    MCPJsonRpcRequest,
    MCPJsonRpcResponse,
    MCPToolCallRequest,
    MCPToolCallResponse,
    MCPToolListResponse,
    MCPToolMetadata,
    MCPTraceOptions,
)
from app.security import require_api_key, require_request_context
from app.tools.base import RiskLevel, ToolResult, ToolSpec
from app.tools.registry import execute_tool, get_tool, list_tools
from app.trace import store
from app.trace.logger import record_tool_result
from app.trace.models import ToolTrace


PROTOCOL_VERSION = "2024-11-05"
ROOT = Path(__file__).resolve().parents[2]
TOOL_ALIASES = {
    "sql_query_readonly": "sql_query",
    "github_search": "mcp_github_search",
}

TRACE_READER_SPEC = ToolSpec(
    name="trace_reader",
    description="Read persisted trace rows for one agent run.",
    input_schema={"run_id": "string", "limit": "integer|null", "status": "string|null"},
    output_schema={"run_id": "string", "traces": "array", "trace_count": "integer"},
    risk_level=RiskLevel.LOW,
    tags=["trace", "read-only"],
)
REPORT_READER_SPEC = ToolSpec(
    name="report_reader",
    description="Read the generated Markdown report for one agent run.",
    input_schema={"run_id": "string"},
    output_schema={"run_id": "string", "markdown": "string", "exists": "boolean"},
    risk_level=RiskLevel.LOW,
    tags=["report", "read-only"],
)

router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)


def _mcp_name(local_name: str) -> str:
    for alias, mapped_name in TOOL_ALIASES.items():
        if mapped_name == local_name:
            return alias
    return local_name


def _resolve_local_tool_name(name: str) -> str:
    return TOOL_ALIASES.get(name, name)


def _metadata_from_spec(spec: ToolSpec, *, alias: str | None = None) -> MCPToolMetadata:
    policy = mcp_policy_metadata(spec, alias=alias)
    return MCPToolMetadata(
        name=policy["name"],
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        read_only=policy["read_only"],
        side_effect_free=policy["side_effect_free"],
        requires_confirmation=policy["requires_confirmation"],
        risk_level=policy["risk_level"],
        enabled=spec.enabled,
        tags=spec.tags,
        local_tool_name=policy["local_tool_name"],
    )


def _exposed_tool_specs() -> list[MCPToolMetadata]:
    exposed: list[MCPToolMetadata] = []
    for spec in list_tools():
        if spec.name == "report_writer":
            continue
        alias = _mcp_name(spec.name)
        if is_tool_exposable(spec, alias=alias):
            exposed.append(_metadata_from_spec(spec, alias=alias))
    exposed.append(_metadata_from_spec(TRACE_READER_SPEC))
    exposed.append(_metadata_from_spec(REPORT_READER_SPEC))
    return sorted(exposed, key=lambda item: item.name)


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _trace_payload(trace: ToolTrace) -> dict[str, Any]:
    output: Any = None
    if trace.output_json:
        try:
            output = json.loads(trace.output_json)
        except json.JSONDecodeError:
            output = trace.output_json
    return {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "step_no": trace.step_no,
        "tool_name": trace.tool_name,
        "status": trace.status,
        "input_summary": trace.input_summary,
        "output_summary": trace.output_summary,
        "latency_ms": trace.latency_ms,
        "error_message": trace.error_message,
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
        "finished_at": trace.finished_at.isoformat() if trace.finished_at else None,
        "output": output,
    }


def _read_traces(db: Session, arguments: dict[str, Any]) -> ToolResult:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        return ToolResult(
            success=False,
            error_message="Missing required argument: run_id.",
            metadata={"tool_name": "trace_reader", "error_type": "invalid_args"},
        )
    run = store.get_agent_run(db, run_id)
    if run is None:
        return ToolResult(
            success=False,
            error_message="Task run not found.",
            metadata={"tool_name": "trace_reader", "error_type": "not_found"},
        )
    status_filter = arguments.get("status")
    traces = store.list_tool_traces(db, run_id)
    if isinstance(status_filter, str) and status_filter.strip():
        traces = [trace for trace in traces if trace.status == status_filter.strip()]
    try:
        limit = int(arguments.get("limit")) if arguments.get("limit") is not None else None
    except (TypeError, ValueError):
        limit = None
    if limit is not None:
        traces = traces[: max(1, min(limit, 500))]
    payload = [_trace_payload(trace) for trace in traces]
    return ToolResult(
        success=True,
        output={"run_id": run_id, "trace_count": len(payload), "traces": payload},
        output_summary=f"trace_reader returned {len(payload)} trace rows.",
        metadata={
            "tool_name": "trace_reader",
            "read_only": True,
            "side_effect_free": True,
            "result_count": len(payload),
        },
    )


def _read_report(db: Session, arguments: dict[str, Any]) -> ToolResult:
    run_id = str(arguments.get("run_id") or "").strip()
    if not run_id:
        return ToolResult(
            success=False,
            error_message="Missing required argument: run_id.",
            metadata={"tool_name": "report_reader", "error_type": "invalid_args"},
        )
    run = store.get_agent_run(db, run_id)
    if run is None:
        return ToolResult(
            success=False,
            error_message="Task run not found.",
            metadata={"tool_name": "report_reader", "error_type": "not_found"},
        )
    if not run.report_path:
        return ToolResult(
            success=True,
            output={"run_id": run_id, "exists": False, "markdown": "", "report_path": None},
            output_summary="Report has not been generated yet.",
            metadata={"tool_name": "report_reader", "read_only": True, "side_effect_free": True},
        )
    report_path = Path(run.report_path)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    if not report_path.exists() or not report_path.is_file():
        return ToolResult(
            success=True,
            output={"run_id": run_id, "exists": False, "markdown": "", "report_path": run.report_path},
            output_summary="Report path is recorded but the file is missing.",
            metadata={
                "tool_name": "report_reader",
                "read_only": True,
                "side_effect_free": True,
                "error_type": "missing_report_file",
            },
        )
    markdown = report_path.read_text(encoding="utf-8")
    return ToolResult(
        success=True,
        output={
            "run_id": run_id,
            "exists": True,
            "markdown": markdown,
            "report_path": run.report_path,
        },
        output_summary=f"report_reader returned {len(markdown)} markdown chars.",
        metadata={"tool_name": "report_reader", "read_only": True, "side_effect_free": True},
    )


def _execute_mcp_tool(db: Session, request: MCPToolCallRequest) -> MCPToolCallResponse:
    local_name = _resolve_local_tool_name(request.name)
    spec = get_tool(local_name)
    custom_reader = local_name in {"trace_reader", "report_reader"}
    if not custom_reader:
        if spec is None:
            raise HTTPException(status_code=404, detail="MCP tool not found")
        if not is_tool_exposable(spec, alias=request.name):
            raise HTTPException(status_code=403, detail="MCP tool is not exposed by read-only policy")

    if request.trace and request.trace.run_id:
        run = store.get_agent_run(db, request.trace.run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Trace run not found")

    started = perf_counter()
    if local_name == "trace_reader":
        result = _read_traces(db, request.arguments)
    elif local_name == "report_reader":
        result = _read_report(db, request.arguments)
    else:
        result = execute_tool(local_name, request.arguments)
    latency_ms = int((perf_counter() - started) * 1000)

    metadata = dict(result.metadata or {})
    metadata.update(
        {
            "mcp_server": settings.service_name,
            "mcp_tool_name": request.name,
            "local_tool_name": local_name,
            "read_only": True,
            "side_effect_free": True,
            "requires_confirmation": False,
            "risk_level": (spec.risk_level.value if spec else "low"),
            "latency_ms": latency_ms,
        }
    )
    traced_result = ToolResult(
        success=result.success,
        output=result.output,
        output_summary=result.output_summary,
        error_message=result.error_message,
        metadata=metadata,
    )

    if request.trace and request.trace.run_id:
        record_tool_result(
            db=db,
            run_id=request.trace.run_id,
            step_no=request.trace.step_no,
            tool_name=request.name,
            input_data=request.arguments,
            result=traced_result,
            latency_ms=latency_ms,
        )

    return MCPToolCallResponse(
        success=traced_result.success,
        output=traced_result.output,
        output_summary=traced_result.output_summary,
        error_message=traced_result.error_message,
        metadata=traced_result.metadata,
    )


@router.get("/health")
async def mcp_health() -> dict[str, Any]:
    """Return MCP server readiness and policy summary."""

    return {
        "status": "ok",
        "server": settings.service_name,
        "protocol_version": PROTOCOL_VERSION,
        "read_only": True,
        "write_operations_allowed": False,
        "tool_count": len(_exposed_tool_specs()),
    }


@router.get("/tools", response_model=MCPToolListResponse)
async def list_mcp_tools() -> MCPToolListResponse:
    """List read-only MCP tools discoverable by external clients."""

    return MCPToolListResponse(
        server=settings.service_name,
        protocol_version=PROTOCOL_VERSION,
        tools=_exposed_tool_specs(),
    )


@router.post("/tools/call", response_model=MCPToolCallResponse)
async def call_mcp_tool(
    request: MCPToolCallRequest,
    db: Session = Depends(get_db),
) -> MCPToolCallResponse:
    """Call one exposed read-only MCP tool."""

    return _execute_mcp_tool(db, request)


@router.post("", response_model=MCPJsonRpcResponse)
async def mcp_json_rpc(
    request: MCPJsonRpcRequest,
    db: Session = Depends(get_db),
) -> MCPJsonRpcResponse:
    """Serve a small JSON-RPC subset compatible with MCP clients."""

    try:
        if request.method == "initialize":
            return MCPJsonRpcResponse(
                id=request.id,
                result={
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": settings.service_name, "version": "0.1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            )
        if request.method == "tools/list":
            tools = [tool.model_dump(by_alias=True) for tool in _exposed_tool_specs()]
            return MCPJsonRpcResponse(id=request.id, result={"tools": _jsonable(tools)})
        if request.method == "tools/call":
            params = request.params or {}
            trace_options = params.get("_trace") if isinstance(params.get("_trace"), dict) else None
            call_request = MCPToolCallRequest(
                name=str(params.get("name") or ""),
                arguments=params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
                trace=MCPTraceOptions(**trace_options) if trace_options else None,
            )
            result = _execute_mcp_tool(db, call_request)
            return MCPJsonRpcResponse(
                id=request.id,
                result={
                    "content": [
                        {
                            "type": "json",
                            "json": _jsonable(
                                {
                                    "success": result.success,
                                    "output": result.output,
                                    "output_summary": result.output_summary,
                                    "error_message": result.error_message,
                                    "metadata": result.metadata,
                                }
                            ),
                        }
                    ],
                    "isError": not result.success,
                },
            )
        return MCPJsonRpcResponse(
            id=request.id,
            error={"code": -32601, "message": f"Unknown MCP method: {request.method}"},
        )
    except HTTPException as exc:
        return MCPJsonRpcResponse(
            id=request.id,
            error={"code": exc.status_code, "message": str(exc.detail)},
        )
