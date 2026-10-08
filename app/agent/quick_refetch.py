"""Bounded Quick-mode fetches from sources discovered by this run."""

from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.evidence.normalizers import canonicalize_url


def select_pending_candidates(
    traces: Iterable[Any],
    *,
    max_total_candidates: int,
    remaining_rounds: int = 1,
    batch_size: int | None = None,
) -> list[str]:
    """Select undispatched URLs from this run's discovery traces.

    URLs are only eligible when a discovery trace recorded them. Every URL
    already submitted to ``web_fetcher`` is excluded, including failed
    attempts, so refetch rounds move across candidates instead of repeating
    transport failures or already-successful sources.
    """

    discovery_urls: list[str] = []
    attempted_urls: set[str] = set()
    requested_urls: set[str] = set()
    for trace in traces:
        output = _json_object(getattr(trace, "output_json", None))
        if str(getattr(trace, "tool_name", "")) in {
            "tavily_search",
            "mcp_github_search",
            "arxiv_search",
            "semantic_scholar_search",
            "openalex_search",
            "crossref_search",
        } and str(getattr(trace, "status", "")) == "success":
            # ``discovery_candidates`` is emitted by source intake and retains
            # deferred results after the selected ``results`` list is reduced.
            # ``fetch_candidates`` keeps compatibility with older traces.
            for item in output.get("discovery_candidates") or []:
                if isinstance(item, dict):
                    discovery_urls.append(_canonical(item.get("url")))
            for value in output.get("fetch_candidates") or []:
                discovery_urls.append(_canonical(value))
        if str(getattr(trace, "tool_name", "")) == "web_fetcher":
            request = _json_object(getattr(trace, "input_json", None))
            request_urls = request.get("urls") or (request.get("args") or {}).get("urls")
            deferred = {_canonical(p.get("url")) for p in output.get("pages") or [] if isinstance(p, dict)
                        and (p.get("error_code") or p.get("error")) == "batch_deadline_exceeded"}
            if isinstance(request_urls, list):
                for value in request_urls:
                    canonical = _canonical(value)
                    if canonical and canonical not in deferred:
                        requested_urls.add(canonical)
                        attempted_urls.add(canonical)
            # Record redirects as attempted identities too. This avoids
            # fetching a candidate that is the successful final URL of a
            # previously requested source.
            for page in output.get("pages") or []:
                if isinstance(page, dict) and _canonical(page.get("url")) not in deferred:
                    attempted_urls.add(_canonical(page.get("url") or page.get("requested_url")))
                    attempted_urls.add(_canonical(page.get("final_url")))

    unique = list(dict.fromkeys(url for url in discovery_urls if url))
    pending = [url for url in unique if url not in attempted_urls]
    remaining_total = max(0, int(max_total_candidates) - len(requested_urls))
    if batch_size is None:
        rounds_left = max(1, int(remaining_rounds))
        batch_size = (remaining_total + rounds_left - 1) // rounds_left
    limit = min(max(0, int(batch_size)), remaining_total)
    return pending[:limit]


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    try:
        normalized = canonicalize_url(raw)
    except (TypeError, ValueError):
        return ""
    parsed = urlsplit(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else ""
