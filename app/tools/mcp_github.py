"""Offline-safe, read-only GitHub/MCP-style search adapter."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.config import Settings, settings
from app.mcp.readonly import is_http_method_allowed, readonly_policy_metadata
from app.tools.base import ToolResult
from app.tools.github_cache import get_cache_key, get_cached_result, put_cached_result


ROOT = Path(__file__).resolve().parents[2]
MAX_LIMIT = 10
DEFAULT_LIMIT = 5
VALID_MODES = {"mock", "public_api"}
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PUBLIC_API_ENDPOINT = "https://api.github.com/search/issues"


def _normalize_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _validate(query: Any, repo: Any, mode: str) -> tuple[str | None, str | None, str | None]:
    if not isinstance(query, str) or not query.strip():
        return None, None, "Missing required argument: query."
    normalized_repo = None
    if repo is not None:
        if not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo.strip()):
            return None, None, "Invalid repo format. Expected owner/name."
        normalized_repo = repo.strip()
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


def _cache_path(settings_obj: Settings) -> Path:
    configured = Path(settings_obj.github_search_cache_path)
    return configured if configured.is_absolute() else ROOT / configured


def _safe_cache_path(settings_obj: Settings) -> str:
    configured = Path(settings_obj.github_search_cache_path)
    return configured.name if configured.is_absolute() else configured.as_posix()


def _metadata(
    settings_obj: Settings,
    *,
    mode: str,
    data_source: str | None,
    repo: str | None,
    result_count: int = 0,
    cache_hit: bool = False,
    fallback_used: bool = False,
    retry_count: int = 0,
    error_type: str | None = None,
    rate_limited: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    metadata = {
        **readonly_policy_metadata(settings_obj),
        "mode": mode,
        "data_source": data_source,
        "cache_enabled": settings_obj.github_search_cache_enabled,
        "cache_hit": cache_hit,
        "cache_path": _safe_cache_path(settings_obj),
        "fallback_used": fallback_used,
        "retry_count": retry_count,
        "error_type": error_type,
        "rate_limited": rate_limited,
        "repo": repo,
        "result_count": result_count,
    }
    metadata.update(extra)
    return metadata


def _request_public_api(
    query: str,
    repo: str | None,
    limit: int,
    settings_obj: Settings,
    opener: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> tuple[list[dict[str, str]] | None, dict[str, Any]]:
    search_query = query if repo is None else f"{query} repo:{repo}"
    url = f"{PUBLIC_API_ENDPOINT}?q={quote(search_query)}&per_page={limit}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "traceable-research-agent-read-only",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings_obj.github_token:
        headers["Authorization"] = f"Bearer {settings_obj.github_token}"

    method = "GET"
    if not is_http_method_allowed(method):
        return None, {
            "error_type": "readonly_policy_rejected",
            "error_message": "GitHub request method is not allowed by read-only policy.",
            "rate_limited": False,
            "retry_count": 0,
        }

    timeout = max(1, settings_obj.github_public_api_timeout_seconds)
    max_retries = max(0, min(settings_obj.github_public_api_max_retries, 5))
    request = Request(url, headers=headers, method=method)
    for attempt in range(max_retries + 1):
        retryable = False
        error_type = "api_error"
        rate_limited = False
        error_message = "GitHub public API request failed."
        try:
            with opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("GitHub response does not contain an items list.")
            results = [
                {
                    "title": str(item.get("title") or item.get("name") or "<untitled>"),
                    "url": str(item.get("html_url") or ""),
                    "type": "issue_or_pr",
                    "source": repo or "github_public_api",
                    "snippet": str(item.get("body") or "")[:300],
                }
                for item in payload["items"][:limit]
                if isinstance(item, dict)
            ]
            return results, {"retry_count": attempt, "rate_limited": False}
        except HTTPError as exc:
            rate_limited = exc.code in {403, 429}
            error_type = "rate_limited" if rate_limited else "api_error"
            error_message = f"GitHub public API request failed with HTTP {exc.code}."
            retryable = rate_limited or exc.code >= 500
        except (URLError, TimeoutError, OSError) as exc:
            error_type = "network_error"
            error_message = f"GitHub public API network error: {type(exc).__name__}."
            retryable = True
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            error_type = "invalid_response"
            error_message = f"GitHub public API returned an invalid response: {type(exc).__name__}."
            retryable = True
        except Exception as exc:
            error_type = "api_error"
            error_message = f"GitHub public API read failed: {type(exc).__name__}."
            retryable = True

        if retryable and attempt < max_retries:
            sleeper(0.5 * (2**attempt))
            continue
        return None, {
            "error_type": error_type,
            "error_message": error_message,
            "rate_limited": rate_limited,
            "retry_count": attempt,
        }

    raise AssertionError("unreachable")


def github_search(
    arguments: dict[str, Any],
    *,
    settings_obj: Settings | None = None,
    opener: Callable[..., Any] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> ToolResult:
    """Search GitHub evidence with cache, retry, and stable fallback behavior."""

    active_settings = settings_obj or settings
    mode = str(arguments.get("mode") or "mock").strip().lower()
    limit = _normalize_limit(arguments.get("limit", DEFAULT_LIMIT))
    query, repo, error = _validate(arguments.get("query"), arguments.get("repo"), mode)
    if error:
        return ToolResult(
            success=False,
            error_message=error,
            metadata=_metadata(
                active_settings,
                mode=mode,
                data_source=None,
                repo=repo,
                error_type="invalid_args",
            ),
        )

    normalized_query = query or ""
    if mode == "mock":
        results = _mock_results(normalized_query, repo, limit)
        return ToolResult(
            success=True,
            output={"query": query, "repo": repo, "mode": "mock", "results": results},
            output_summary=f"mcp_github_search returned {len(results)} mock results.",
            metadata=_metadata(
                active_settings,
                mode="mock",
                data_source="mock",
                repo=repo,
                result_count=len(results),
            ),
        )

    cache_error = None
    cache_key = get_cache_key(normalized_query, repo, limit, mode, PUBLIC_API_ENDPOINT)
    path = _cache_path(active_settings)
    if active_settings.github_search_cache_enabled:
        cached, cache_error = get_cached_result(path, cache_key)
        if cached is not None:
            results = cached["results"]
            return ToolResult(
                success=True,
                output={"query": query, "repo": repo, "mode": "public_api", "results": results},
                output_summary=f"mcp_github_search returned {len(results)} cached results.",
                metadata=_metadata(
                    active_settings,
                    mode="public_api",
                    data_source="cache",
                    repo=repo,
                    result_count=len(results),
                    cache_hit=True,
                    cache_error=cache_error,
                ),
            )

    results, request_metadata = _request_public_api(
        normalized_query,
        repo,
        limit,
        active_settings,
        opener or urlopen,
        sleeper or time.sleep,
    )
    if results is not None:
        metadata = _metadata(
            active_settings,
            mode="public_api",
            data_source="public_api",
            repo=repo,
            result_count=len(results),
            retry_count=request_metadata["retry_count"],
            cache_error=cache_error,
        )
        if active_settings.github_search_cache_enabled:
            write_error = put_cached_result(
                path,
                cache_key,
                results,
                {
                    "mode": "public_api",
                    "repo": repo,
                    "result_count": len(results),
                    "read_only": True,
                },
                active_settings.github_search_cache_ttl_seconds,
            )
            if write_error:
                metadata["cache_error"] = write_error
        return ToolResult(
            success=True,
            output={"query": query, "repo": repo, "mode": "public_api", "results": results},
            output_summary=f"mcp_github_search returned {len(results)} public API results.",
            metadata=metadata,
        )

    if active_settings.github_public_api_fallback_to_mock:
        fallback_results = _mock_results(normalized_query, repo, limit)
        return ToolResult(
            success=True,
            output={
                "query": query,
                "repo": repo,
                "mode": "public_api",
                "results": fallback_results,
            },
            output_summary=(
                f"mcp_github_search returned {len(fallback_results)} fallback mock results."
            ),
            metadata=_metadata(
                active_settings,
                mode="public_api",
                data_source="fallback",
                repo=repo,
                result_count=len(fallback_results),
                fallback_used=True,
                retry_count=request_metadata["retry_count"],
                rate_limited=request_metadata["rate_limited"],
                original_error_type=request_metadata["error_type"],
                fallback_reason=request_metadata["error_message"],
                cache_error=cache_error,
            ),
        )

    return ToolResult(
        success=False,
        error_message=request_metadata["error_message"],
        metadata=_metadata(
            active_settings,
            mode="public_api",
            data_source="public_api",
            repo=repo,
            retry_count=request_metadata["retry_count"],
            error_type=request_metadata["error_type"],
            rate_limited=request_metadata["rate_limited"],
            cache_error=cache_error,
        ),
    )


def github_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Registry-compatible wrapper for the read-only GitHub search tool."""

    return github_search(arguments)
