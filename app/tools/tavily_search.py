"""Read-only Tavily web search tool with explicit offline behavior."""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings, settings
from app.tools.base import ToolResult


TAVILY_ENDPOINT = "https://api.tavily.com/search"
VALID_DEPTHS = {"basic", "advanced"}


def _bounded_results(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 20))


def _mock_results(query: str, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "title": "Offline Tavily demonstration result",
            "url": "https://example.invalid/offline-tavily-demo",
            "content": f"Offline-only mock evidence for query: {query}",
            "score": 0.0,
            "raw_content": None,
        }
    ][:limit]


def _metadata(
    active: Settings,
    *,
    data_source: str,
    result_count: int = 0,
    retry_count: int = 0,
    error_type: str | None = None,
    fallback_used: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    metadata = {
        "tool_name": "tavily_search",
        "tavily_configured": bool(active.tavily_api_key),
        "data_source": data_source,
        "read_only": True,
        "write_operations_allowed": False,
        "result_count": result_count,
        "retry_count": retry_count,
        "error_type": error_type,
        "fallback_used": fallback_used,
    }
    metadata.update(extra)
    return metadata


def tavily_search(
    arguments: dict[str, Any],
    *,
    settings_obj: Settings | None = None,
    opener: Callable[..., Any] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> ToolResult:
    """Search the web through Tavily without exposing or persisting its key."""

    active = settings_obj or settings
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolResult(
            success=False,
            error_message="Missing required argument: query.",
            metadata=_metadata(active, data_source="tavily_api", error_type="invalid_args"),
        )
    query = query.strip()
    max_results = _bounded_results(
        arguments.get("max_results"), active.tavily_default_max_results
    )
    search_depth = str(arguments.get("search_depth") or "basic").strip().lower()
    if search_depth not in VALID_DEPTHS:
        return ToolResult(
            success=False,
            error_message="Invalid search_depth. Expected basic or advanced.",
            metadata=_metadata(active, data_source="tavily_api", error_type="invalid_args"),
        )

    if active.offline_mode:
        results = _mock_results(query, max_results)
        return ToolResult(
            success=True,
            output={"query": query, "answer": None, "results": results},
            output_summary=f"tavily_search returned {len(results)} offline mock results.",
            metadata=_metadata(
                active, data_source="mock", result_count=len(results), offline_mode=True
            ),
        )
    if not active.tavily_search_enabled:
        return ToolResult(
            success=False,
            error_message="Tavily search is disabled.",
            metadata=_metadata(active, data_source="tavily_api", error_type="disabled"),
        )
    if not active.tavily_api_key:
        return ToolResult(
            success=False,
            error_message="TAVILY_API_KEY is not configured.",
            metadata=_metadata(active, data_source="tavily_api", error_type="missing_api_key"),
        )

    include_answer = bool(arguments.get("include_answer", False))
    include_raw_content = bool(arguments.get("include_raw_content", False))
    body = json.dumps(
        {
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
        }
    ).encode("utf-8")
    request = Request(
        TAVILY_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {active.tavily_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "traceable-research-agent-read-only",
        },
        method="POST",
    )
    call = opener or urlopen
    wait = sleeper or time.sleep
    max_retries = max(0, min(active.tavily_max_retries, 5))
    error_type = "api_error"
    error_message = "Tavily API request failed."
    for attempt in range(max_retries + 1):
        retryable = False
        try:
            with call(request, timeout=max(1, active.tavily_timeout_seconds)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Tavily response does not contain a results list.")
            results = [
                {
                    "title": str(item.get("title") or "<untitled>"),
                    "url": str(item.get("url") or ""),
                    "content": str(item.get("content") or ""),
                    "score": item.get("score"),
                    "raw_content": item.get("raw_content") if include_raw_content else None,
                }
                for item in payload["results"][:max_results]
                if isinstance(item, dict)
            ]
            return ToolResult(
                success=True,
                output={"query": query, "answer": payload.get("answer"), "results": results},
                output_summary=f"tavily_search returned {len(results)} Tavily API results.",
                metadata=_metadata(
                    active,
                    data_source="tavily_api",
                    result_count=len(results),
                    retry_count=attempt,
                ),
            )
        except HTTPError as exc:
            error_type = "rate_limited" if exc.code in {403, 429} else "api_error"
            error_message = f"Tavily API request failed with HTTP {exc.code}."
            retryable = exc.code in {403, 429} or exc.code >= 500
        except (URLError, TimeoutError, OSError) as exc:
            error_type = "network_error"
            error_message = f"Tavily API network error: {type(exc).__name__}."
            retryable = True
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            error_type = "invalid_response"
            error_message = f"Tavily API returned an invalid response: {type(exc).__name__}."
            retryable = True

        if retryable and attempt < max_retries:
            wait(0.5 * (2**attempt))
            continue
        break

    if active.tavily_fallback_to_mock or active.allow_mock_fallback:
        results = _mock_results(query, max_results)
        return ToolResult(
            success=True,
            output={"query": query, "answer": None, "results": results},
            output_summary=f"tavily_search returned {len(results)} fallback mock results.",
            metadata=_metadata(
                active,
                data_source="fallback",
                result_count=len(results),
                retry_count=attempt,
                fallback_used=True,
                original_error_type=error_type,
                fallback_reason=error_message,
            ),
        )
    return ToolResult(
        success=False,
        error_message=error_message,
        metadata=_metadata(
            active,
            data_source="tavily_api",
            retry_count=attempt,
            error_type=error_type,
        ),
    )


def tavily_search_handler(arguments: dict[str, Any]) -> ToolResult:
    return tavily_search(arguments)
