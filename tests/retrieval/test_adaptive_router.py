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
from app.retrieval.pdf_backend import PdfBackend
from app.retrieval.remote_extract import RemoteExtractBackend, normalize_remote_payload
from app.retrieval.router import RetrievalRouter
from app.tools.base import ToolResult
from app.tools.web_fetcher import web_fetch


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


def test_pdf_backend_preserves_page_locators() -> None:
    def reader(arguments):
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
    assert result.metadata["page_locators"] == [
        {"page_number": 1, "char_count": len(CONTENT)},
        {"page_number": 2, "char_count": len(CONTENT)},
    ]


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
