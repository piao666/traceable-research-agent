"""Read-only Semantic Scholar academic paper search tool.

Free, no API key required for basic usage (100 req/5min).
Optional API key via SEMANTIC_SCHOLAR_API_KEY env var for higher rate limits.
Uses the Semantic Scholar Academic Graph API.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.config import settings
from app.tools.base import ToolResult

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = "title,authors,year,venue,abstract,citationCount,externalIds,publicationTypes,openAccessPdf,fieldsOfStudy"


def _bounded_limit(value: Any, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_paper(item: dict[str, Any]) -> dict[str, Any]:
    authors = [
        author.get("name", "") for author in item.get("authors") or []
    ]
    external_ids = item.get("externalIds") or {}
    open_access = item.get("openAccessPdf") or {}

    return {
        "paperId": item.get("paperId"),
        "title": item.get("title", ""),
        "authors": authors,
        "year": item.get("year"),
        "venue": item.get("venue", ""),
        "abstract": (item.get("abstract") or "")[:600],
        "citationCount": item.get("citationCount", 0),
        "externalIds": {
            "DOI": external_ids.get("DOI"),
            "ArXiv": external_ids.get("ArXiv"),
            "MAG": external_ids.get("MAG"),
        },
        "publicationTypes": item.get("publicationTypes") or [],
        "fieldsOfStudy": item.get("fieldsOfStudy") or [],
        "openAccessUrl": open_access.get("url"),
        "url": f"https://api.semanticscholar.org/{item.get('paperId', '')}",
    }


def semantic_scholar_handler(arguments: dict[str, Any]) -> ToolResult:
    """Search Semantic Scholar for academic papers matching a query."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error_message="semantic_scholar_search requires a non-empty 'query' parameter.",
            metadata={"error_type": "invalid_request"},
        )

    limit = _bounded_limit(arguments.get("limit"), 5, 1, 20)
    fields = str(arguments.get("fields") or DEFAULT_FIELDS).strip()

    params = {
        "query": query,
        "limit": limit,
        "fields": fields,
    }
    url = f"{S2_API_BASE}/paper/search?{urlencode(params)}"

    headers = {"Accept": "application/json"}
    # Optional API key for higher rate limits
    api_key = (getattr(settings, "semantic_scholar_api_key", None)
               or "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return ToolResult(
            success=False,
            error_message=f"Semantic Scholar API HTTP error: {exc.code}",
            metadata={"error_type": "provider_error", "http_status": exc.code},
        )
    except (URLError, TimeoutError) as exc:
        return ToolResult(
            success=False,
            error_message=f"Semantic Scholar API network error: {exc}",
            metadata={"error_type": "timeout"},
        )
    except json.JSONDecodeError as exc:
        return ToolResult(
            success=False,
            error_message=f"Semantic Scholar returned invalid JSON: {exc}",
            metadata={"error_type": "invalid_result"},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"Semantic Scholar API unexpected error: {exc}",
            metadata={"error_type": "internal_error"},
        )

    papers = [_parse_paper(item) for item in data.get("data") or []]
    total = data.get("total", 0)

    return ToolResult(
        success=True,
        output={
            "papers": papers,
            "total": total,
            "returned": len(papers),
            "query": query,
            "source": "semantic_scholar",
        },
        output_summary=f"Semantic Scholar: {len(papers)} papers returned of {total} total",
        metadata={
            "tool_name": "semantic_scholar_search",
            "read_only": True,
            "data_source": "semantic_scholar_api",
            "result_count": len(papers),
            "total_results": total,
        },
    )
