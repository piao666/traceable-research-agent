"""Default Tool Registry metadata for Phase 1."""

from app.tools.base import RiskLevel, ToolSpec
from app.tools.registry import register_tool


def register_default_tools() -> None:
    """Register metadata-only default tools.

    Handlers are intentionally omitted in Day5. Calling `execute_tool` for these
    tools returns a not-implemented ToolResult.
    """

    register_tool(
        ToolSpec(
            name="file_reader",
            description="Read allowed local files under workspace/docs.",
            input_schema={"path": "string", "max_chars": "integer"},
            output_schema={"content": "string", "source_path": "string"},
            risk_level=RiskLevel.LOW,
            tags=["local", "file", "read-only"],
        )
    )
    register_tool(
        ToolSpec(
            name="sql_query",
            description=(
                "Run read-only SQL queries against workspace demo database. "
                "Only SELECT/WITH will be allowed when the real safety check is implemented."
            ),
            input_schema={"query": "string", "limit": "integer"},
            output_schema={"rows": "array", "row_count": "integer"},
            risk_level=RiskLevel.MEDIUM,
            tags=["database", "sql", "read-only"],
        )
    )
    register_tool(
        ToolSpec(
            name="rag_search",
            description="Search local vector index and return top-k chunks.",
            input_schema={"query": "string", "top_k": "integer"},
            output_schema={"hits": "array"},
            risk_level=RiskLevel.LOW,
            tags=["rag", "search", "read-only"],
        )
    )
    register_tool(
        ToolSpec(
            name="mcp_github_search",
            description=(
                "Search GitHub repository information through a read-only "
                "MCP/GitHub adapter."
            ),
            input_schema={"query": "string", "repo": "string", "limit": "integer"},
            output_schema={"results": "array"},
            risk_level=RiskLevel.MEDIUM,
            tags=["github", "mcp", "read-only"],
        )
    )
    register_tool(
        ToolSpec(
            name="report_writer",
            description="Generate a Markdown report from collected observations and evidence.",
            input_schema={"run_id": "string", "observations": "array"},
            output_schema={"markdown": "string", "report_path": "string"},
            risk_level=RiskLevel.LOW,
            tags=["report", "markdown"],
        )
    )
