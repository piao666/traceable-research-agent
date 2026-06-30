"""Default Tool Registry metadata and Phase 2 handlers."""

from app.tools.base import RiskLevel, ToolSpec
from app.tools.file_reader import read_file
from app.tools.mcp_github import github_search_handler
from app.tools.rag_search import search_rag
from app.tools.registry import register_tool
from app.tools.sql_query import run_query
from app.tools.tavily_search import tavily_search_handler


def register_default_tools() -> None:
    """Register default tools, wiring implemented Phase 2 handlers."""

    register_tool(
        ToolSpec(
            name="file_reader",
            description=(
                "Read allowed local files under configured FILE_READER_ALLOWED_ROOTS. "
                "Paths outside allowed roots require per-file HITL approval during agent runs."
            ),
            input_schema={"path": "string", "max_chars": "integer"},
            output_schema={"content": "string", "source_path": "string"},
            risk_level=RiskLevel.LOW,
            tags=["local", "file", "read-only"],
        ),
        handler=read_file,
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
        ),
        handler=run_query,
    )
    register_tool(
        ToolSpec(
            name="rag_search",
            description="Search local evidence using dense, BM25, or read-only RRF hybrid retrieval.",
            input_schema={"query": "string", "top_k": "integer", "retrieval_mode": "dense|bm25|hybrid"},
            output_schema={"query": "string", "top_k": "integer", "hits": "array"},
            risk_level=RiskLevel.LOW,
            tags=["rag", "search", "read-only"],
        ),
        handler=search_rag,
    )
    register_tool(
        ToolSpec(
            name="mcp_github_search",
            description=(
                "Search real GitHub repositories or issues through a read-only "
                "Public API adapter. Mock mode is explicit/offline only."
            ),
            input_schema={
                "query": "string",
                "repo": "string|null",
                "limit": "integer",
                "mode": "mock|public_api",
                "search_type": "issues|repositories",
                "sort": "stars|updated|best_match",
                "order": "asc|desc",
            },
            output_schema={"query": "string", "repo": "string|null", "mode": "string", "results": "array"},
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            enabled=True,
            tags=["github", "mcp", "read-only"],
        ),
        handler=github_search_handler,
    )
    register_tool(
        ToolSpec(
            name="tavily_search",
            description="Search current external web sources through the real read-only Tavily API.",
            input_schema={
                "query": "string",
                "max_results": "integer",
                "search_depth": "basic|advanced",
                "include_answer": "boolean",
                "include_raw_content": "boolean",
            },
            output_schema={"query": "string", "answer": "string|null", "results": "array"},
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            enabled=True,
            tags=["tavily", "web", "search", "read-only"],
        ),
        handler=tavily_search_handler,
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
