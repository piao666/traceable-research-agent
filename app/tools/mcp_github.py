"""Read-only GitHub/MCP-style search adapter.

The default `mock` mode is deterministic and offline-safe. `public_api` mode is
best-effort and only uses GitHub read-only GET endpoints.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.tools.base import ToolResult


MAX_LIMIT = 10
DEFAULT_LIMIT = 5
VALID_MODES = {"mock", "public_api"}
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _normalize_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if limit < 1:
        return 1
    return min(limit, MAX_LIMIT)


def _validate(query: Any, repo: Any, mode: str) -> tuple[str | None, str | None, str | None]:
    if not isinstance(query, str) or not query.strip():
        return None, None, "Missing required argument: query."
    normalized_repo = None
    if repo is not None:
        if not isinstance(repo, str) or not REPO_PATTERN.match(repo):
            return None, None, "Invalid repo format. Expected owner/name."
        normalized_repo = repo
    if mode not in VALID_MODES:
        return None, None, f"Invalid mode '{mode}'. Expected mock or public_api."
    return query.strip(), normalized_repo, None


def _mock_results(query: str, repo: str | None, limit: int) -> list[dict[str, str]]:
    source = repo or "public-mock/github"
    base_url = f"https://github.com/{source}" if repo else "https://github.com/search"
    templates = [
        (
            "Trace-first tool execution design",
            "issue",
            "Tool calls should persist success, failed, and rejected states for interview review.",
        ),
        (
            "Read-only GitHub adapter safety boundary",
            "discussion",
            "The adapter supports search-style evidence collection without write operations.",
        ),
        (
            "Agent demo flow documentation",
            "code",
            "Create, inspect plan, run manually, inspect trace, and read generated report.",
        ),
        (
            "Evaluation case coverage",
            "issue",
            "Eval cases cover file, SQL, RAG, GitHub mock, HITL, and exception paths.",
        ),
        (
            "MCP compatibility notes",
            "doc",
            "The local adapter mirrors MCP tool semantics while keeping the MVP offline-safe.",
        ),
    ]
    results = []
    for index, (title, result_type, snippet) in enumerate(templates[:limit], start=1):
        results.append(
            {
                "title": title,
                "url": f"{base_url}#mock-result-{index}",
                "type": result_type,
                "source": source,
                "snippet": f"{snippet} Query: {query}",
            }
        )
    return results


def _public_api_results(query: str, repo: str | None, limit: int) -> ToolResult:
    search_query = query if repo is None else f"{query} repo:{repo}"
    url = f"https://api.github.com/search/issues?q={quote(search_query)}&per_page={limit}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "traceable-research-agent-read-only",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_type = "rate_limited" if exc.code in {403, 429} else "github_api_error"
        return ToolResult(
            success=False,
            error_message=f"GitHub public API request failed with HTTP {exc.code}.",
            metadata={
                "read_only": True,
                "mode": "public_api",
                "error_type": error_type,
                "repo": repo,
            },
        )
    except (URLError, TimeoutError) as exc:
        return ToolResult(
            success=False,
            error_message=f"GitHub public API network error: {exc}.",
            metadata={
                "read_only": True,
                "mode": "public_api",
                "error_type": "network_error",
                "repo": repo,
            },
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"GitHub public API read failed: {exc}.",
            metadata={
                "read_only": True,
                "mode": "public_api",
                "error_type": "github_api_error",
                "repo": repo,
            },
        )

    results = []
    for item in payload.get("items", [])[:limit]:
        results.append(
            {
                "title": str(item.get("title") or item.get("name") or "<untitled>"),
                "url": str(item.get("html_url") or ""),
                "type": "issue_or_pr",
                "source": repo or "github_public_api",
                "snippet": str(item.get("body") or "")[:300],
            }
        )
    return ToolResult(
        success=True,
        output={"query": query, "repo": repo, "mode": "public_api", "results": results},
        output_summary=f"mcp_github_search returned {len(results)} public API results.",
        metadata={"read_only": True, "mode": "public_api", "repo": repo},
    )


def github_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Search GitHub-style evidence without any write operation."""

    mode = str(arguments.get("mode") or "mock")
    limit = _normalize_limit(arguments.get("limit", DEFAULT_LIMIT))
    query, repo, error = _validate(arguments.get("query"), arguments.get("repo"), mode)
    if error:
        return ToolResult(
            success=False,
            error_message=error,
            metadata={"read_only": True, "mode": mode, "error_type": "invalid_args"},
        )

    if mode == "public_api":
        return _public_api_results(query or "", repo, limit)

    results = _mock_results(query or "", repo, limit)
    return ToolResult(
        success=True,
        output={"query": query, "repo": repo, "mode": "mock", "results": results},
        output_summary=f"mcp_github_search returned {len(results)} mock results.",
        metadata={"read_only": True, "mode": "mock", "repo": repo},
    )
