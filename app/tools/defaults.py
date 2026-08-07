"""Default Tool Registry metadata and Phase 2 handlers."""

from app.tools.base import RiskLevel, ToolSpec
from app.tools.arxiv_search import arxiv_search_handler
from app.tools.file_reader import read_file
from app.tools.mcp_github import github_search_handler
from app.tools.registry import register_tool
from app.tools.semantic_scholar import semantic_scholar_handler
from app.tools.sql_query import run_query
from app.tools.tavily_search import tavily_search_handler
from app.tools.web_fetcher import web_fetch


def _memory_search_handler(arguments: dict) -> "ToolResult":
    """Lazy-import wrapper so defaults.py does not eagerly depend on app.memory."""
    from app.memory.retriever import memory_search_handler

    return memory_search_handler(arguments)


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
            name="memory_search",
            description=(
                "Search local cross-session memory for relevant preferences, "
                "facts, and research history. Read-only and single-instance."
            ),
            input_schema={"query": "string", "top_k": "integer"},
            output_schema={"memories": "array", "recalled": "integer"},
            risk_level=RiskLevel.LOW,
            tags=["memory", "search", "read-only"],
        ),
        handler=_memory_search_handler,
    )
    register_tool(
        ToolSpec(
            name="web_fetcher",
            description=(
                "Fetch full-text content from a list of URLs using multi-level extraction. "
                "Phase 8.2: trafilatura → BeautifulSoup → raw regex fallback chain. "
                "PDF URLs are routed to pdf_reader. Read-only, offline-capable. "
                "Each page is tagged with content_basis (full_text/partial/snippet_only), "
                "extraction_method, and extraction_confidence."
            ),
            input_schema={
                "urls": "list[string]",
                "max_chars": "integer",
                "timeout_seconds": "integer",
            },
            output_schema={
                "pages": "array",
                "fetched_count": "integer",
                "failed_count": "integer",
                "total_count": "integer",
            },
            risk_level=RiskLevel.LOW,
            tags=["web", "fetch", "read-only"],
        ),
        handler=web_fetch,
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
    register_tool(
        ToolSpec(
            name="arxiv_search",
            description=(
                "Search academic papers on arXiv. Free, no API key required. "
                "Read-only, returns paper metadata (title, authors, abstract, categories, PDF link)."
            ),
            input_schema={"query": "string", "max_results": "integer", "sort_by": "string", "sort_order": "string"},
            output_schema={"papers": "array", "total_results": "integer", "returned": "integer"},
            risk_level=RiskLevel.LOW,
            tags=["academic", "arxiv", "read-only"],
        ),
        handler=arxiv_search_handler,
    )
    register_tool(
        ToolSpec(
            name="semantic_scholar_search",
            description=(
                "Search academic papers via Semantic Scholar Academic Graph API. "
                "Free, optional API key for higher rate limits. "
                "Read-only, returns paper metadata with citation counts."
            ),
            input_schema={"query": "string", "limit": "integer", "fields": "string"},
            output_schema={"papers": "array", "total": "integer", "returned": "integer"},
            risk_level=RiskLevel.LOW,
            tags=["academic", "semantic-scholar", "read-only"],
        ),
        handler=semantic_scholar_handler,
    )
