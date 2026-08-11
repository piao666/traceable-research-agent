"""Read-only Crossref academic paper search tool.

Free (polite pool), no API key required. Rate limit: ~1 request per second.
Uses the Crossref REST API (https://api.crossref.org/works).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.tools.base import ToolResult

CROSSREF_API_URL = "https://api.crossref.org/works"


def _bounded_rows(value: Any, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_item(item: dict[str, Any]) -> dict[str, Any]:
    title_list = item.get("title") or []
    title = title_list[0] if title_list else ""

    author_objs = item.get("author") or []
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in author_objs
        if isinstance(a, dict)
    ]
    authors = [a for a in authors if a]

    published = item.get("published-print") or item.get("published-online") or {}
    date_parts = published.get("date-parts") or []
    year = date_parts[0][0] if date_parts and date_parts[0] else None

    container = item.get("container-title") or []
    venue = container[0] if container else None

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": item.get("DOI", ""),
        "publisher": item.get("publisher", ""),
        "venue": venue,
        "type": item.get("type", ""),
        "url": item.get("URL", ""),
        "issn": item.get("ISSN") or [],
        "abstract": (item.get("abstract") or "")[:600],
    }


def crossref_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Search Crossref for academic works matching a query."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error_message="crossref_search requires a non-empty 'query' parameter.",
            metadata={"error_type": "invalid_request"},
        )

    max_results = _bounded_rows(arguments.get("max_results"), 5, 1, 20)

    params = {
        "query": query,
        "rows": max_results,
    }
    url = f"{CROSSREF_API_URL}?{urlencode(params)}"

    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return ToolResult(
            success=False,
            error_message=f"Crossref API HTTP error: {exc.code}",
            metadata={"error_type": "provider_error", "http_status": exc.code},
        )
    except (URLError, TimeoutError) as exc:
        return ToolResult(
            success=False,
            error_message=f"Crossref API network error: {exc}",
            metadata={"error_type": "timeout"},
        )
    except json.JSONDecodeError as exc:
        return ToolResult(
            success=False,
            error_message=f"Crossref returned invalid JSON: {exc}",
            metadata={"error_type": "invalid_result"},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"Crossref API unexpected error: {exc}",
            metadata={"error_type": "internal_error"},
        )

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    items = message.get("items") if isinstance(message.get("items"), list) else []
    total = int(message.get("total-results", 0))

    papers = [_parse_item(item) for item in items]

    return ToolResult(
        success=True,
        output={
            "papers": papers,
            "total": total,
            "returned": len(papers),
            "query": query,
            "source": "crossref",
        },
        output_summary=f"Crossref: {len(papers)} papers returned of {total} total",
        metadata={
            "tool_name": "crossref_search",
            "read_only": True,
            "data_source": "crossref_api",
            "result_count": len(papers),
            "total_results": total,
        },
    )
