"""Built-in web page fetcher with httpx + BeautifulSoup offline fallback."""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.tools.base import ToolResult
from app.tools.web_content_cleaner import clean_web_snippet


PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)

USER_AGENT = "traceable-research-agent-read-only/1.0"

TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

BODY_SELECTORS = (
    ("article",),
    ("main",),
    ("div[role=main]",),
    ("body",),
)


def _is_private_host(host: str) -> bool:
    """Return True if host is a private / loopback address."""
    if not host:
        return True
    host = host.split("%")[0].strip("[]")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in PRIVATE_NETWORKS)


def _validate_url(raw: str) -> str | None:
    """Return a normalized URL or None if the URL is unsafe."""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or _is_private_host(host):
        return None
    return parsed.geturl()


def _extract_title(html: str, url: str) -> str:
    match = TITLE_PATTERN.search(html[:4096])
    if match:
        title = re.sub(r"\s+", " ", match.group(1).strip())
        return title[:200] if title else url
    return url


def _extract_body(html: str) -> str:
    """Extract main text from HTML using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Fallback: strip tags with regex
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag_name in ("script", "style", "nav", "footer", "header", "iframe", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Try semantic selectors first
    for selectors in BODY_SELECTORS:
        tag = soup.select_one(", ".join(selectors))
        if tag:
            text = tag.get_text(separator=" ", strip=True)
            if len(text) > 100:
                return clean_web_snippet(text, max_chars=99999)

    text = soup.get_text(separator=" ", strip=True)
    return clean_web_snippet(text, max_chars=99999) if text else ""


def _classify_content_basis(raw_len: int, cleaned_len: int, max_chars: int, fetch_error: str | None) -> str:
    if fetch_error:
        return "snippet_only"
    if cleaned_len >= max_chars - 50:
        return "partial"
    return "full_text"


def web_fetch(arguments: dict[str, Any]) -> ToolResult:
    """Fetch full-text content from a list of URLs via httpx + BeautifulSoup.

    Input:  urls (list[str]), max_chars (int, default 8000), timeout_seconds (int, default 10)
    Output: pages list with {url, title, content, content_basis, error?}
    """
    urls_raw = arguments.get("urls", [])
    if isinstance(urls_raw, str):
        urls_raw = [urls_raw]
    if not isinstance(urls_raw, list):
        return ToolResult(
            success=False,
            error_message="web_fetcher requires a 'urls' list argument.",
            metadata={"error_type": "invalid_args", "tool_name": "web_fetcher"},
        )

    max_chars = int(arguments.get("max_chars", 8000))
    max_chars = max(500, min(max_chars, 50000))
    timeout_seconds = int(arguments.get("timeout_seconds", 10))
    timeout_seconds = max(3, min(timeout_seconds, 60))

    pages: list[dict[str, Any]] = []
    validated: list[tuple[str, str]] = []

    for raw_url in urls_raw:
        if not isinstance(raw_url, str):
            continue
        url = _validate_url(raw_url.strip())
        if url:
            validated.append((raw_url.strip(), url))
        else:
            pages.append({
                "url": raw_url.strip()[:200] if isinstance(raw_url, str) else str(raw_url)[:200],
                "title": "",
                "content": "",
                "content_basis": "snippet_only",
                "error": "URL failed validation (non-http scheme or private IP).",
            })

    if not validated:
        return ToolResult(
            success=True,
            output={
                "pages": pages,
                "fetched_count": 0,
                "failed_count": len(pages),
                "total_count": len(pages),
            },
            output_summary=f"web_fetcher processed 0 URLs (all {len(pages)} rejected: validation failed).",
            metadata={
                "tool_name": "web_fetcher",
                "fetcher_backend": "httpx_beautifulsoup",
                "read_only": True,
            },
        )

    # Fetch each URL
    with httpx.Client(
        timeout=timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        for original_url, valid_url in validated:
            fetch_error: str | None = None
            title = valid_url
            content = ""
            raw_html = ""
            started = time.monotonic()

            try:
                response = client.get(valid_url)
                if response.status_code == 200:
                    raw_html = response.text or ""
                    title = _extract_title(raw_html, valid_url)
                    content = _extract_body(raw_html)
                else:
                    fetch_error = f"HTTP {response.status_code}"
            except httpx.TimeoutException:
                fetch_error = "timeout"
            except httpx.ConnectError:
                fetch_error = "connection_error"
            except httpx.HTTPError as exc:
                fetch_error = f"http_error: {type(exc).__name__}"
            except Exception as exc:
                fetch_error = f"fetch_error: {type(exc).__name__}"

            elapsed_ms = int((time.monotonic() - started) * 1000)
            content_basis = _classify_content_basis(
                len(raw_html), len(content), max_chars, fetch_error
            )
            truncated_content = content[:max_chars] if content else ""

            page_entry: dict[str, Any] = {
                "url": valid_url,
                "title": title,
                "content": truncated_content,
                "content_basis": content_basis,
                "fetched_at_ms": elapsed_ms,
            }
            if fetch_error:
                page_entry["error"] = fetch_error
            pages.append(page_entry)

    fetched_count = sum(1 for p in pages if not p.get("error"))
    failed_count = len(pages) - fetched_count

    return ToolResult(
        success=True,
        output={
            "pages": pages,
            "fetched_count": fetched_count,
            "failed_count": failed_count,
            "total_count": len(pages),
        },
        output_summary=(
            f"web_fetcher: {fetched_count}/{len(pages)} URLs fetched "
            f"(full_text={sum(1 for p in pages if p.get('content_basis') == 'full_text')}, "
            f"partial={sum(1 for p in pages if p.get('content_basis') == 'partial')}, "
            f"snippet_only={sum(1 for p in pages if p.get('content_basis') == 'snippet_only')})"
        ),
        metadata={
            "tool_name": "web_fetcher",
            "fetcher_backend": "httpx_beautifulsoup",
            "read_only": True,
            "result_count": len(pages),
        },
    )
