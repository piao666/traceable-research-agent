"""Offline R11 quality-gate fixtures for false-success pages."""

from __future__ import annotations

import pytest

from app.retrieval.content_quality import assess_page_quality
from app.retrieval.contracts import FetchBackend, FetchFailureCode


@pytest.mark.parametrize(
    ("title", "content", "html", "code"),
    [
        ("Loading", "Please enable JavaScript to continue", "", FetchFailureCode.JAVASCRIPT_REQUIRED),
        ("Checking your browser", "cf-chl-", "", FetchFailureCode.CLOUDFLARE_CHALLENGE),
        ("Verify", "Please verify you are human with CAPTCHA", "", FetchFailureCode.CAPTCHA),
        ("Cookies", "Accept cookies to continue", "", FetchFailureCode.COOKIE_WALL),
        ("404 - Page not found", "Return home", "", FetchFailureCode.SOFT_NOT_FOUND),
        ("Wait", "Too many requests. Try later.", "", FetchFailureCode.HTTP_429),
        ("Binary", "%PDF-1.7 binary payload", "", FetchFailureCode.PDF_ROUTED),
    ],
)
def test_false_success_pages_are_classified(title, content, html, code) -> None:
    quality, failure = assess_page_quality(
        content,
        title=title,
        raw_html=html,
        extraction_method="beautifulsoup",
        extraction_confidence=0.7,
    )
    assert not quality.usable
    assert failure and failure.code == code


def test_script_heavy_empty_spa_recommends_browser() -> None:
    html = "<html><body><div id='app'>Loading</div>" + ("<script src='app.js'></script>" * 8) + "</body></html>"
    quality, failure = assess_page_quality(
        "Loading",
        title="Application",
        raw_html=html,
        extraction_method="raw_regex",
        extraction_confidence=0.35,
    )
    assert not quality.usable
    assert failure and failure.code == FetchFailureCode.JAVASCRIPT_REQUIRED
    assert failure.recommended_next_strategy == FetchBackend.BROWSER


def test_long_article_mentioning_404_is_not_a_soft_404() -> None:
    content = ("The guide explains how an HTTP 404 page not found response should be handled. " * 40)
    quality, failure = assess_page_quality(
        content,
        title="HTTP error handling guide",
        extraction_method="beautifulsoup",
        extraction_confidence=0.7,
    )
    assert quality.usable
    assert failure is None
