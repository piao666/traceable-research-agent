"""Built-in web page fetcher with multi-level extraction fallback chain.

Phase 8.2: trafilatura → BeautifulSoup → raw text fallback chain,
Content-Type / PDF routing, response size limits, redirect SSRF re-check,
and extraction metadata.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings, settings
from app.tools.base import ToolResult
from app.tools.fetch_cache import FetchCache, FetchCacheEntry
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

# ── Phase 8.2: PDF magic bytes ───────────────────────────────────────────
PDF_MAGIC = b"%PDF-"

# ── Phase 8.2: Extraction method constants ───────────────────────────────
EXTRACT_TRAFILATURA = "trafilatura"
EXTRACT_BEAUTIFULSOUP = "beautifulsoup"
EXTRACT_RAW_REGEX = "raw_regex"
EXTRACT_NONE = "none"


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


def _is_pdf_url(url: str) -> bool:
    """Check if URL likely points to a PDF based on path suffix."""
    return urlparse(url).path.lower().endswith(".pdf")


def _is_pdf_content_type(content_type: str) -> bool:
    """Check if Content-Type indicates PDF."""
    ct = content_type.lower().strip()
    return "application/pdf" in ct


def _check_pdf_magic(data: bytes) -> bool:
    """Check if data starts with PDF magic bytes."""
    return data.startswith(PDF_MAGIC)


def _extract_title(html: str, url: str) -> str:
    match = TITLE_PATTERN.search(html[:4096])
    if match:
        title = re.sub(r"\s+", " ", match.group(1).strip())
        return title[:200] if title else url
    return url


# ── Phase 8.2: Multi-level extraction chain ──────────────────────────────

def _extract_with_trafilatura(html: str, url: str) -> str | None:
    """Attempt extraction with trafilatura (optional dependency)."""
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
        if result and len(result.strip()) > 50:
            return result.strip()
        return None
    except Exception:
        return None


def _extract_body_bs4(html: str) -> str:
    """Extract main text from HTML using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

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


def _extract_raw_regex(html: str) -> str:
    """Last-resort tag stripping with regex."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body_v2(
    html: str,
    url: str,
    *,
    trafilatura_enabled: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    """Multi-level extraction with metadata.

    Returns: (content, extraction_method, metadata)
    """
    extraction_meta: dict[str, Any] = {
        "extraction_chain": [],
        "extraction_confidence": 0.0,
    }

    # Level 1: trafilatura
    traf_start = time.monotonic()
    traf_result = _extract_with_trafilatura(html, url) if trafilatura_enabled else None
    traf_ms = int((time.monotonic() - traf_start) * 1000)
    if traf_result:
        extraction_meta["extraction_chain"].append({
            "method": EXTRACT_TRAFILATURA,
            "success": True,
            "output_length": len(traf_result),
            "duration_ms": traf_ms,
        })
        extraction_meta["extraction_confidence"] = 0.90
        return traf_result, EXTRACT_TRAFILATURA, extraction_meta
    extraction_meta["extraction_chain"].append({
        "method": EXTRACT_TRAFILATURA,
        "success": False,
        "duration_ms": traf_ms,
        "disabled": not trafilatura_enabled,
    })

    # Level 2: BeautifulSoup
    bs_start = time.monotonic()
    bs_result = _extract_body_bs4(html)
    bs_ms = int((time.monotonic() - bs_start) * 1000)
    if bs_result and len(bs_result) > 50:
        extraction_meta["extraction_chain"].append({
            "method": EXTRACT_BEAUTIFULSOUP,
            "success": True,
            "output_length": len(bs_result),
            "duration_ms": bs_ms,
        })
        extraction_meta["extraction_confidence"] = 0.70
        return bs_result, EXTRACT_BEAUTIFULSOUP, extraction_meta
    extraction_meta["extraction_chain"].append({
        "method": EXTRACT_BEAUTIFULSOUP,
        "success": False,
        "duration_ms": bs_ms,
    })

    # Level 3: raw regex
    raw_result = _extract_raw_regex(html)
    if raw_result and len(raw_result) > 30:
        extraction_meta["extraction_chain"].append({
            "method": EXTRACT_RAW_REGEX,
            "success": True,
            "output_length": len(raw_result),
            "duration_ms": 0,
        })
        extraction_meta["extraction_confidence"] = 0.35
        return raw_result, EXTRACT_RAW_REGEX, extraction_meta
    extraction_meta["extraction_chain"].append({
        "method": EXTRACT_RAW_REGEX,
        "success": False,
        "duration_ms": 0,
    })

    extraction_meta["extraction_confidence"] = 0.0
    return "", EXTRACT_NONE, extraction_meta


def _classify_content_basis(
    raw_len: int,
    cleaned_len: int,
    max_chars: int,
    fetch_error: str | None,
    extraction_method: str = EXTRACT_NONE,
) -> str:
    if fetch_error:
        return "snippet_only"
    if extraction_method == EXTRACT_NONE:
        return "snippet_only"
    if cleaned_len >= max_chars - 50:
        return "partial"
    if extraction_method == EXTRACT_RAW_REGEX:
        return "partial"  # raw regex is never full_text quality
    return "full_text"


def web_fetch(
    arguments: dict[str, Any],
    *,
    settings_obj: Settings | None = None,
    cache: FetchCache | None = None,
    client: httpx.Client | None = None,
) -> ToolResult:
    """Fetch full-text content from a list of URLs via httpx + multi-level extraction.

    Phase 8.2: trafilatura → BeautifulSoup → raw regex fallback chain,
    Content-Type / PDF routing, response size limits, redirect SSRF re-check.

    Input:  urls (list[str]), max_chars (int, default 8000), timeout_seconds (int, default 10)
    Output: pages list with {url, title, content, content_basis, extraction_method, error?}
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

    active = settings_obj or settings
    max_response_bytes = active.web_fetcher_max_response_bytes
    cache_enabled = active.web_fetcher_cache_enabled
    fetch_cache = cache
    cache_init_error: str | None = None
    if cache_enabled and fetch_cache is None:
        try:
            fetch_cache = FetchCache(
                active.web_fetcher_cache_dir,
                active.web_fetcher_cache_ttl_seconds,
            )
        except Exception as exc:
            cache_init_error = type(exc).__name__
            fetch_cache = None
    cache_params = {
        "extractor_version": "web-fetch-v2",
        "trafilatura_enabled": active.web_fetcher_trafilatura_enabled,
        "max_response_bytes": max_response_bytes,
    }

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
                "extraction_method": EXTRACT_NONE,
                "cache_status": "disabled" if not cache_enabled else "not_applicable",
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
                "cache_enabled": cache_enabled,
                "cache_init_error": cache_init_error,
            },
        )

    # Fetch each URL
    owned_client = None
    if client is None:
        owned_client = httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            max_redirects=5,
        )
    client_context = owned_client if owned_client is not None else nullcontext(client)
    with client_context as active_client:
        for original_url, valid_url in validated:
            fetch_error: str | None = None
            title = valid_url
            content = ""
            extraction_method = EXTRACT_NONE
            extraction_meta: dict[str, Any] = {}
            raw_html = ""
            started = time.monotonic()
            content_type = ""
            redirect_chain: list[str] = []
            cache_status = "disabled" if not cache_enabled else "miss"
            cached_entry: FetchCacheEntry | None = None
            cache_age_seconds: float | None = None

            if cache_enabled and fetch_cache is not None:
                cached_entry, cache_status = fetch_cache.lookup(valid_url, cache_params)
                if cached_entry is not None:
                    cache_age_seconds = cached_entry.age_seconds
                if cache_status == "hit" and cached_entry is not None:
                    content = cached_entry.content
                    extraction_method = cached_entry.extraction_method
                    extraction_meta = dict(cached_entry.metadata.get("extraction_meta") or {})
                    title = str(cached_entry.metadata.get("title") or valid_url)
                    content_type = cached_entry.content_type
                    raw_length = int(cached_entry.metadata.get("raw_length") or len(content))
                    content_basis = _classify_content_basis(
                        raw_length,
                        len(content),
                        max_chars,
                        None,
                        extraction_method,
                    )
                    page_entry = {
                        "url": valid_url,
                        "final_url": cached_entry.metadata.get("final_url") or valid_url,
                        "title": title,
                        "content": content[:max_chars],
                        "content_basis": content_basis,
                        "extraction_method": extraction_method,
                        "extraction_confidence": cached_entry.extraction_confidence,
                        "extraction_chain": extraction_meta.get("extraction_chain") or [],
                        "fetched_at_ms": 0,
                        "content_type": content_type,
                        "cache_status": "hit",
                        "cache_hit": True,
                        "cache_age_seconds": round(cache_age_seconds or 0.0, 3),
                        "cache_fetched_at": cached_entry.fetched_at,
                        "content_hash": cached_entry.content_hash,
                    }
                    redirect_chain = list(cached_entry.metadata.get("redirect_chain") or [])
                    if redirect_chain:
                        page_entry["redirect_chain"] = redirect_chain
                    pages.append(page_entry)
                    continue

            try:
                request_headers: dict[str, str] = {}
                if cache_status == "expired" and cached_entry and cached_entry.etag:
                    request_headers["If-None-Match"] = cached_entry.etag
                response = active_client.get(valid_url, headers=request_headers or None)

                if response.status_code == 304 and cached_entry is not None:
                    cached_entry.fetched_at = time.time()
                    cached_entry.ttl_seconds = active.web_fetcher_cache_ttl_seconds
                    if fetch_cache is not None:
                        fetch_cache.put(cached_entry)
                    content = cached_entry.content
                    extraction_method = cached_entry.extraction_method
                    extraction_meta = dict(cached_entry.metadata.get("extraction_meta") or {})
                    title = str(cached_entry.metadata.get("title") or valid_url)
                    content_type = cached_entry.content_type
                    raw_html = " " * int(cached_entry.metadata.get("raw_length") or len(content))
                    cache_status = "revalidated"

                # Record redirect chain for audit
                if response.status_code != 304 and response.history:
                    redirect_chain = [str(r.url) for r in response.history]
                    redirect_chain.append(str(response.url))
                    # ── Phase 8.2: re-check SSRF after redirects ─────
                    final_url = str(response.url)
                    if _validate_url(final_url) is None:
                        fetch_error = "redirect_target_unsafe: final URL failed validation after redirect"
                        content_basis = _classify_content_basis(0, 0, max_chars, fetch_error)
                        pages.append({
                            "url": valid_url,
                            "final_url": final_url,
                            "title": "",
                            "content": "",
                            "content_basis": content_basis,
                            "extraction_method": EXTRACT_NONE,
                            "cache_status": cache_status,
                            "cache_hit": False,
                            "error": fetch_error,
                            "redirect_chain": redirect_chain,
                        })
                        continue

                if response.status_code == 304 and cached_entry is not None:
                    pass
                elif response.status_code == 200:
                    content_type = response.headers.get("content-type", "")

                    # ── Phase 8.2: PDF routing ──────────────────────
                    if _is_pdf_content_type(content_type) or _is_pdf_url(valid_url):
                        # Check magic bytes for confirmation
                        content_bytes = response.content[:max_response_bytes]
                        if _check_pdf_magic(content_bytes):
                            fetch_error = "pdf_routed: PDF detected — use the pdf_reader tool (Phase 8.3) for page-level extraction"
                            pages.append({
                                "url": valid_url,
                                "title": valid_url,
                                "content": "",
                                "content_basis": "snippet_only",
                                "extraction_method": EXTRACT_NONE,
                                "content_type": "application/pdf",
                                "cache_status": cache_status,
                                "cache_hit": False,
                                "error": fetch_error,
                                "redirect_chain": redirect_chain if redirect_chain else None,
                            })
                            continue

                    # ── Phase 8.2: response size limit ──────────────
                    if response.headers.get("content-length"):
                        try:
                            cl = int(response.headers["content-length"])
                            if cl > max_response_bytes:
                                raw_html = response.text[:max_chars * 10]  # read a portion
                                fetch_error = f"response_too_large: {cl} bytes (max {max_response_bytes})"
                        except ValueError:
                            pass

                    if not fetch_error:
                        raw_html = response.text or ""

                    title = _extract_title(raw_html, valid_url)

                    # ── Phase 8.2: multi-level extraction ───────────
                    content, extraction_method, extraction_meta = _extract_body_v2(
                        raw_html,
                        valid_url,
                        trafilatura_enabled=active.web_fetcher_trafilatura_enabled,
                    )
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
                len(raw_html), len(content), max_chars, fetch_error, extraction_method,
            )
            truncated_content = content[:max_chars] if content else ""

            page_entry: dict[str, Any] = {
                "url": valid_url,
                "title": title,
                "content": truncated_content,
                "content_basis": content_basis,
                "extraction_method": extraction_method,
                "fetched_at_ms": elapsed_ms,
                "cache_status": cache_status,
                "cache_hit": cache_status in {"hit", "revalidated"},
            }
            if fetch_error:
                page_entry["error"] = fetch_error
            if extraction_meta.get("extraction_chain"):
                page_entry["extraction_chain"] = extraction_meta["extraction_chain"]
                page_entry["extraction_confidence"] = extraction_meta["extraction_confidence"]
            if redirect_chain:
                page_entry["redirect_chain"] = redirect_chain
            if content_type:
                page_entry["content_type"] = content_type
            if cache_age_seconds is not None:
                page_entry["cache_age_seconds"] = round(cache_age_seconds, 3)

            if (
                cache_enabled
                and fetch_cache is not None
                and not fetch_error
                and content
                and extraction_method != EXTRACT_NONE
                and cache_status != "revalidated"
            ):
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                entry = FetchCacheEntry(
                    cache_key=fetch_cache._compute_key(valid_url, cache_params),
                    url=valid_url,
                    content_hash=content_hash,
                    content=content,
                    content_type=content_type or "text/html",
                    fetched_at=time.time(),
                    ttl_seconds=active.web_fetcher_cache_ttl_seconds,
                    etag=response.headers.get("etag"),
                    extraction_method=extraction_method,
                    extraction_confidence=float(extraction_meta.get("extraction_confidence") or 0.0),
                    metadata={
                        "title": title,
                        "final_url": str(response.url),
                        "raw_length": len(raw_html),
                        "redirect_chain": redirect_chain,
                        "extraction_meta": extraction_meta,
                    },
                )
                if fetch_cache.put(entry):
                    page_entry["cache_stored"] = True
                    page_entry["content_hash"] = content_hash
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
            "fetcher_backend": "httpx_multi_level",
            "read_only": True,
            "result_count": len(pages),
            "cache_enabled": cache_enabled,
            "cache_init_error": cache_init_error,
            "cache_hits": sum(1 for page in pages if page.get("cache_status") == "hit"),
            "cache_revalidated": sum(1 for page in pages if page.get("cache_status") == "revalidated"),
            "cache_misses": sum(1 for page in pages if page.get("cache_status") == "miss"),
            "cache_expired": sum(1 for page in pages if page.get("cache_status") == "expired"),
            "cache_corrupt": sum(1 for page in pages if page.get("cache_status") == "corrupt"),
        },
    )
