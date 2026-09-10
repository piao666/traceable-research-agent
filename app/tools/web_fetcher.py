"""Compatibility web_fetcher entry backed by the R11 adaptive retrieval router."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings, settings
from app.retrieval.browser_backend import BrowserBackend, configure_browser_concurrency
from app.retrieval.contracts import FetchBackend, FetchFailureCode, FetchRequest, FetchResult
from app.retrieval.html_extractor import (
    EXTRACT_BEAUTIFULSOUP,
    EXTRACT_NONE,
    EXTRACT_RAW_REGEX,
    EXTRACT_TRAFILATURA,
    extract_html,
)
from app.retrieval.http_backend import HttpBackend, PDF_MAGIC, _read_bounded
from app.retrieval.pdf_backend import PdfBackend
from app.retrieval.remote_extract import RemoteExtractBackend, configured_remote_providers
from app.retrieval.router import RetrievalRouter
from app.retrieval.url_normalizer import canonicalize_url
from app.tools.base import ToolResult
from app.tools.fetch_cache import FetchCache

USER_AGENT = "traceable-research-agent-read-only/2.0"
DEFAULT_BATCH_TIMEOUT_SECONDS = 25
MAX_BATCH_TIMEOUT_SECONDS = 120
BATCH_RETURN_MARGIN_SECONDS = 2


def web_fetch(
    arguments: dict[str, Any],
    *,
    settings_obj: Settings | None = None,
    cache: FetchCache | None = None,
    client: httpx.Client | None = None,
    router: RetrievalRouter | None = None,
) -> ToolResult:
    """Fetch URL content while preserving the public web_fetcher tool contract."""
    if arguments.get("source_id"):
        from app.tools.source_snapshot import read_snapshot

        return read_snapshot(arguments)

    urls_raw = arguments.get("urls", [])
    if isinstance(urls_raw, str):
        urls_raw = [urls_raw]
    if not isinstance(urls_raw, list):
        return _invalid_result("web_fetcher requires a 'urls' list argument.", [], "invalid_args")

    active = settings_obj or settings
    max_chars = _bounded_int(arguments.get("max_chars", 8000), 8000, 500, 50_000)
    timeout_seconds = _bounded_int(arguments.get("timeout_seconds", 10), 10, 1, 120)
    batch_timeout_seconds = _bounded_int(
        arguments.get("batch_timeout_seconds", DEFAULT_BATCH_TIMEOUT_SECONDS),
        DEFAULT_BATCH_TIMEOUT_SECONDS,
        5,
        MAX_BATCH_TIMEOUT_SECONDS,
    )
    timeout_seconds = min(timeout_seconds, max(1, batch_timeout_seconds - BATCH_RETURN_MARGIN_SECONDS))

    fetch_cache = cache
    cache_init_error: str | None = None
    if active.web_fetcher_cache_enabled and fetch_cache is None:
        try:
            fetch_cache = FetchCache(active.web_fetcher_cache_dir, active.web_fetcher_cache_ttl_seconds)
        except Exception as exc:
            cache_init_error = type(exc).__name__
            fetch_cache = None

    active_router = router or _build_router(active, client, fetch_cache)
    pages: list[dict[str, Any]] = []
    results: list[FetchResult] = []
    batch_started = time.monotonic()
    deferred_count = 0
    canonical_results: dict[str, FetchResult] = {}
    content_sources: dict[str, str] = {}

    for index, raw_url in enumerate(urls_raw):
        original = str(raw_url or "").strip()
        elapsed = time.monotonic() - batch_started
        remaining = batch_timeout_seconds - elapsed
        if index > 0 and remaining < timeout_seconds + BATCH_RETURN_MARGIN_SECONDS:
            deferred = urls_raw[index:]
            deferred_count = len(deferred)
            pages.extend(_deferred_page(str(item or "").strip()) for item in deferred)
            break

        canonical = (
            canonicalize_url(original).normalized_url
            if active.url_canonicalization_enabled
            else original
        )
        existing = canonical_results.get(canonical) if active.content_dedup_enabled else None
        if existing is not None:
            duplicate = existing.model_copy(deep=True)
            duplicate.requested_url = original
            duplicate.metadata.update(
                deduplicated=True,
                duplicate_reason="canonical_url",
                duplicate_of=existing.canonical_url or existing.final_url,
            )
            results.append(duplicate)
            pages.append(duplicate.to_page_dict())
            continue

        preferred = _preferred_backends(arguments.get("preferred_backends"))
        if not active.fetch_router_enabled:
            preferred = [FetchBackend.HTTP]
        request = FetchRequest(
            url=original,
            max_chars=max_chars,
            preferred_backends=preferred,
            allow_browser=active.fetch_router_enabled and bool(arguments.get("allow_browser", True)),
            allow_remote_extract=active.fetch_router_enabled and bool(arguments.get("allow_remote_extract", True)),
            research_run_id=_optional_str(arguments.get("research_run_id")),
            trace_id=_optional_str(arguments.get("trace_id")),
            timeout_seconds=min(timeout_seconds, max(1, int(remaining - BATCH_RETURN_MARGIN_SECONDS))),
        )
        result = active_router.fetch(request)
        canonical_results[canonical] = result
        if active.content_dedup_enabled and result.content_hash:
            duplicate_of = content_sources.get(result.content_hash)
            if duplicate_of:
                result.metadata.update(
                    deduplicated=True,
                    duplicate_reason="content_hash",
                    duplicate_of=duplicate_of,
                )
            else:
                content_sources[result.content_hash] = result.canonical_url or result.final_url or original
        results.append(result)
        pages.append(result.to_page_dict())

    fetched_count = sum(1 for result in results if result.usable)
    failed_count = len(pages) - fetched_count
    failure_counts = _failure_counts(results, pages)
    recommended_actions = _recommended_actions(results)
    full_text = sum(1 for page in pages if page.get("content_basis") == "full_text")
    partial = sum(1 for page in pages if page.get("content_basis") == "partial")
    snippet = sum(1 for page in pages if page.get("content_basis") == "snippet_only")
    dominant_failure = max(failure_counts, key=failure_counts.get) if failure_counts else None

    return ToolResult(
        success=fetched_count > 0,
        error_message=None if fetched_count else "No usable page content was fetched.",
        output={
            "pages": pages,
            "fetched_count": fetched_count,
            "failed_count": failed_count,
            "total_count": len(pages),
        },
        output_summary=(
            f"web_fetcher: {fetched_count}/{len(pages)} URLs fetched "
            f"(full_text={full_text}, partial={partial}, snippet_only={snippet}, "
            f"deadline_deferred={deferred_count})"
        ),
        metadata={
            **({"error_type": dominant_failure or "empty_input"} if not fetched_count else {}),
            **({"legacy_error_type": "empty_result"} if not fetched_count else {}),
            "tool_name": "web_fetcher",
            "fetcher_backend": "httpx_multi_level",
            "retrieval_router": "adaptive_r11",
            "fetch_router_enabled": active.fetch_router_enabled,
            "read_only": True,
            "result_count": len(pages),
            "cache_enabled": active.web_fetcher_cache_enabled,
            "cache_init_error": cache_init_error,
            "cache_hits": sum(1 for page in pages if page.get("cache_status") == "hit"),
            "cache_revalidated": sum(1 for page in pages if page.get("cache_status") == "revalidated"),
            "cache_misses": sum(1 for page in pages if page.get("cache_status") == "miss"),
            "cache_expired": sum(1 for page in pages if page.get("cache_status") == "expired"),
            "cache_corrupt": sum(1 for page in pages if page.get("cache_status") == "corrupt"),
            "batch_timeout_seconds": batch_timeout_seconds,
            "batch_deadline_exceeded": deferred_count > 0,
            "batch_deferred_count": deferred_count,
            "failure_counts": failure_counts,
            "recommended_actions": recommended_actions,
            "canonical_deduplicated_count": sum(
                1 for page in pages if page.get("deduplicated") and page.get("duplicate_reason") == "canonical_url"
            ),
            "content_deduplicated_count": sum(
                1 for page in pages if page.get("deduplicated") and page.get("duplicate_reason") == "content_hash"
            ),
        },
    )


def _build_router(active: Settings, client: httpx.Client | None, cache: FetchCache | None) -> RetrievalRouter:
    configure_browser_concurrency(active.fetch_browser_max_concurrency)
    http_backend = HttpBackend(
        client=client,
        cache=cache,
        cache_enabled=active.web_fetcher_cache_enabled,
        cache_ttl_seconds=active.web_fetcher_cache_ttl_seconds,
        max_response_bytes=active.web_fetcher_max_response_bytes,
        trafilatura_enabled=active.web_fetcher_trafilatura_enabled,
        enabled=active.fetch_http_enabled,
        quality_min_score=active.fetch_quality_min_score,
    )
    browser_enabled = bool(active.fetch_browser_enabled or active.web_fetcher_playwright_enabled)
    remote_enabled = bool(active.fetch_remote_extract_enabled)
    return RetrievalRouter(
        http_backend=http_backend,
        browser_backend=BrowserBackend(
            enabled=browser_enabled,
            timeout_seconds=active.fetch_browser_timeout_seconds,
            quality_min_score=active.fetch_quality_min_score,
            max_response_bytes=active.web_fetcher_max_response_bytes,
        ),
        remote_backend=RemoteExtractBackend(
            configured_remote_providers(
                timeout_seconds=20,
                max_content_chars=50_000,
                provider_order=active.fetch_remote_extract_provider_order,
            ),
            enabled=remote_enabled,
            quality_min_score=active.fetch_quality_min_score,
        ),
        pdf_backend=PdfBackend(
            enabled=active.pdf_reader_enabled,
            quality_min_score=active.fetch_quality_min_score,
        ),
    )


def _failure_counts(results: list[FetchResult], pages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        attempts = result.metadata.get("retrieval_attempts")
        if isinstance(attempts, list):
            for attempt in attempts:
                if isinstance(attempt, dict) and attempt.get("failure_code"):
                    code = str(attempt["failure_code"])
                    counts[code] = counts.get(code, 0) + 1
        elif result.failure is not None:
            code = result.failure.code.value
            counts[code] = counts.get(code, 0) + 1
    deferred = sum(
        1
        for page in pages
        if (page.get("error_code") or page.get("error")) == FetchFailureCode.BATCH_DEADLINE.value
    )
    if deferred:
        counts[FetchFailureCode.BATCH_DEADLINE.value] = deferred
    return counts


def _recommended_actions(results: list[FetchResult]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        failure = result.failure
        if failure is None or failure.recommended_next_strategy is None:
            continue
        key = (result.requested_url, failure.recommended_next_strategy.value)
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            {
                "url": result.requested_url,
                "failure_code": failure.code.value,
                "recommended_backend": failure.recommended_next_strategy.value,
            }
        )
    return actions


def _deferred_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": url,
        "content": "",
        "content_basis": "snippet_only",
        "extraction_method": EXTRACT_NONE,
        "fetch_status": "timeout",
        "fetch_backend": "http",
        "provider": "retrieval_router",
        "cache_status": "not_attempted",
        "cache_hit": False,
        "fetched_at_ms": 0,
        "error": "batch_deadline_exceeded",
        "error_code": "batch_deadline_exceeded",
    }


def _invalid_result(message: str, pages: list[dict[str, Any]], error_type: str) -> ToolResult:
    return ToolResult(
        success=False,
        error_message=message,
        output={"pages": pages, "fetched_count": 0, "failed_count": len(pages), "total_count": len(pages)},
        output_summary=message,
        metadata={
            "error_type": error_type,
            "tool_name": "web_fetcher",
            "fetcher_backend": "httpx_multi_level",
            "retrieval_router": "adaptive_r11",
            "read_only": True,
        },
    )


def _preferred_backends(value: Any) -> list[FetchBackend]:
    if not isinstance(value, list):
        return []
    selected: list[FetchBackend] = []
    for item in value:
        try:
            selected.append(FetchBackend(str(item)))
        except ValueError:
            continue
    return selected


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


# Backwards-compatible helpers used by deterministic evaluation cases.
def _extract_body_v2(
    html: str,
    url: str,
    *,
    trafilatura_enabled: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    extraction = extract_html(html, url, trafilatura_enabled=trafilatura_enabled)
    return extraction.content, extraction.extraction_method, {
        "extraction_chain": list(extraction.extraction_chain),
        "extraction_confidence": extraction.extraction_confidence,
    }


def _classify_content_basis(
    raw_len: int,
    cleaned_len: int,
    max_chars: int,
    fetch_error: str | None,
    extraction_method: str = EXTRACT_NONE,
) -> str:
    if fetch_error or extraction_method == EXTRACT_NONE:
        return "snippet_only"
    if cleaned_len >= max_chars - 50 or extraction_method == EXTRACT_RAW_REGEX:
        return "partial"
    return "full_text"


def _is_pdf_content_type(content_type: str) -> bool:
    return "application/pdf" in str(content_type).casefold()


def _is_pdf_url(url: str) -> bool:
    try:
        return urlsplit(url).path.casefold().endswith(".pdf")
    except ValueError:
        return False


def _check_pdf_magic(data: bytes) -> bool:
    return data.startswith(PDF_MAGIC)


__all__ = [
    "EXTRACT_BEAUTIFULSOUP",
    "EXTRACT_NONE",
    "EXTRACT_RAW_REGEX",
    "EXTRACT_TRAFILATURA",
    "_check_pdf_magic",
    "_classify_content_basis",
    "_extract_body_v2",
    "_is_pdf_content_type",
    "_is_pdf_url",
    "_read_bounded",
    "web_fetch",
]
