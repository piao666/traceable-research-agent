"""Optional isolated Playwright backend for pages that require rendering."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.retrieval.classifier import classify_exception, classify_http_status, failure_status, make_failure
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
from app.tools.ssrf import validate_url


_BROWSER_SEMAPHORE = threading.BoundedSemaphore(2)
_BROWSER_SEMAPHORE_SIZE = 2
_BROWSER_SEMAPHORE_LOCK = threading.Lock()


def configure_browser_concurrency(max_concurrency: int) -> None:
    """Set the process-wide Browser capacity before backends start work."""

    global _BROWSER_SEMAPHORE, _BROWSER_SEMAPHORE_SIZE
    selected = max(1, min(int(max_concurrency), 8))
    with _BROWSER_SEMAPHORE_LOCK:
        if selected != _BROWSER_SEMAPHORE_SIZE:
            _BROWSER_SEMAPHORE = threading.BoundedSemaphore(selected)
            _BROWSER_SEMAPHORE_SIZE = selected


@dataclass(frozen=True)
class RenderedPage:
    final_url: str
    html: str
    title: str = ""
    redirect_chain: tuple[str, ...] = ()


class PageLoader(Protocol):
    def load(self, request: FetchRequest) -> RenderedPage: ...


class PlaywrightPageLoader:
    """Create a fresh non-persistent browser context for every page."""

    def __init__(
        self,
        *,
        wait_until: str = "domcontentloaded",
        max_response_bytes: int = 10_485_760,
    ) -> None:
        self.wait_until = wait_until
        self.max_response_bytes = max_response_bytes

    def load(self, request: FetchRequest) -> RenderedPage:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright_not_installed") from exc

        with _BROWSER_SEMAPHORE, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    accept_downloads=False,
                    java_script_enabled=True,
                    service_workers="block",
                )
                page = context.new_page()

                def guard(route) -> None:
                    target = str(route.request.url)
                    if validate_url(target) is None:
                        route.abort("blockedbyclient")
                    else:
                        route.continue_()

                page.route("**/*", guard)
                response = page.goto(
                    request.url,
                    wait_until=self.wait_until,
                    timeout=request.timeout_seconds * 1000,
                )
                final_url = page.url
                if validate_url(final_url) is None:
                    raise RuntimeError("unsafe_final_url")
                status = response.status if response is not None else 200
                if status >= 400:
                    raise RuntimeError(f"browser_http_{status}")
                if response is not None:
                    try:
                        length = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        length = 0
                    if length > self.max_response_bytes:
                        raise RuntimeError("browser_content_too_large")
                page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight, 4000))")
                page.wait_for_timeout(200)
                try:
                    page.wait_for_load_state("networkidle", timeout=1500)
                except Exception:
                    pass
                html = page.content()
                if len(html.encode("utf-8")) > self.max_response_bytes:
                    raise RuntimeError("browser_content_too_large")
                return RenderedPage(
                    final_url=final_url,
                    html=html,
                    title=page.title(),
                    redirect_chain=(request.url, final_url) if final_url != request.url else (final_url,),
                )
            finally:
                browser.close()


class BrowserBackend:
    name = FetchBackend.BROWSER

    def __init__(
        self,
        loader: PageLoader | None = None,
        *,
        enabled: bool = False,
        timeout_seconds: int = 20,
        quality_min_score: float = 0.55,
        max_response_bytes: int = 10_485_760,
    ) -> None:
        self.loader = loader or PlaywrightPageLoader(max_response_bytes=max_response_bytes)
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.quality_min_score = quality_min_score

    def fetch(self, request: FetchRequest) -> FetchResult:
        started = time.monotonic()
        if not self.enabled:
            return self._unavailable(request, "Browser retrieval is disabled.", started)
        if validate_url(request.url) is None:
            failure = make_failure(FetchFailureCode.SSRF_BLOCKED, "Browser URL failed SSRF validation.")
            return self._failure(request, failure, started)
        try:
            browser_request = request.model_copy(
                update={"timeout_seconds": min(request.timeout_seconds, self.timeout_seconds)}
            )
            rendered = self.loader.load(browser_request)
            if validate_url(rendered.final_url) is None:
                failure = make_failure(FetchFailureCode.SSRF_BLOCKED, "Rendered final URL failed SSRF validation.")
                return self._failure(request, failure, started)
            extraction = extract_html(rendered.html, rendered.final_url)
            title = rendered.title.strip() or extraction.title
            truncated = len(extraction.content) > request.max_chars
            content = extraction.content[: request.max_chars]
            quality, quality_failure = assess_page_quality(
                content,
                title=title,
                raw_html=rendered.html,
                extraction_method=extraction.extraction_method,
                extraction_confidence=extraction.extraction_confidence,
                truncated=truncated,
                minimum_score=self.quality_min_score,
            )
            canonical_url = _trusted_canonical(rendered.final_url, extraction.canonical_hint)
            canonical_url = canonicalize_url(canonical_url or rendered.final_url).normalized_url
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
            status = failure_status(quality_failure) if quality_failure else (
                FetchStatus.PARTIAL if truncated or quality.content_basis == "partial" else FetchStatus.SUCCESS
            )
            return FetchResult(
                requested_url=request.url,
                final_url=rendered.final_url,
                title=title,
                content=content,
                published_at=extraction.published_at,
                content_type="text/html",
                content_basis=quality.content_basis,
                extraction_method=f"browser_{extraction.extraction_method}",
                extraction_confidence=extraction.extraction_confidence,
                fetch_status=status,
                fetch_backend=FetchBackend.BROWSER,
                provider="local_playwright",
                quality=quality,
                failure=quality_failure,
                failure_reason=quality_failure.message if quality_failure else None,
                content_hash=content_hash,
                canonical_url=canonical_url,
                canonical_hint=extraction.canonical_hint,
                fragment_locator=canonicalize_url(request.url).fragment_locator,
                redirect_chain=list(rendered.redirect_chain),
                metadata={
                    "tables": list(extraction.tables),
                    "extraction_chain": list(extraction.extraction_chain),
                    "source_identity": source_lineage(canonical_url, content, {"title": title}).to_dict(),
                    "fetched_at_ms": int((time.monotonic() - started) * 1000),
                    "browser_context": "isolated_non_persistent",
                    "downloads_allowed": False,
                    "js_render_recovery": True,
                },
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            text = str(exc).casefold()
            if "playwright_not_installed" in text:
                return self._unavailable(request, "Playwright is not installed.", started)
            if "unsafe_final_url" in text:
                failure = make_failure(FetchFailureCode.SSRF_BLOCKED, "Browser reached an unsafe final URL.")
            elif "browser_http_" in text:
                try:
                    failure = classify_http_status(int(text.rsplit("browser_http_", 1)[1].split()[0]))
                except (ValueError, IndexError):
                    failure = make_failure(FetchFailureCode.UNKNOWN, type(exc).__name__)
            elif "timeout" in text:
                failure = make_failure(FetchFailureCode.TIMEOUT, "Browser navigation timed out.")
            elif "content_too_large" in text:
                failure = make_failure(
                    FetchFailureCode.CONTENT_TOO_LARGE,
                    "Rendered page exceeded the configured response-size limit.",
                )
            else:
                failure = classify_exception(exc)
            return self._failure(request, failure, started)

    def _unavailable(self, request: FetchRequest, message: str, started: float) -> FetchResult:
        failure = make_failure(
            FetchFailureCode.BACKEND_UNAVAILABLE,
            message,
            next_strategy=FetchBackend.REMOTE_EXTRACT,
            tool_scoped=True,
        )
        return self._failure(request, failure, started)

    @staticmethod
    def _failure(request: FetchRequest, failure, started: float) -> FetchResult:
        return FetchResult(
            requested_url=request.url,
            canonical_url=canonicalize_url(request.url).normalized_url,
            fetch_status=failure_status(failure),
            fetch_backend=FetchBackend.BROWSER,
            provider="local_playwright",
            failure=failure,
            failure_reason=failure.message,
            metadata={"fetched_at_ms": int((time.monotonic() - started) * 1000)},
        )


def _trusted_canonical(final_url: str, hint: str | None) -> str | None:
    resolved = resolve_canonical_hint(final_url, hint)
    if not resolved:
        return None
    try:
        same_host = urlsplit(resolved).hostname == urlsplit(final_url).hostname
    except ValueError:
        same_host = False
    return resolved if same_host and validate_url(resolved) is not None else None


__all__ = [
    "BrowserBackend",
    "PageLoader",
    "PlaywrightPageLoader",
    "RenderedPage",
    "configure_browser_concurrency",
]
