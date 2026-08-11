"""Read-only OpenAlex academic paper search tool.

Free, no API key required. Rate limit: ~10 requests per second (polite: 1 req/0.5s).
Uses the OpenAlex REST API (https://api.openalex.org/works).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.tools.base import ToolResult

OPENALEX_API_URL = "https://api.openalex.org/works"


def _bounded_limit(value: Any, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_paper(item: dict[str, Any]) -> dict[str, Any]:
    authorships = item.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if isinstance(a, dict)
    ]
    authors = [a.strip() for a in authors if a.strip()]

    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    open_access = item.get("open_access") or {}

    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "authors": authors,
        "year": item.get("publication_year"),
        "doi": item.get("doi"),
        "venue": source.get("display_name", ""),
        "cited_by_count": item.get("cited_by_count", 0),
        "is_open_access": bool(open_access.get("is_oa", False)),
        "type": item.get("type", ""),
        "cited_by_api_url": item.get("cited_by_api_url", ""),
    }


def openalex_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Search OpenAlex for academic works matching a query."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error_message="openalex_search requires a non-empty 'query' parameter.",
            metadata={"error_type": "invalid_request"},
        )

    max_results = _bounded_limit(arguments.get("max_results"), 5, 1, 20)
    sort = str(arguments.get("sort") or "relevance").strip()

    params: dict[str, Any] = {
        "search": query,
        "per_page": max_results,
        "sort": sort,
    }
    url = f"{OPENALEX_API_URL}?{urlencode(params)}"

    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return ToolResult(
            success=False,
            error_message=f"OpenAlex API HTTP error: {exc.code}",
            metadata={"error_type": "provider_error", "http_status": exc.code},
        )
    except (URLError, TimeoutError) as exc:
        return ToolResult(
            success=False,
            error_message=f"OpenAlex API network error: {exc}",
            metadata={"error_type": "timeout"},
        )
    except json.JSONDecodeError as exc:
        return ToolResult(
            success=False,
            error_message=f"OpenAlex returned invalid JSON: {exc}",
            metadata={"error_type": "invalid_result"},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"OpenAlex API unexpected error: {exc}",
            metadata={"error_type": "internal_error"},
        )

    papers = [_parse_paper(item) for item in data.get("results") or []]
    meta = data.get("meta") or {}
    total = int(meta.get("count", 0))
    per_page = int(meta.get("per_page", 0))

    return ToolResult(
        success=True,
        output={
            "papers": papers,
            "total": total,
            "returned": len(papers),
            "per_page": per_page,
            "query": query,
            "source": "openalex",
        },
        output_summary=f"OpenAlex: {len(papers)} papers returned of {total} total",
        metadata={
            "tool_name": "openalex_search",
            "read_only": True,
            "data_source": "openalex_api",
            "result_count": len(papers),
            "total_results": total,
        },
    )
