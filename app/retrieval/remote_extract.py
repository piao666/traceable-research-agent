"""Normalize optional Firecrawl, Exa, or MCP extraction into FetchResult."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.retrieval.classifier import failure_status, make_failure
from app.retrieval.content_quality import assess_page_quality
from app.retrieval.contracts import (
    FetchBackend,
    FetchFailureCode,
    FetchRequest,
    FetchResult,
    FetchStatus,
)
from app.retrieval.source_identity import source_lineage
from app.retrieval.source_view import build_source_view
from app.retrieval.url_normalizer import canonicalize_url
from app.tools.ssrf import validate_url


class RemoteProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def extract(self, request: FetchRequest) -> Any: ...


@dataclass
class SourcePackProviderAdapter:
    """Adapt an existing read-only Source Pack provider without nested tool calls."""

    provider: Any
    tool_name: str
    name: str

    def available(self) -> bool:
        if getattr(self.provider, "fake_mode", False):
            return True
        api_key = getattr(self.provider, "api_key", "")
        self_hosted = bool(getattr(self.provider, "self_hosted", False))
        return bool(api_key or self_hosted)

    def extract(self, request: FetchRequest) -> Any:
        return self.provider.call_tool(self.tool_name, {"url": request.url, "urls": [request.url]})


def configured_remote_providers(
    *,
    timeout_seconds: float = 20,
    max_content_chars: int = 50_000,
    provider_order: str = "firecrawl,exa",
) -> list[RemoteProvider]:
    """Build configured providers; missing optional dependencies stay non-fatal."""

    providers: list[RemoteProvider] = []
    try:
        from app.mcp_bridge.providers.firecrawl import FirecrawlProvider

        provider = FirecrawlProvider(
            fake_mode=False,
            timeout_seconds=timeout_seconds,
            max_results=1,
            max_content_chars=max_content_chars,
        )
        providers.append(SourcePackProviderAdapter(provider, "firecrawl.scrape", "firecrawl"))
    except (ImportError, RuntimeError):
        pass
    try:
        from app.mcp_bridge.providers.exa import ExaProvider

        provider = ExaProvider(
            fake_mode=False,
            timeout_seconds=timeout_seconds,
            max_results=1,
            max_content_chars=max_content_chars,
        )
        providers.append(SourcePackProviderAdapter(provider, "exa.web_fetch_exa", "exa"))
    except (ImportError, RuntimeError):
        pass
    order = [item.strip().casefold() for item in provider_order.split(",") if item.strip()]
    rank = {name: index for index, name in enumerate(order)}
    providers.sort(key=lambda item: rank.get(item.name.casefold(), len(rank)))
    return providers


class RemoteExtractBackend:
    name = FetchBackend.REMOTE_EXTRACT

    def __init__(
        self,
        providers: list[RemoteProvider] | None = None,
        *,
        enabled: bool = False,
        quality_min_score: float = 0.55,
    ) -> None:
        self.providers = list(providers or [])
        self.enabled = enabled
        self.quality_min_score = quality_min_score

    def fetch(self, request: FetchRequest) -> FetchResult:
        started = time.monotonic()
        if not self.enabled:
            return self._unavailable(request, "Remote extraction is disabled.", started)
        if validate_url(request.url) is None:
            failure = make_failure(FetchFailureCode.SSRF_BLOCKED, "Remote extraction URL failed validation.")
            return self._failed(request, failure, started, provider="remote_extract")

        attempts: list[dict[str, Any]] = []
        for provider in self.providers:
            if not provider.available():
                attempts.append({"provider": provider.name, "status": "unavailable"})
                continue
            provider_started = time.monotonic()
            try:
                raw = provider.extract(request)
                page, error = normalize_remote_payload(raw, request.url)
                attempts.append(
                    {
                        "provider": provider.name,
                        "status": "success" if page else "provider_error",
                        "duration_ms": int((time.monotonic() - provider_started) * 1000),
                        **({"error": error} if error else {}),
                    }
                )
                if page is None:
                    continue
                page_metadata = (
                    page.get("metadata")
                    if isinstance(page.get("metadata"), dict)
                    else {}
                )
                provider_content_truncated = bool(
                    page_metadata.get("provider_content_truncated", False)
                )
                source_view = build_source_view(
                    str(page.get("content") or ""),
                    request.max_chars,
                    source_truncated=provider_content_truncated,
                )
                title = str(page.get("title") or request.url)[:300]
                final_url = str(page.get("url") or request.url)
                if validate_url(final_url) is None:
                    attempts[-1]["status"] = "unsafe_result_url"
                    continue
                quality, quality_failure = assess_page_quality(
                    source_view.content,
                    title=title,
                    extraction_method=f"remote_{provider.name}",
                    extraction_confidence=0.82,
                    truncated=source_view.truncated,
                    minimum_score=self.quality_min_score,
                )
                canonical = canonicalize_url(final_url).normalized_url
                status = failure_status(quality_failure) if quality_failure else (
                    FetchStatus.PARTIAL if source_view.truncated else FetchStatus.SUCCESS
                )
                return FetchResult(
                    requested_url=request.url,
                    final_url=final_url,
                    title=title,
                    content=source_view.content,
                    published_at=page.get("published_at"),
                    content_type=str(page.get("content_type") or "text/markdown"),
                    content_basis=quality.content_basis,
                    extraction_method=f"remote_{provider.name}",
                    extraction_confidence=0.82,
                    fetch_status=status,
                    fetch_backend=FetchBackend.REMOTE_EXTRACT,
                    provider=provider.name,
                    quality=quality,
                    failure=quality_failure,
                    failure_reason=quality_failure.message if quality_failure else None,
                    content_hash=source_view.source_content_hash,
                    source_content_hash=source_view.source_content_hash,
                    canonical_url=canonical,
                    fragment_locator=canonicalize_url(request.url).fragment_locator,
                    metadata={
                        "provider_attempts": attempts,
                        **source_view.metadata(),
                        **{
                            key: page_metadata[key]
                            for key in (
                                "provider_content_original_length",
                                "provider_content_returned_length",
                                "provider_content_limit",
                                "provider_content_truncated",
                            )
                            if key in page_metadata
                        },
                        "remote_metadata": dict(page_metadata),
                        "source_identity": source_lineage(
                            canonical,
                            source_view.source_content,
                            {
                                **(
                                    page_metadata
                                ),
                                "title": title,
                            },
                        ).to_dict(),
                        "fetched_at_ms": int((time.monotonic() - started) * 1000),
                    },
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                attempts.append(
                    {
                        "provider": provider.name,
                        "status": "provider_error",
                        "error": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - provider_started) * 1000),
                    }
                )
        return self._unavailable(
            request,
            "No configured remote extraction provider returned usable content.",
            started,
            attempts=attempts,
        )

    def _unavailable(
        self,
        request: FetchRequest,
        message: str,
        started: float,
        *,
        attempts: list[dict[str, Any]] | None = None,
    ) -> FetchResult:
        failure = make_failure(
            FetchFailureCode.REMOTE_EXTRACT_UNAVAILABLE,
            message,
            tool_scoped=True,
        )
        return self._failed(request, failure, started, provider="remote_extract", attempts=attempts)

    @staticmethod
    def _failed(
        request: FetchRequest,
        failure,
        started: float,
        *,
        provider: str,
        attempts: list[dict[str, Any]] | None = None,
    ) -> FetchResult:
        return FetchResult(
            requested_url=request.url,
            canonical_url=canonicalize_url(request.url).normalized_url,
            fetch_status=failure_status(failure),
            fetch_backend=FetchBackend.REMOTE_EXTRACT,
            provider=provider,
            failure=failure,
            failure_reason=failure.message,
            metadata={
                "provider_attempts": list(attempts or []),
                "fetched_at_ms": int((time.monotonic() - started) * 1000),
            },
        )


def normalize_remote_payload(raw: Any, requested_url: str) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize BridgeToolResult, MCP-style dicts, and provider-native page dicts."""

    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="python")
    elif hasattr(raw, "__dict__") and not isinstance(raw, dict):
        raw = dict(vars(raw))
    if not isinstance(raw, dict):
        return None, "provider returned a non-object payload"
    if raw.get("success") is False:
        return None, str(raw.get("error_message") or "provider reported failure")[:500]
    payload = raw.get("output") if isinstance(raw.get("output"), dict) else raw
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("pages", "results", "documents", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                candidates.append(value)
        if not candidates:
            candidates.append(payload)
    requested_canonical = canonicalize_url(requested_url).normalized_url
    page = next(
        (
            item
            for item in candidates
            if canonicalize_url(str(item.get("url") or item.get("sourceURL") or requested_url)).normalized_url
            == requested_canonical
        ),
        candidates[0] if candidates else None,
    )
    if not isinstance(page, dict):
        return None, "provider returned no page"
    metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
    content = page.get("content") or page.get("markdown") or page.get("text") or page.get("summary")
    if not str(content or "").strip():
        return None, "provider page contained no readable content"
    return {
        "url": page.get("url") or metadata.get("sourceURL") or metadata.get("url") or requested_url,
        "title": page.get("title") or metadata.get("title") or requested_url,
        "content": str(content).strip(),
        "published_at": page.get("published_at") or page.get("publishedDate") or metadata.get("publishedTime"),
        "content_type": page.get("content_type") or "text/markdown",
        "metadata": metadata,
    }, None


__all__ = [
    "RemoteExtractBackend",
    "RemoteProvider",
    "SourcePackProviderAdapter",
    "configured_remote_providers",
    "normalize_remote_payload",
]
