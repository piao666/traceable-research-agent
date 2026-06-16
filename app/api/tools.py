"""Mock tool catalog endpoint for the Day 1-3 skeleton."""

from fastapi import APIRouter

from app.schemas import ToolInfo, ToolListResponse

router = APIRouter(prefix="/tools", tags=["tools"])


MOCK_TOOLS = [
    ToolInfo(
        name="file_reader",
        description="Read whitelisted local documents under workspace/docs.",
        risk_level="low",
        requires_confirmation=False,
        enabled=False,
        input_schema={"path": "string", "max_chars": "integer"},
        output_schema={"content": "string", "source_path": "string"},
    ),
    ToolInfo(
        name="sql_query",
        description="Run read-only SELECT/WITH queries against a demo database.",
        risk_level="medium",
        requires_confirmation=False,
        enabled=False,
        input_schema={"query": "string", "limit": "integer"},
        output_schema={"rows": "array", "row_count": "integer"},
    ),
    ToolInfo(
        name="rag_search",
        description="Search indexed document chunks and return traceable hits.",
        risk_level="low",
        requires_confirmation=False,
        enabled=False,
        input_schema={"query": "string", "top_k": "integer"},
        output_schema={"hits": "array"},
    ),
    ToolInfo(
        name="mcp_github_search",
        description="Read-only GitHub/MCP search placeholder for later phases.",
        risk_level="medium",
        requires_confirmation=False,
        enabled=False,
        input_schema={"query": "string", "repo": "string"},
        output_schema={"results": "array"},
    ),
    ToolInfo(
        name="report_writer",
        description="Generate Markdown reports from observations and evidence.",
        risk_level="low",
        requires_confirmation=False,
        enabled=False,
        input_schema={"run_id": "string", "observations": "array"},
        output_schema={"markdown": "string", "report_path": "string"},
    ),
]


@router.get("", response_model=ToolListResponse)
async def list_tools() -> ToolListResponse:
    """Return mock tool metadata without registering executable handlers."""

    return ToolListResponse(tools=MOCK_TOOLS)
