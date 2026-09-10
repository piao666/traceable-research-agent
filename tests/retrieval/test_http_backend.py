"""R11 HTTP backend contract, safety, extraction, and failure tests."""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import httpx

from app.retrieval.contracts import FetchBackend, FetchFailureCode, FetchRequest, FetchStatus
from app.retrieval.http_backend import HttpBackend
from app.tools.fetch_cache import FetchCache


SAFE_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
ARTICLE = "Research evidence sentence with meaningful detail. " * 20
HTML = f"""<html><head><title>Reliable source</title>
<link rel="canonical" href="/canonical?utm_source=test">
<meta property="article:published_time" content="2026-09-09T00:00:00Z">
</head><body><main>{ARTICLE}</main></body></html>"""


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_backend_extracts_audited_page_contract() -> None:
    client = _client(
        lambda request: httpx.Response(
            200,
            text=HTML,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )
    )
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = HttpBackend(client=client, cache_enabled=False, trafilatura_enabled=False).fetch(
            FetchRequest(url="https://EXAMPLE.com/article?utm_campaign=x", max_chars=4000)
        )

    assert result.usable
    assert result.fetch_status == FetchStatus.SUCCESS
    assert result.fetch_backend == FetchBackend.HTTP
    assert result.canonical_url == "https://example.com/canonical"
    assert result.published_at == "2026-09-09T00:00:00Z"
    assert result.content_basis == "full_text"
    assert result.extraction_method == "beautifulsoup"
    assert result.quality and result.quality.quality_score > 0.5
    assert result.metadata["extraction_chain"]
    assert result.metadata["source_identity"]["independence_group"].startswith("srcgrp_")


def test_http_backend_rejects_credentials_without_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=HTML, request=request)

    result = HttpBackend(client=_client(handler), cache_enabled=False).fetch(
        FetchRequest(url="https://user:secret@example.com/private")
    )
    assert calls == 0
    assert result.fetch_status == FetchStatus.UNSAFE_URL
    assert result.failure and result.failure.code == FetchFailureCode.SSRF_BLOCKED


def test_http_backend_classifies_challenge_and_recommends_browser() -> None:
    html = "<html><title>Checking your browser</title><body>cf-chl- verify</body></html>"
    client = _client(lambda request: httpx.Response(200, text=html, request=request))
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = HttpBackend(client=client, cache_enabled=False, trafilatura_enabled=False).fetch(
            FetchRequest(url="https://example.com/challenge")
        )
    assert not result.usable
    assert result.fetch_status == FetchStatus.BLOCKED
    assert result.failure and result.failure.code == FetchFailureCode.CLOUDFLARE_CHALLENGE
    assert result.failure.recommended_next_strategy == FetchBackend.BROWSER


def test_http_backend_blocks_unsafe_redirect_before_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}, request=request)

    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = HttpBackend(client=_client(handler), cache_enabled=False).fetch(
            FetchRequest(url="https://example.com/redirect")
        )
    assert calls == ["https://example.com/redirect"]
    assert result.fetch_status == FetchStatus.UNSAFE_URL
    assert result.failure and result.failure.code == FetchFailureCode.SSRF_BLOCKED


def test_http_backend_routes_pdf_and_rejects_oversized_content() -> None:
    responses = [
        httpx.Response(200, content=b"%PDF-1.7\nfixture", headers={"content-type": "application/pdf"}),
        httpx.Response(200, content=b"x" * 101, headers={"content-type": "text/plain"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    backend = HttpBackend(client=_client(handler), cache_enabled=False, max_response_bytes=100)
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        pdf = backend.fetch(FetchRequest(url="https://example.com/download"))
        large = backend.fetch(FetchRequest(url="https://example.com/large"))
    assert pdf.failure and pdf.failure.code == FetchFailureCode.PDF_ROUTED
    assert large.fetch_status == FetchStatus.CONTENT_TOO_LARGE


def test_http_backend_cache_hit_uses_unified_cache_backend() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"}, request=request)

    with tempfile.TemporaryDirectory() as cache_dir:
        cache = FetchCache(cache_dir, default_ttl=60)
        backend = HttpBackend(
            client=_client(handler),
            cache=cache,
            cache_enabled=True,
            trafilatura_enabled=False,
        )
        with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
            first = backend.fetch(FetchRequest(url="https://example.com/article"))
            second = backend.fetch(FetchRequest(url="https://example.com/article"))
    assert calls == 1
    assert first.metadata["cache_stored"] is True
    assert second.fetch_backend == FetchBackend.CACHE
    assert second.metadata["cache_status"] == "hit"
    assert second.usable


def test_http_backend_preserves_csv_tables_for_structured_research() -> None:
    csv = "date,value\n2026-09-01,10\n2026-09-02,11\n"
    client = _client(
        lambda request: httpx.Response(
            200,
            text=csv,
            headers={"content-type": "text/csv"},
            request=request,
        )
    )
    with patch("app.tools.ssrf.socket.getaddrinfo", return_value=SAFE_DNS):
        result = HttpBackend(
            client=client,
            cache_enabled=False,
            quality_min_score=0.0,
        ).fetch(FetchRequest(url="https://example.com/data.csv"))
    assert result.usable
    assert result.metadata["tables"]
    assert result.metadata["tables"][0]["columns"] == ["date", "value"]
