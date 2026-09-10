"""SSRF-safe HTTP retrieval backend with deterministic extraction and caching."""

from __future__ import annotations

import hashlib
import time
from contextlib import nullcontext
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.retrieval.classifier import (
    classify_exception,
    classify_http_status,
    failure_status,
    make_failure,
)
from app.retrieval.content_quality import assess_page_quality
from app.retrieval.contracts import (
    FetchBackend,
    FetchFailureCode,
    FetchRequest,
    FetchResult,
    FetchStatus,
)
from app.retrieval.html_extractor import extract_html
from app.retrieval.source_identity import source_lineage
from app.retrieval.url_normalizer import canonicalize_url, resolve_canonical_hint
from app.tools.fetch_cache import FetchCache, FetchCacheEntry
from app.tools.ssrf import validate_url
from app.tools.web_content_cleaner import clean_web_snippet


USER_AGENT = "traceable-research-agent-read-only/2.0"
PDF_MAGIC = b"%PDF-"
SUPPORTED_TEXT_TYPES = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
)


class HttpBackend:
    """Retrieve one URL through httpx and return the unified R11 contract."""

    name = FetchBackend.HTTP

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: FetchCache | None = None,
        cache_enabled: bool = True,
        cache_ttl_seconds: int = 3600,
        max_response_bytes: int = 10_485_760,
        trafilatura_enabled: bool = True,
        max_redirects: int = 5,
        enabled: bool = True,
        quality_min_score: float = 0.55,
    ) -> None:
        self.client = client
        self.cache = cache
        self.cache_enabled = bool(cache_enabled and cache is not None)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_response_bytes = max_response_bytes
        self.trafilatura_enabled = trafilatura_enabled
        self.max_redirects = max_redirects
        self.enabled = enabled
        self.quality_min_score = quality_min_score

    def fetch(self, request: FetchRequest) -> FetchResult:
        started = time.monotonic()
        requested = request.url.strip()
        if not self.enabled:
            failure = make_failure(
                FetchFailureCode.BACKEND_UNAVAILABLE,
                "HTTP retrieval is disabled.",
                next_strategy=FetchBackend.BROWSER,
                tool_scoped=True,
            )
            return self._failed(requested, failure, started)
        validated = _validated_url(requested)
        if validated is None:
            failure = make_failure(
                FetchFailureCode.SSRF_BLOCKED,
                "URL failed validation (non-http scheme, credentials, or private/reserved host).",
            )
            return self._failed(requested, failure, started)

        normalized = canonicalize_url(validated)
        cache_params = self._cache_params()
        cached_entry: FetchCacheEntry | None = None
        cache_status = "disabled" if not self.cache_enabled else "miss"
        if self.cache_enabled and self.cache is not None:
            cached_entry, cache_status = self.cache.lookup(normalized.normalized_url, cache_params)
            if cache_status == "hit" and cached_entry is not None:
                return self._from_cache(request, normalized.fragment_locator, cached_entry, cache_status)

        owned_client = None
        if self.client is None:
            owned_client = httpx.Client(
                timeout=request.timeout_seconds,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=False,
            )
        context = owned_client if owned_client is not None else nullcontext(self.client)
        try:
            with context as client:
                assert client is not None
                headers: dict[str, str] = {}
                if cache_status == "expired" and cached_entry is not None and cached_entry.etag:
                    headers["If-None-Match"] = cached_entry.etag
                response, redirect_chain, redirect_failure = self._request(
                    client,
                    normalized.normalized_url,
                    headers,
                )
                if redirect_failure is not None:
                    return self._failed(
                        requested,
                        redirect_failure,
                        started,
                        final_url=redirect_chain[-1] if redirect_chain else normalized.normalized_url,
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                    )
                assert response is not None
                if response.status_code == 304 and cached_entry is not None:
                    cached_entry.fetched_at = time.time()
                    cached_entry.ttl_seconds = self.cache_ttl_seconds
                    if self.cache is not None:
                        self.cache.put(cached_entry)
                    return self._from_cache(
                        request,
                        normalized.fragment_locator,
                        cached_entry,
                        "revalidated",
                    )
                if not 200 <= response.status_code < 300:
                    failure = classify_http_status(response.status_code, response.headers)
                    return self._failed(
                        requested,
                        failure,
                        started,
                        final_url=str(response.url),
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                    )

                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                content_length = _content_length(response.headers)
                if content_length is not None and content_length > self.max_response_bytes:
                    failure = make_failure(
                        FetchFailureCode.CONTENT_TOO_LARGE,
                        f"Response Content-Length {content_length} exceeds {self.max_response_bytes} bytes.",
                    )
                    return self._failed(
                        requested,
                        failure,
                        started,
                        final_url=str(response.url),
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                        content_type=content_type,
                    )
                body, received = _read_bounded(response, self.max_response_bytes)
                if body is None:
                    failure = make_failure(
                        FetchFailureCode.CONTENT_TOO_LARGE,
                        f"Response exceeded {self.max_response_bytes} bytes while streaming.",
                    )
                    return self._failed(
                        requested,
                        failure,
                        started,
                        final_url=str(response.url),
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                        content_type=content_type,
                        extra={"received_bytes": received},
                    )
                if _is_pdf(content_type, str(response.url), body):
                    failure = make_failure(
                        FetchFailureCode.PDF_ROUTED,
                        "PDF content must be handled by the PDF backend.",
                    )
                    return self._failed(
                        requested,
                        failure,
                        started,
                        final_url=str(response.url),
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                        content_type="application/pdf",
                    )
                if not _supported_content_type(content_type):
                    failure = make_failure(
                        FetchFailureCode.UNSUPPORTED_CONTENT_TYPE,
                        f"Unsupported Content-Type: {content_type or 'unknown'}",
                    )
                    return self._failed(
                        requested,
                        failure,
                        started,
                        final_url=str(response.url),
                        redirect_chain=redirect_chain,
                        cache_status=cache_status,
                        content_type=content_type,
                    )
                return self._extract_response(
                    request,
                    response,
                    body,
                    content_type,
                    normalized.fragment_locator,
                    redirect_chain,
                    cache_status,
                    cached_entry,
                    started,
                )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._failed(requested, classify_exception(exc), started, cache_status=cache_status)

    def _request(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
    ) -> tuple[httpx.Response | None, list[str], Any | None]:
        chain: list[str] = []
        current = url
        request_headers = dict(headers)
        for _ in range(self.max_redirects + 1):
            response = client.get(current, headers=request_headers or None)
            response_url = str(response.url)
            chain.append(response_url)
            if response.status_code == 304 or not response.is_redirect:
                return response, chain, None
            location = response.headers.get("location")
            if not location:
                return None, chain, make_failure(
                    FetchFailureCode.REDIRECT_ERROR,
                    "Redirect response did not include a Location header.",
                )
            target = urljoin(response_url or current, location)
            safe_target = _validated_url(target)
            if safe_target is None:
                return None, chain, make_failure(
                    FetchFailureCode.SSRF_BLOCKED,
                    "Redirect target failed SSRF validation.",
                )
            current = safe_target
            request_headers = {}
        return None, chain, make_failure(
            FetchFailureCode.REDIRECT_ERROR,
            f"Redirect limit exceeded ({self.max_redirects}).",
        )

    def _extract_response(
        self,
        request: FetchRequest,
        response: httpx.Response,
        body: bytes,
        content_type: str,
        fragment_locator: str | None,
        redirect_chain: list[str],
        cache_status: str,
        cached_entry: FetchCacheEntry | None,
        started: float,
    ) -> FetchResult:
        final_url = str(response.url)
        text = body.decode(response.encoding or "utf-8", errors="replace")
        is_html = "html" in content_type or text.lstrip().lower().startswith(("<!doctype html", "<html"))
        if is_html:
            extraction = extract_html(
                text,
                final_url,
                trafilatura_enabled=self.trafilatura_enabled,
            )
            title = extraction.title
            extracted = extraction.content
            method = extraction.extraction_method
            confidence = extraction.extraction_confidence
            canonical_hint = extraction.canonical_hint
            published_at = extraction.published_at
            tables = list(extraction.tables)
            chain = list(extraction.extraction_chain)
        else:
            title = final_url
            extracted = clean_web_snippet(text, max_chars=max(len(text), request.max_chars))
            method = "plain_text"
            confidence = 0.85
            canonical_hint = None
            published_at = None
            if "csv" in content_type or urlsplit(final_url).path.casefold().endswith(".csv"):
                try:
                    from app.tools.structured_tables import csv_tables

                    tables = csv_tables(text)
                except Exception:
                    tables = []
            else:
                tables = []
            chain = [{"method": method, "success": bool(extracted), "output_length": len(extracted)}]

        truncated = len(extracted) > request.max_chars
        content = extracted[: request.max_chars]
        quality, failure = assess_page_quality(
            content,
            title=title,
            raw_html=text if is_html else "",
            extraction_method=method,
            extraction_confidence=confidence,
            truncated=truncated,
            minimum_score=self.quality_min_score,
            structured_data=bool(tables),
        )
        canonical_url = _trusted_canonical(final_url, canonical_hint)
        normalized_final = canonicalize_url(canonical_url or final_url).normalized_url
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
        lineage = source_lineage(normalized_final, content, {"title": title})
        metadata: dict[str, Any] = {
            "tables": tables,
            "extraction_chain": chain,
            "fetched_at_ms": int((time.monotonic() - started) * 1000),
            "cache_status": cache_status,
            "cache_hit": cache_status in {"hit", "revalidated"},
            "raw_length": len(text),
            "source_identity": lineage.to_dict(),
        }
        status = failure_status(failure) if failure is not None else (
            FetchStatus.PARTIAL if truncated or quality.content_basis == "partial" else FetchStatus.SUCCESS
        )
        result = FetchResult(
            requested_url=request.url,
            final_url=final_url,
            title=title,
            content=content,
            published_at=published_at,
            content_type=content_type or ("text/html" if is_html else "text/plain"),
            content_basis=quality.content_basis,
            extraction_method=method,
            extraction_confidence=confidence,
            fetch_status=status,
            fetch_backend=FetchBackend.HTTP,
            provider="local_http",
            quality=quality,
            failure=failure,
            failure_reason=failure.message if failure else None,
            content_hash=content_hash,
            canonical_url=normalized_final,
            canonical_hint=canonical_hint,
            fragment_locator=fragment_locator,
            redirect_chain=redirect_chain,
            metadata=metadata,
        )
        if self.cache_enabled and self.cache is not None and result.usable and content_hash:
            entry = FetchCacheEntry(
                cache_key=self.cache._compute_key(
                    canonicalize_url(request.url).normalized_url,
                    self._cache_params(),
                ),
                url=canonicalize_url(request.url).normalized_url,
                content_hash=content_hash,
                content=content,
                content_type=result.content_type,
                fetched_at=time.time(),
                ttl_seconds=self.cache_ttl_seconds,
                etag=response.headers.get("etag"),
                extraction_method=method,
                extraction_confidence=confidence,
                metadata={
                    "title": title,
                    "final_url": final_url,
                    "canonical_url": normalized_final,
                    "canonical_hint": canonical_hint,
                    "published_at": published_at,
                    "redirect_chain": redirect_chain,
                    "quality": quality.to_dict(),
                    "tables": tables,
                    "extraction_chain": chain,
                    "source_identity": lineage.to_dict(),
                },
            )
            if self.cache.put(entry):
                result.metadata["cache_stored"] = True
        return result

    def _from_cache(
        self,
        request: FetchRequest,
        fragment_locator: str | None,
        entry: FetchCacheEntry,
        status: str,
    ) -> FetchResult:
        meta = dict(entry.metadata)
        content = entry.content[: request.max_chars]
        truncated = len(entry.content) > request.max_chars
        quality, failure = assess_page_quality(
            content,
            title=str(meta.get("title") or entry.url),
            extraction_method=entry.extraction_method,
            extraction_confidence=entry.extraction_confidence,
            truncated=truncated,
            minimum_score=self.quality_min_score,
        )
        result_status = failure_status(failure) if failure else (
            FetchStatus.PARTIAL if truncated or quality.content_basis == "partial" else FetchStatus.SUCCESS
        )
        return FetchResult(
            requested_url=request.url,
            final_url=str(meta.get("final_url") or entry.url),
            title=str(meta.get("title") or entry.url),
            content=content,
            published_at=meta.get("published_at"),
            content_type=entry.content_type,
            content_basis=quality.content_basis,
            extraction_method=entry.extraction_method,
            extraction_confidence=entry.extraction_confidence,
            fetch_status=result_status,
            fetch_backend=FetchBackend.CACHE,
            provider="local_cache",
            quality=quality,
            failure=failure,
            failure_reason=failure.message if failure else None,
            content_hash=entry.content_hash,
            canonical_url=str(meta.get("canonical_url") or entry.url),
            canonical_hint=meta.get("canonical_hint"),
            fragment_locator=fragment_locator,
            redirect_chain=list(meta.get("redirect_chain") or []),
            metadata={
                "tables": list(meta.get("tables") or []),
                "extraction_chain": list(meta.get("extraction_chain") or []),
                "source_identity": dict(meta.get("source_identity") or {}),
                "fetched_at_ms": 0,
                "cache_status": status,
                "cache_hit": True,
                "cache_age_seconds": round(entry.age_seconds, 3),
                "cache_fetched_at": entry.fetched_at,
            },
        )

    def _failed(
        self,
        requested_url: str,
        failure: Any,
        started: float,
        *,
        final_url: str | None = None,
        redirect_chain: list[str] | None = None,
        cache_status: str = "not_applicable",
        content_type: str = "",
        extra: dict[str, Any] | None = None,
    ) -> FetchResult:
        return FetchResult(
            requested_url=requested_url,
            final_url=final_url,
            content_type=content_type,
            fetch_status=failure_status(failure),
            fetch_backend=FetchBackend.HTTP,
            provider="local_http",
            failure=failure,
            failure_reason=failure.message,
            canonical_url=(canonicalize_url(final_url or requested_url).normalized_url),
            redirect_chain=list(redirect_chain or []),
            metadata={
                "fetched_at_ms": int((time.monotonic() - started) * 1000),
                "cache_status": cache_status,
                "cache_hit": False,
                **(extra or {}),
            },
        )

    def _cache_params(self) -> dict[str, Any]:
        return {
            "extractor_version": "retrieval-r11-v1",
            "trafilatura_enabled": self.trafilatura_enabled,
            "max_response_bytes": self.max_response_bytes,
        }


def _validated_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        # Accessing ``port`` also rejects malformed and out-of-range ports.
        _ = parsed.port
        if parsed.username is not None or parsed.password is not None or parsed.netloc.endswith(":"):
            return None
    except ValueError:
        return None
    return validate_url(url)


def _read_bounded(response: httpx.Response, max_bytes: int) -> tuple[bytes | None, int]:
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes(chunk_size=65_536):
        received += len(chunk)
        if received > max_bytes:
            return None, received
        chunks.append(chunk)
    return b"".join(chunks), received


def _content_length(headers: httpx.Headers) -> int | None:
    try:
        value = headers.get("content-length")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_pdf(content_type: str, url: str, body: bytes) -> bool:
    return "application/pdf" in content_type or urlsplit(url).path.casefold().endswith(".pdf") or body.startswith(PDF_MAGIC)


def _supported_content_type(content_type: str) -> bool:
    if not content_type:
        return True
    return any(content_type.startswith(prefix) for prefix in SUPPORTED_TEXT_TYPES)


def _trusted_canonical(final_url: str, hint: str | None) -> str | None:
    resolved = resolve_canonical_hint(final_url, hint)
    if not resolved:
        return None
    try:
        if urlsplit(resolved).hostname != urlsplit(final_url).hostname:
            return None
    except ValueError:
        return None
    return resolved if _validated_url(resolved) is not None else None


__all__ = ["HttpBackend", "PDF_MAGIC", "_read_bounded"]
