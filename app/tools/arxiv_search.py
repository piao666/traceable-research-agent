"""Read-only arXiv academic paper search tool.

Free, no API key required. Rate limit: ~1 request per 3 seconds (polite).
Uses the arXiv API (http://export.arxiv.org/api/query).
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.config import settings
from app.tools.base import ToolResult

ARXIV_API_URL = "http://export.arxiv.org/api/query"
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _bounded_max(value: Any, default: int, minimum: int = 1, maximum: int = 30) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_paper(entry: ET.Element) -> dict[str, Any]:
    def _text(tag: str) -> str:
        el = entry.find(f"atom:{tag}", NAMESPACES)
        return (el.text or "").strip() if el is not None and el.text else ""

    def _arxiv_text(tag: str) -> str:
        el = entry.find(f"arxiv:{tag}", NAMESPACES)
        return (el.text or "").strip() if el is not None and el.text else ""

    authors = [
        (author.find("atom:name", NAMESPACES) or ET.Element("name")).text or ""
        for author in entry.findall("atom:author", NAMESPACES)
    ]
    authors = [a.strip() for a in authors if a.strip()]

    links = entry.findall("atom:link", NAMESPACES)
    pdf_url = ""
    abstract_url = ""
    for link in links:
        href = link.attrib.get("href", "")
        title_attr = link.attrib.get("title", "")
        rel = link.attrib.get("rel", "")
        if title_attr == "pdf" or "pdf" in rel:
            pdf_url = href
        elif not abstract_url and (rel == "alternate" or not rel):
            abstract_url = href

    published = _text("published")
    updated = _text("updated")

    return {
        "id": _text("id"),
        "title": _text("title"),
        "summary": _text("summary")[:600],
        "authors": authors,
        "published": published,
        "updated": updated,
        "abstract_url": abstract_url,
        "pdf_url": pdf_url,
        "primary_category": _arxiv_text("primary_category"),
        "categories": [c.attrib.get("term", "") for c in entry.findall("arxiv:category", NAMESPACES)],
        "comment": _arxiv_text("comment"),
    }


def arxiv_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Search arXiv for academic papers matching a query."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error_message="arxiv_search requires a non-empty 'query' parameter.",
            metadata={"error_type": "invalid_request"},
        )

    max_results = _bounded_max(arguments.get("max_results"), 5, 1, 30)

    params = {
        "search_query": f"all:{quote(query)}",
        "start": 0,
        "max_results": max_results,
        "sortBy": arguments.get("sort_by", "relevance"),
        "sortOrder": arguments.get("sort_order", "descending"),
    }
    url = f"{ARXIV_API_URL}?{urlencode(params)}"

    try:
        request = Request(url, headers={"Accept": "application/atom+xml"})
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        return ToolResult(
            success=False,
            error_message=f"arXiv API HTTP error: {exc.code}",
            metadata={"error_type": "provider_error", "http_status": exc.code},
        )
    except (URLError, TimeoutError) as exc:
        return ToolResult(
            success=False,
            error_message=f"arXiv API network error: {exc}",
            metadata={"error_type": "timeout"},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"arXiv API unexpected error: {exc}",
            metadata={"error_type": "internal_error"},
        )

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return ToolResult(
            success=False,
            error_message=f"arXiv returned invalid XML: {exc}",
            metadata={"error_type": "invalid_result"},
        )

    total_results = 0
    total_el = root.find("atom:totalResults", NAMESPACES) or root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    if total_el is not None and total_el.text:
        try:
            total_results = int(total_el.text)
        except (TypeError, ValueError):
            pass

    entries = root.findall("atom:entry", NAMESPACES)
    papers = [_parse_paper(entry) for entry in entries]

    return ToolResult(
        success=True,
        output={
            "papers": papers,
            "total_results": total_results,
            "returned": len(papers),
            "query": query,
            "source": "arxiv",
        },
        output_summary=f"arXiv: {len(papers)} papers returned of {total_results} total",
        metadata={
            "tool_name": "arxiv_search",
            "read_only": True,
            "data_source": "arxiv_api",
            "result_count": len(papers),
            "total_results": total_results,
        },
    )
