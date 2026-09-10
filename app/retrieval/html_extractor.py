"""Static and rendered HTML extraction shared by retrieval backends."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.tools.web_content_cleaner import clean_web_snippet


EXTRACT_TRAFILATURA = "trafilatura"
EXTRACT_BEAUTIFULSOUP = "beautifulsoup"
EXTRACT_RAW_REGEX = "raw_regex"
EXTRACT_NONE = "none"

TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
BODY_SELECTORS = ("article", "main", "div[role=main]", "body")


@dataclass(frozen=True)
class HtmlExtraction:
    title: str
    content: str
    extraction_method: str
    extraction_confidence: float
    extraction_chain: tuple[dict[str, Any], ...] = ()
    canonical_hint: str | None = None
    published_at: str | None = None
    tables: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def extract_html(
    html: str,
    url: str,
    *,
    trafilatura_enabled: bool = True,
) -> HtmlExtraction:
    """Run the deterministic extraction chain and retain audit metadata."""

    title, canonical_hint, published_at = extract_document_metadata(html, url)
    chain: list[dict[str, Any]] = []

    started = time.monotonic()
    trafilatura_content = _extract_with_trafilatura(html, url) if trafilatura_enabled else None
    duration_ms = int((time.monotonic() - started) * 1000)
    if trafilatura_content:
        chain.append(
            {
                "method": EXTRACT_TRAFILATURA,
                "success": True,
                "output_length": len(trafilatura_content),
                "duration_ms": duration_ms,
            }
        )
        return _result(
            title,
            trafilatura_content,
            EXTRACT_TRAFILATURA,
            0.90,
            chain,
            canonical_hint,
            published_at,
            html,
        )
    chain.append(
        {
            "method": EXTRACT_TRAFILATURA,
            "success": False,
            "duration_ms": duration_ms,
            "disabled": not trafilatura_enabled,
        }
    )

    started = time.monotonic()
    soup_content = _extract_body_bs4(html)
    duration_ms = int((time.monotonic() - started) * 1000)
    if soup_content and len(soup_content) > 50:
        chain.append(
            {
                "method": EXTRACT_BEAUTIFULSOUP,
                "success": True,
                "output_length": len(soup_content),
                "duration_ms": duration_ms,
            }
        )
        return _result(
            title,
            soup_content,
            EXTRACT_BEAUTIFULSOUP,
            0.70,
            chain,
            canonical_hint,
            published_at,
            html,
        )
    chain.append(
        {"method": EXTRACT_BEAUTIFULSOUP, "success": False, "duration_ms": duration_ms}
    )

    raw_content = _extract_raw_regex(html)
    if raw_content and len(raw_content) > 30:
        chain.append(
            {
                "method": EXTRACT_RAW_REGEX,
                "success": True,
                "output_length": len(raw_content),
                "duration_ms": 0,
            }
        )
        return _result(
            title,
            raw_content,
            EXTRACT_RAW_REGEX,
            0.35,
            chain,
            canonical_hint,
            published_at,
            html,
        )
    chain.append({"method": EXTRACT_RAW_REGEX, "success": False, "duration_ms": 0})
    return _result(
        title,
        "",
        EXTRACT_NONE,
        0.0,
        chain,
        canonical_hint,
        published_at,
        html,
    )


def extract_document_metadata(html: str, url: str) -> tuple[str, str | None, str | None]:
    title = _extract_title(html, url)
    canonical_hint: str | None = None
    published_at: str | None = None
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        canonical = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
        if canonical is not None:
            canonical_hint = str(canonical.get("href") or "").strip() or None
        for attrs in (
            {"property": "article:published_time"},
            {"name": "date"},
            {"name": "datePublished"},
            {"itemprop": "datePublished"},
        ):
            node = soup.find("meta", attrs=attrs)
            if node is not None and str(node.get("content") or "").strip():
                published_at = str(node.get("content")).strip()[:128]
                break
    except Exception:
        pass
    return title, canonical_hint, published_at


def _result(
    title: str,
    content: str,
    method: str,
    confidence: float,
    chain: list[dict[str, Any]],
    canonical_hint: str | None,
    published_at: str | None,
    html: str,
) -> HtmlExtraction:
    try:
        from app.tools.structured_tables import html_tables

        tables = tuple(html_tables(html))
    except Exception:
        tables = ()
    return HtmlExtraction(
        title=title,
        content=content,
        extraction_method=method,
        extraction_confidence=confidence,
        extraction_chain=tuple(chain),
        canonical_hint=canonical_hint,
        published_at=published_at,
        tables=tables,
    )


def _extract_title(html: str, url: str) -> str:
    match = TITLE_PATTERN.search(html[:8192])
    if match:
        title = re.sub(r"\s+", " ", match.group(1).strip())
        if title:
            return title[:200]
    return url


def _extract_with_trafilatura(html: str, url: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        result = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            include_images=False,
            include_links=False,
            output_format="txt",
        )
    except Exception:
        return None
    return result.strip() if result and len(result.strip()) > 50 else None


def _extract_body_bs4(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in ("script", "style", "nav", "footer", "header", "iframe", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for selector in BODY_SELECTORS:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(separator=" ", strip=True)
            if len(text) > 100:
                return clean_web_snippet(text, max_chars=99999)
    text = soup.get_text(separator=" ", strip=True)
    return clean_web_snippet(text, max_chars=99999) if text else ""


def _extract_raw_regex(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()
