"""R11 browser, remote, PDF and adaptive routing tests."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from app.retrieval.browser_backend import BrowserBackend, RenderedPage
from app.retrieval.classifier import make_failure
from app.retrieval.contracts import (
    ContentQualityAssessment,
    FetchBackend,
    FetchFailureCode,
    FetchRequest,
    FetchResult,
    FetchStatus,
)
from app.retrieval.pdf_backend import PDF_SOURCE_MAX_CHARS, PdfBackend
from app.retrieval.remote_extract import RemoteExtractBackend, normalize_remote_payload
from app.retrieval.router import RetrievalRouter
from app.tools.base import ToolResult
from app.tools.web_fetcher import web_fetch
from app.mcp_bridge.providers.exa import _normalize_exa_result


SAFE_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
CONTENT = "Rendered research evidence with detailed findings and source context. " * 20


class FixtureLoader:
    def load(self, request: FetchRequest) -> RenderedPage:
        return RenderedPage(
            final_url="https://example.com/rendered",
            html=f"<html><title>Rendered title</title><main>{CONTENT}</main></html>",
            title="Rendered title",
            redirect_chain=(request.url, "https://example.com/rendered"),
        )


@dataclass
class FixtureRemoteProvider:
    name: str = "fixture_remote"
    enabled: bool = True

    def available(self) -> bool:
        return self.enabled

    def extract(self, request: FetchRequest):
        return {
            "success": True,
            "output": {
                "results": [
                    {
                        "url": request.url,
                        "title": "Remote title",
                        "content": CONTENT,
                        "publishedDate": "2026-09-09",
                    }
                ]
            },
        }


class StubBackend:
    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls = 0

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls += 1
        return self.result.model_copy(deep=True)


def _quality() -> ContentQualityAssessment:
    return ContentQualityAssessment(
        usable=True,
        quality_score=0.9,
        content_length=len(CONTENT),
        boilerplate_ratio=0,
        language="en",
        title_quality=1,
        extraction_confidence=0.9,
        content_basis="full_text",
    )


def _success(backend: FetchBackend) -> FetchResult:
    return FetchResult(
        requested_url="https://example.com/a",
        final_url="https://example.com/a",
        content=CONTENT,
        content_basis="full_text",
        fetch_status=FetchStatus.SUCCESS,
        fetch_backend=backend,
        quality=_quality(),
    )


def _failure(backend: FetchBackend, code: FetchFailureCode) -> FetchResult:
    failure = make_failure(code)
    return FetchResult(
        requested_url="https://example.com/a",
        fetch_status=FetchStatus.BLOCKED,
        fetch_backend=backend,
        failure=failure,
        failure_reason=failure.message,
    )


def test_browser_backend_uses_isolated_loader_contract() -> None:
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = BrowserBackend(FixtureLoader(), enabled=True).fetch(
            FetchRequest(url="https://example.com/app")
        )
    assert result.usable
    assert result.fetch_backend == FetchBackend.BROWSER
    assert result.final_url == "https://example.com/rendered"
    assert result.metadata["browser_context"] == "isolated_non_persistent"
    assert result.metadata["downloads_allowed"] is False


def test_browser_source_identity_is_invariant_across_8k_and_50k_views() -> None:
    source = "<p>" + ("Stable browser source identity evidence. " * 2200) + "</p>"

    class LargeFixtureLoader:
        def load(self, request: FetchRequest) -> RenderedPage:
            return RenderedPage(
                final_url="https://example.com/CaseSensitive/Story",
                html=f"<html><title>Stable story</title><main>{source}</main></html>",
                title="Stable story",
                redirect_chain=(request.url, "https://example.com/CaseSensitive/Story"),
            )

    backend = BrowserBackend(LargeFixtureLoader(), enabled=True)
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        small = backend.fetch(
            FetchRequest(url="https://example.com/app", max_chars=8000)
        )
        large = backend.fetch(
            FetchRequest(url="https://example.com/app", max_chars=50000)
        )

    assert len(small.content) == 8000
    assert len(large.content) == 50000
    assert small.content_hash == large.content_hash
    assert small.source_content_hash == large.source_content_hash
    assert small.metadata["source_content_length"] == large.metadata["source_content_length"]
    assert small.metadata["source_identity"]["canonical_story_hash"] == (
        large.metadata["source_identity"]["canonical_story_hash"]
    )
    assert small.metadata["source_identity"]["independence_group"] == (
        large.metadata["source_identity"]["independence_group"]
    )
    assert small.metadata["view_truncated"] is True
    assert large.metadata["view_truncated"] is True


def test_disabled_browser_is_nonfatal_backend_unavailable() -> None:
    result = BrowserBackend(FixtureLoader(), enabled=False).fetch(
        FetchRequest(url="https://example.com/app")
    )
    assert result.failure and result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.failure.tool_scoped
    assert result.failure.recommended_next_strategy == FetchBackend.REMOTE_EXTRACT


def test_remote_payload_normalizes_firecrawl_and_exa_shapes() -> None:
    firecrawl, firecrawl_error = normalize_remote_payload(
        {
            "success": True,
            "output": {
                "url": "https://example.com/a",
                "title": "A",
                "markdown": CONTENT,
                "metadata": {"sourceURL": "https://example.com/a"},
            },
        },
        "https://example.com/a",
    )
    exa, exa_error = normalize_remote_payload(
        {"output": {"results": [{"url": "https://example.com/a", "text": CONTENT}]}},
        "https://example.com/a",
    )
    assert firecrawl_error is None and firecrawl and firecrawl["content"] == CONTENT.strip()
    assert exa_error is None and exa and exa["content"] == CONTENT.strip()


def test_remote_backend_returns_provider_identity_and_attempts() -> None:
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = RemoteExtractBackend([FixtureRemoteProvider()], enabled=True).fetch(
            FetchRequest(url="https://example.com/a")
        )
    assert result.usable
    assert result.provider == "fixture_remote"
    assert result.metadata["provider_attempts"][0]["status"] == "success"


def test_remote_identity_uses_source_not_requested_view() -> None:
    source = "Remote source identity evidence. " * 2200

    @dataclass
    class LargeRemoteProvider(FixtureRemoteProvider):
        def extract(self, request: FetchRequest):
            return {
                "success": True,
                "output": {
                    "results": [
                        {"url": request.url, "title": "Remote", "content": source}
                    ]
                },
            }

    backend = RemoteExtractBackend([LargeRemoteProvider()], enabled=True)
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        small = backend.fetch(FetchRequest(url="https://example.com/a", max_chars=8000))
        large = backend.fetch(FetchRequest(url="https://example.com/a", max_chars=50000))

    assert small.content_hash == large.content_hash
    assert small.source_content_hash == large.source_content_hash
    assert small.metadata["source_identity"] == large.metadata["source_identity"]


def test_resource_identity_is_stable_when_backends_return_different_final_urls() -> None:
    request_url = "https://example.com/requested/article"

    class RedirectingLoader:
        def load(self, request: FetchRequest) -> RenderedPage:
            return RenderedPage(
                final_url="https://browser.example/rendered/article",
                html=f"<html><title>Article</title><main>{CONTENT}</main></html>",
                title="Article",
                redirect_chain=(request.url, "https://browser.example/rendered/article"),
            )

    @dataclass
    class RedirectingRemoteProvider(FixtureRemoteProvider):
        def extract(self, request: FetchRequest):
            return {
                "success": True,
                "output": {
                    "results": [
                        {
                            "url": "https://remote.example/extracted/article",
                            "title": "Article",
                            "content": CONTENT,
                        }
                    ]
                },
            }

    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        browser = BrowserBackend(RedirectingLoader(), enabled=True).fetch(
            FetchRequest(url=request_url)
        )
        remote = RemoteExtractBackend(
            [RedirectingRemoteProvider()], enabled=True
        ).fetch(FetchRequest(url=request_url))

    browser_identity = browser.metadata["source_identity"]
    remote_identity = remote.metadata["source_identity"]
    assert browser_identity["resource_identity"] == remote_identity["resource_identity"]
    assert browser_identity["independence_group"] == remote_identity["independence_group"]
    assert browser_identity["resource_identity"] == request_url


def test_remote_provider_truncation_propagates_partial_source_basis() -> None:
    raw_content = ("Remote provider evidence sentence. " * 2500)[:79_999] + "."

    @dataclass
    class TruncatingRemoteProvider(FixtureRemoteProvider):
        def extract(self, request: FetchRequest):
            page = _normalize_exa_result(
                {"url": request.url, "title": "Remote", "text": raw_content},
                50_000,
            )
            return {"success": True, "output": {"results": [page]}}

    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = RemoteExtractBackend(
            [TruncatingRemoteProvider()], enabled=True
        ).fetch(FetchRequest(url="https://example.com/a", max_chars=8_000))

    assert result.metadata["provider_content_original_length"] == 80_000
    assert result.metadata["provider_content_returned_length"] == 50_000
    assert result.metadata["provider_content_limit"] == 50_000
    assert result.metadata["provider_content_truncated"] is True
    assert result.metadata["source_content_length"] == 50_000
    assert result.metadata["source_truncated_at_backend_limit"] is True
    assert result.metadata["view_truncated"] is True
    assert result.content_basis == "partial"


def test_remote_envelope_truncation_metadata_is_preserved() -> None:
    page, error = normalize_remote_payload(
        {
            "success": True,
            "metadata": {
                "provider_content_original_length": 120_000,
                "provider_content_returned_length": 50_000,
                "provider_content_limit": 50_000,
                "provider_content_truncated": True,
            },
            "output": {
                "results": [
                    {"url": "https://example.com/a", "content": CONTENT}
                ]
            },
        },
        "https://example.com/a",
    )
    assert error is None and page is not None
    assert page["metadata"]["provider_content_truncated"] is True
    assert page["metadata"]["provider_content_original_length"] == 120_000


def test_router_falls_back_http_to_browser_and_stops_on_success() -> None:
    http = StubBackend(_failure(FetchBackend.HTTP, FetchFailureCode.JAVASCRIPT_REQUIRED))
    browser = StubBackend(_success(FetchBackend.BROWSER))
    remote = StubBackend(_success(FetchBackend.REMOTE_EXTRACT))
    result = RetrievalRouter(http_backend=http, browser_backend=browser, remote_backend=remote).fetch(
        FetchRequest(url="https://example.com/a")
    )
    assert result.usable
    assert http.calls == 1 and browser.calls == 1 and remote.calls == 0
    assert [item["backend"] for item in result.metadata["retrieval_attempts"]] == ["http", "browser"]


def test_router_converts_backend_assertion_to_structured_unavailable() -> None:
    class BrokenBackend:
        def fetch(self, request: FetchRequest) -> FetchResult:
            raise AssertionError("backend invariant")

    result = RetrievalRouter(http_backend=BrokenBackend()).fetch(
        FetchRequest(
            url="https://example.com/a",
            preferred_backends=[FetchBackend.HTTP],
            allow_browser=False,
            allow_remote_extract=False,
        )
    )
    assert result.failure is not None
    assert result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.failure.tool_scoped is True
    assert result.metadata["retrieval_attempts"][0]["failure_code"] == "backend_unavailable"


def test_router_does_not_escalate_terminal_404() -> None:
    http_result = _failure(FetchBackend.HTTP, FetchFailureCode.HTTP_404)
    http_result.fetch_status = FetchStatus.NOT_FOUND
    http = StubBackend(http_result)
    browser = StubBackend(_success(FetchBackend.BROWSER))
    result = RetrievalRouter(http_backend=http, browser_backend=browser).fetch(
        FetchRequest(url="https://example.com/missing")
    )
    assert not result.usable
    assert http.calls == 1 and browser.calls == 0


def test_router_returns_structured_failure_when_only_browser_is_disallowed() -> None:
    result = RetrievalRouter(
        http_backend=StubBackend(_success(FetchBackend.HTTP)),
        browser_backend=StubBackend(_success(FetchBackend.BROWSER)),
    ).fetch(
        FetchRequest(
            url="https://example.com/a",
            preferred_backends=[FetchBackend.BROWSER],
            allow_browser=False,
        )
    )

    assert result.fetch_status == FetchStatus.PROVIDER_ERROR
    assert result.failure and result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.failure.tool_scoped is True
    assert result.failure.page_scoped is False
    assert result.failure.message == "No permitted retrieval backend was available for this request."
    assert result.metadata == {
        "retrieval_attempts": [],
        "retrieval_attempt_count": 0,
        "requested_backends": ["browser"],
        "browser_allowed": False,
        "remote_extract_allowed": True,
    }


def test_router_returns_structured_failure_when_only_remote_is_disallowed() -> None:
    result = RetrievalRouter(
        http_backend=StubBackend(_success(FetchBackend.HTTP)),
        remote_backend=StubBackend(_success(FetchBackend.REMOTE_EXTRACT)),
    ).fetch(
        FetchRequest(
            url="https://example.com/a",
            preferred_backends=[FetchBackend.REMOTE_EXTRACT],
            allow_remote_extract=False,
        )
    )

    assert result.failure and result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.metadata["requested_backends"] == ["remote_extract"]
    assert result.metadata["remote_extract_allowed"] is False


def test_router_returns_structured_failure_when_preferred_backend_is_unconfigured() -> None:
    result = RetrievalRouter(
        http_backend=StubBackend(_success(FetchBackend.HTTP)),
        browser_backend=None,
    ).fetch(
        FetchRequest(
            url="https://example.com/a",
            preferred_backends=[FetchBackend.BROWSER],
        )
    )

    assert result.failure and result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.metadata["retrieval_attempt_count"] == 0


def test_router_returns_structured_failure_when_all_requested_backends_are_disabled() -> None:
    result = RetrievalRouter(
        http_backend=StubBackend(_success(FetchBackend.HTTP)),
        browser_backend=StubBackend(_success(FetchBackend.BROWSER)),
        remote_backend=StubBackend(_success(FetchBackend.REMOTE_EXTRACT)),
    ).fetch(
        FetchRequest(
            url="https://example.com/a",
            preferred_backends=[FetchBackend.BROWSER, FetchBackend.REMOTE_EXTRACT],
            allow_browser=False,
            allow_remote_extract=False,
        )
    )

    assert result.failure and result.failure.code == FetchFailureCode.BACKEND_UNAVAILABLE
    assert result.metadata["requested_backends"] == ["browser", "remote_extract"]
    assert result.metadata["retrieval_attempts"] == []


def test_pdf_backend_preserves_page_locators() -> None:
    reader_arguments = []

    def reader(arguments):
        reader_arguments.append(arguments)
        return ToolResult(
            success=True,
            output={
                "documents": [
                    {
                        "path": arguments["paths"][0],
                        "title": "PDF evidence",
                        "pages": [
                            {"page_number": 1, "text": CONTENT, "char_count": len(CONTENT)},
                            {"page_number": 2, "text": CONTENT, "char_count": len(CONTENT)},
                        ],
                        "integrity": {"truncated": False, "actual_pages": 2},
                        "metadata": {"author": "Researcher"},
                        "content_basis": "full_text",
                        "extraction_method": "native",
                        "error": None,
                    }
                ]
            },
        )

    result = PdfBackend(reader).fetch(FetchRequest(url="https://example.com/evidence.pdf"))
    assert result.usable
    assert result.provider == "local_pdf_reader"
    assert reader_arguments[0]["max_chars"] == PDF_SOURCE_MAX_CHARS
    assert result.metadata["page_locators"] == [
        {"page_number": 1, "char_count": len(CONTENT)},
        {"page_number": 2, "char_count": len(CONTENT)},
    ]


def test_pdf_identity_uses_fixed_source_extraction_for_different_views() -> None:
    source = "PDF source identity evidence. " * 2200

    def reader(arguments):
        assert arguments["max_chars"] == PDF_SOURCE_MAX_CHARS
        return ToolResult(
            success=True,
            output={
                "documents": [
                    {
                        "path": arguments["paths"][0],
                        "title": "PDF evidence",
                        "pages": [
                            {
                                "page_number": 1,
                                "text": source,
                                "char_count": len(source),
                            }
                        ],
                        "integrity": {"truncated": False, "actual_pages": 1},
                        "metadata": {"author": "Researcher"},
                        "content_basis": "full_text",
                        "extraction_method": "native",
                    }
                ]
            },
        )

    backend = PdfBackend(reader)
    small = backend.fetch(
        FetchRequest(url="https://example.com/evidence.pdf", max_chars=8000)
    )
    large = backend.fetch(
        FetchRequest(url="https://example.com/evidence.pdf", max_chars=50000)
    )

    assert small.content_hash == large.content_hash
    assert small.source_content_hash == large.source_content_hash
    assert small.metadata["source_identity"] == large.metadata["source_identity"]


def test_web_fetcher_deduplicates_canonical_and_content_identities() -> None:
    from app.config import Settings

    first = _success(FetchBackend.HTTP)
    first.requested_url = "https://example.com/a"
    first.canonical_url = "https://example.com/a"
    first.content_hash = "same-hash"
    second = first.model_copy(deep=True)
    second.requested_url = "https://mirror.example/a"
    second.canonical_url = "https://mirror.example/a"

    class SequenceRouter:
        def __init__(self) -> None:
            self.results = [first, second]
            self.calls = 0

        def fetch(self, request: FetchRequest) -> FetchResult:
            self.calls += 1
            return self.results.pop(0)

    router = SequenceRouter()
    result = web_fetch(
        {
            "urls": [
                "https://example.com/a?utm_source=x",
                "https://example.com/a#section",
                "https://mirror.example/a",
            ]
        },
        settings_obj=Settings(web_fetcher_cache_enabled=False),
        router=router,  # type: ignore[arg-type]
    )
    assert router.calls == 2
    assert result.metadata["canonical_deduplicated_count"] == 1
    assert result.metadata["content_deduplicated_count"] == 1
    assert result.output["pages"][1]["duplicate_reason"] == "canonical_url"
    assert result.output["pages"][2]["duplicate_reason"] == "content_hash"


def test_web_fetch_metadata_materializes_only_first_duplicate_as_evidence() -> None:
    from app.agent.evidence import _web_page_items

    record = {
        "trace_id": "trace-r11",
        "run_id": "run-r11",
        "step_no": 1,
        "tool_name": "web_fetcher",
        "success": True,
        "status": "success",
        "metadata": {},
        "output": {
            "pages": [
                {
                    "url": "https://example.com/a?utm_source=x",
                    "requested_url": "https://example.com/a?utm_source=x",
                    "final_url": "https://example.com/a",
                    "canonical_url": "https://example.com/a",
                    "title": "A",
                    "content": CONTENT,
                    "content_basis": "full_text",
                    "extraction_method": "beautifulsoup",
                    "extraction_confidence": 0.7,
                    "fetch_status": "success",
                    "provider": "local_http",
                    "source_identity": {"independence_group": "srcgrp_a"},
                },
                {
                    "url": "https://mirror.example/a",
                    "title": "A duplicate",
                    "content": CONTENT,
                    "content_basis": "full_text",
                    "deduplicated": True,
                    "duplicate_reason": "content_hash",
                },
            ]
        },
    }
    items = _web_page_items("run-r11", record, 0)
    assert len(items) == 1
    assert items[0].source_ref == "https://example.com/a"
    assert items[0].metadata["fetch_status"] == "success"
    assert items[0].metadata["source_identity"]["independence_group"] == "srcgrp_a"
