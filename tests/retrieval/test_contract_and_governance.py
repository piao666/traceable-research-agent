"""R11 deterministic retrieval contract and governance tests."""

from __future__ import annotations

import socket
import unittest

import httpx

from app.retrieval.url_normalizer import canonicalize_url, resolve_canonical_hint
from app.retrieval.classifier import (
    classify_exception,
    classify_http_status,
    classify_legacy_error,
)
from app.retrieval.source_identity import canonical_story_hash, source_lineage
from app.retrieval.contracts import FetchBackend, FetchFailureCode, PageFetchResult
from app.retrieval.content_quality import assess_page_quality
from app.tools.errors import ToolErrorCategory, classify_tool_error


class RetrievalContractTests(unittest.TestCase):
    def test_page_contract_preserves_structured_failure(self) -> None:
        failure = classify_http_status(403)
        page = PageFetchResult(
            requested_url="https://example.com/a",
            transport_url="https://example.com/a?sig=abc&id=123",
            final_url="https://example.com/a",
            failure=failure,
        ).to_page_dict()
        self.assertEqual(page["error"], "HTTP 403")
        self.assertEqual(page["error_code"], "http_403")
        self.assertEqual(page["error_detail"]["recommended_next_strategy"], "browser")
        self.assertFalse(page["error_detail"]["retryable"])
        self.assertEqual(page["fetch_backend"], "http")
        self.assertEqual(page["transport_url"], "https://example.com/a?sig=abc&id=123")


class FetchFailureClassifierTests(unittest.TestCase):
    def test_agent_recovery_understands_r11_page_failures(self) -> None:
        self.assertEqual(classify_tool_error("javascript_required"), ToolErrorCategory.INVALID_RESULT)
        self.assertEqual(classify_tool_error("remote_extract_unavailable"), ToolErrorCategory.UNAVAILABLE)
        self.assertEqual(classify_tool_error("soft_not_found"), ToolErrorCategory.NOT_FOUND)

    def test_http_failures_expose_recovery_policy(self) -> None:
        forbidden = classify_http_status(403)
        limited = classify_http_status(429, {"Retry-After": "12"})
        missing = classify_http_status(404)
        self.assertEqual(forbidden.code, FetchFailureCode.HTTP_403)
        self.assertEqual(forbidden.recommended_next_strategy, FetchBackend.BROWSER)
        self.assertEqual(limited.retry_after_seconds, 12)
        self.assertTrue(limited.retryable)
        self.assertIsNone(missing.recommended_next_strategy)

    def test_transport_failures_are_distinct(self) -> None:
        timeout = classify_exception(httpx.ReadTimeout("late"))
        dns = classify_exception(socket.gaierror("missing"))
        self.assertEqual(timeout.code, FetchFailureCode.TIMEOUT)
        self.assertEqual(dns.code, FetchFailureCode.DNS_ERROR)

    def test_legacy_error_is_normalized(self) -> None:
        self.assertEqual(
            classify_legacy_error("pdf_routed: use pdf_reader").code,
            FetchFailureCode.PDF_ROUTED,
        )
        self.assertEqual(
            classify_legacy_error("response_too_large: >10").code,
            FetchFailureCode.CONTENT_TOO_LARGE,
        )


class PageQualityGateTests(unittest.TestCase):
    def test_http_200_javascript_shell_is_not_usable(self) -> None:
        quality, failure = assess_page_quality(
            "Please enable JavaScript to continue.",
            title="Loading",
            raw_html="<div id='root'>Please enable JavaScript</div>",
            extraction_method="beautifulsoup",
            extraction_confidence=0.7,
        )
        self.assertFalse(quality.usable)
        self.assertEqual(failure.code, FetchFailureCode.JAVASCRIPT_REQUIRED)
        self.assertEqual(failure.recommended_next_strategy, FetchBackend.BROWSER)

    def test_cloudflare_and_captcha_are_distinct(self) -> None:
        _, cloudflare = assess_page_quality(
            "Checking your browser before accessing this page. Cloudflare Ray ID 123",
            extraction_method="beautifulsoup",
        )
        _, captcha = assess_page_quality(
            "Please verify you are human using CAPTCHA before continuing.",
            extraction_method="beautifulsoup",
        )
        self.assertEqual(cloudflare.code, FetchFailureCode.CLOUDFLARE_CHALLENGE)
        self.assertEqual(captcha.code, FetchFailureCode.CAPTCHA)

    def test_substantive_document_is_usable(self) -> None:
        content = "This technical specification defines a stable retrieval contract. " * 12
        quality, failure = assess_page_quality(
            content,
            title="Retrieval specification",
            extraction_method="trafilatura",
            extraction_confidence=0.9,
        )
        self.assertTrue(quality.usable)
        self.assertIsNone(failure)
        self.assertEqual(quality.content_basis, "full_text")


class UrlCanonicalizationTests(unittest.TestCase):
    def test_tracking_fragment_default_port_and_case_are_normalized(self) -> None:
        result = canonicalize_url(
            "HTTPS://Example.COM:443/a/../docs/?utm_source=x&b=2&a=1#section-2"
        )
        self.assertEqual(result.normalized_url, "https://example.com/docs?a=1&b=2")
        self.assertEqual(result.fragment_locator, "section-2")

    def test_canonical_hint_is_a_signal(self) -> None:
        self.assertEqual(
            resolve_canonical_hint("https://example.com/a/page", "../canonical?utm_medium=x"),
            "https://example.com/canonical",
        )


class SourceIndependenceTests(unittest.TestCase):
    def test_syndicated_reuters_copies_share_independence_group(self) -> None:
        content = "Reporting by Reuters. The organization announced the same verified result."
        first = source_lineage("https://news-a.example/story", content)
        second = source_lineage("https://news-b.example/reprint", content)
        self.assertEqual(first.original_publisher, "reuters")
        self.assertEqual(first.independence_group, second.independence_group)

    def test_story_hash_ignores_spacing_and_punctuation(self) -> None:
        self.assertEqual(
            canonical_story_hash("A result: 42. Stable!"),
            canonical_story_hash("A   result 42 stable"),
        )


if __name__ == "__main__":
    unittest.main()
