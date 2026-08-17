"""Read-only PDF reader tool with page-level provenance.

Phase 8.3: Extracts text from PDF files (public URL or local path) using PyMuPDF.
Includes integrity pre-check (download, page count, traversal completeness)
and page-level locators for citation. OCR is an optional independent profile.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.agent.file_access_policy import (
    DOCS_ROOT,
    allowed_roots,
    display_path,
    find_allowed_root,
    resolve_file_reader_path,
)
from app.tools.base import ToolResult
from app.tools.ssrf import validate_url as _validate_url


PDF_MAGIC = b"%PDF"
USER_AGENT = "traceable-research-agent-read-only/1.0"

# Extraction method constants
EXTRACT_NATIVE = "native"
EXTRACT_OCR = "ocr"
EXTRACT_NONE = "none"

# Minimum text density to consider a page "readable" without OCR
MIN_TEXT_DENSITY = 0.05

MAX_REDIRECTS = 5


def _check_pdf_magic(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == PDF_MAGIC


def _resolve_local_path(raw_path: str) -> tuple[Path | None, ToolResult | None]:
    """Resolve a local file path within allowed roots. Returns (path, failure)."""
    resolved = resolve_file_reader_path(raw_path)
    allowed_root = find_allowed_root(resolved)
    if allowed_root is not None:
        return resolved, None
    return None, ToolResult(
        success=False,
        error_message=(
            "PDF path is outside configured file_reader allowed roots. "
            "Use a path under workspace/docs or request HITL approval."
        ),
        metadata={
            "error_type": "safety_rejected",
            "path": raw_path,
            "resolved_path_summary": str(resolved),
            "docs_root": str(DOCS_ROOT),
            "allowed_roots": [str(r) for r in allowed_roots()],
            "tool_name": "pdf_reader",
        },
    )


def _download_pdf(
    url: str,
    timeout_seconds: int,
    max_response_bytes: int,
) -> tuple[bytes | None, ToolResult | None]:
    """Download a PDF from a public URL. Returns (bytes, failure)."""
    validated = _validate_url(url)
    if validated is None:
        return None, ToolResult(
            success=False,
            error_message="PDF URL failed validation (non-http scheme or private IP).",
            metadata={"error_type": "url_validation_failed", "url": url, "tool_name": "pdf_reader"},
        )

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        ) as client:
            # Follow redirects manually, validating every hop before fetching.
            chain: list[str] = []
            current = validated
            response = None
            for _ in range(MAX_REDIRECTS + 1):
                hop = client.get(current)
                chain.append(str(hop.url))
                if hop.is_redirect:
                    location = hop.headers.get("location")
                    if not location:
                        return None, ToolResult(
                            success=False,
                            error_message="redirect_error: missing location header",
                            metadata={"error_type": "redirect_unsafe", "url": url, "tool_name": "pdf_reader"},
                        )
                    next_url = urljoin(current, location)
                    if _validate_url(next_url) is None:
                        return None, ToolResult(
                            success=False,
                            error_message="redirect_target_unsafe: redirect target failed validation",
                            metadata={"error_type": "redirect_unsafe", "url": url, "final_url": next_url, "tool_name": "pdf_reader"},
                        )
                    current = next_url
                    continue
                response = hop
                break
            else:
                return None, ToolResult(
                    success=False,
                    error_message="redirect_error: too many redirects",
                    metadata={"error_type": "redirect_unsafe", "url": url, "tool_name": "pdf_reader"},
                )

            if response.status_code != 200:
                return None, ToolResult(
                    success=False,
                    error_message=f"HTTP {response.status_code}",
                    metadata={"error_type": "http_error", "url": url, "status_code": response.status_code, "tool_name": "pdf_reader"},
                )

            content_type = response.headers.get("content-type", "").lower()

            # Bounded streaming read, enforced regardless of content-length.
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes(chunk_size=65536):
                chunks.append(chunk)
                received += len(chunk)
                if received > max_response_bytes:
                    return None, ToolResult(
                        success=False,
                        error_message=f"response_too_large: >{max_response_bytes} bytes (max {max_response_bytes})",
                        metadata={"error_type": "too_large", "url": url, "tool_name": "pdf_reader"},
                    )
            content = b"".join(chunks)

            # Verify PDF magic
            if not _check_pdf_magic(content):
                return None, ToolResult(
                    success=False,
                    error_message="Content is not a valid PDF (missing PDF magic bytes).",
                    metadata={"error_type": "not_pdf", "url": url, "content_type": content_type, "tool_name": "pdf_reader"},
                )

            return content, None

    except httpx.TimeoutException:
        return None, ToolResult(
            success=False,
            error_message=f"timeout after {timeout_seconds}s",
            metadata={"error_type": "timeout", "url": url, "tool_name": "pdf_reader"},
        )
    except httpx.ConnectError:
        return None, ToolResult(
            success=False,
            error_message="connection_error",
            metadata={"error_type": "connection_error", "url": url, "tool_name": "pdf_reader"},
        )
    except httpx.HTTPError as exc:
        return None, ToolResult(
            success=False,
            error_message=f"http_error: {type(exc).__name__}",
            metadata={"error_type": "http_error", "url": url, "tool_name": "pdf_reader"},
        )
    except Exception as exc:
        return None, ToolResult(
            success=False,
            error_message=f"download_error: {type(exc).__name__}",
            metadata={"error_type": "download_error", "url": url, "tool_name": "pdf_reader"},
        )


def _extract_pdf(
    pdf_bytes: bytes,
    max_pages: int,
    max_chars: int,
    ocr_enabled: bool,
) -> dict[str, Any]:
    """Extract text from PDF bytes using PyMuPDF.

    Returns a dict with pages, integrity info, and metadata.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "pages": [],
            "total_pages": 0,
            "extracted_pages": 0,
            "integrity": {"damaged": False, "truncated": False, "declared_pages": 0, "actual_pages": 0, "traversed_pages": 0},
            "metadata": {},
            "extraction_method": EXTRACT_NONE,
            "content_basis": "snippet_only",
            "error": "PyMuPDF (fitz) is not installed. Install with: pip install PyMuPDF",
        }

    pages: list[dict[str, Any]] = []
    integrity: dict[str, Any] = {
        "damaged": False,
        "truncated": False,
        "declared_pages": 0,
        "actual_pages": 0,
        "traversed_pages": 0,
    }
    doc_metadata: dict[str, Any] = {}
    extraction_methods: set[str] = set()
    total_chars = 0

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return {
            "pages": [],
            "total_pages": 0,
            "extracted_pages": 0,
            "integrity": {"damaged": True, "truncated": False, "declared_pages": 0, "actual_pages": 0, "traversed_pages": 0},
            "metadata": {},
            "extraction_method": EXTRACT_NONE,
            "content_basis": "snippet_only",
            "error": "Failed to open PDF (possibly damaged or encrypted).",
        }

    try:
        # Metadata
        raw_meta = doc.metadata or {}
        doc_metadata = {
            "title": raw_meta.get("title", ""),
            "author": raw_meta.get("author", ""),
            "subject": raw_meta.get("subject", ""),
            "format": raw_meta.get("format", ""),
            "creator": raw_meta.get("creator", ""),
            "producer": raw_meta.get("producer", ""),
        }
        declared = raw_meta.get("pages")

        # Integrity: declared vs actual page count
        if declared is not None:
            try:
                integrity["declared_pages"] = int(declared)
            except (TypeError, ValueError):
                integrity["declared_pages"] = 0
        integrity["actual_pages"] = doc.page_count

        actual_pages = doc.page_count
        pages_to_read = min(actual_pages, max_pages)
        integrity["truncated"] = actual_pages > max_pages

        for page_idx in range(pages_to_read):
            try:
                page = doc[page_idx]
                text = page.get_text("text") or ""
                text = re.sub(r"\s+", " ", text).strip()

                char_count = len(text)
                # Rough text density: chars per unit area (approximate)
                page_rect = page.rect
                page_area = abs(page_rect.width * page_rect.height)
                text_density = char_count / max(page_area, 1.0) if page_area > 0 else 0.0

                method = EXTRACT_NATIVE

                # Low text density → try OCR if enabled
                if text_density < MIN_TEXT_DENSITY and ocr_enabled:
                    try:
                        ocr_text = page.get_text("ocr") or ""
                        ocr_text = re.sub(r"\s+", " ", ocr_text).strip()
                        if len(ocr_text) > len(text):
                            text = ocr_text
                            char_count = len(text)
                            method = EXTRACT_OCR
                            text_density = char_count / max(page_area, 1.0) if page_area > 0 else 0.0
                    except Exception:
                        pass

                # Apply max_chars cap
                truncated = False
                if total_chars + char_count > max_chars:
                    remaining = max(0, max_chars - total_chars)
                    text = text[:remaining] if remaining > 0 else ""
                    char_count = len(text)
                    truncated = True
                    integrity["truncated"] = True

                pages.append({
                    "page_number": page_idx + 1,
                    "text": text,
                    "char_count": char_count,
                    "extraction_method": method,
                    "text_density": round(text_density, 6),
                    "truncated": truncated,
                })
                extraction_methods.add(method)
                total_chars += char_count

                if truncated:
                    break

            except Exception:
                pages.append({
                    "page_number": page_idx + 1,
                    "text": "",
                    "char_count": 0,
                    "extraction_method": EXTRACT_NONE,
                    "text_density": 0.0,
                    "truncated": False,
                    "error": "page_extraction_error",
                })
                extraction_methods.add(EXTRACT_NONE)

        integrity["traversed_pages"] = len(pages)

        # Determine overall extraction method
        if not extraction_methods or extraction_methods == {EXTRACT_NONE}:
            overall_method = EXTRACT_NONE
        elif EXTRACT_OCR in extraction_methods and EXTRACT_NATIVE in extraction_methods:
            overall_method = "mixed"
        elif EXTRACT_OCR in extraction_methods:
            overall_method = EXTRACT_OCR
        else:
            overall_method = EXTRACT_NATIVE

        # Determine content_basis
        if overall_method == EXTRACT_NONE:
            content_basis = "snippet_only"
        elif integrity["truncated"] or integrity["traversed_pages"] < integrity["actual_pages"]:
            content_basis = "partial"
        else:
            content_basis = "full_text"

        return {
            "pages": pages,
            "total_pages": actual_pages,
            "extracted_pages": len([p for p in pages if p.get("char_count", 0) > 0]),
            "integrity": integrity,
            "metadata": doc_metadata,
            "extraction_method": overall_method,
            "content_basis": content_basis,
            "error": None,
        }

    finally:
        doc.close()


def pdf_read(arguments: dict[str, Any]) -> ToolResult:
    """Read and extract text from PDF files.

    Input:
        paths (list[str]): URLs or local file paths to PDF documents
        max_chars (int, default 16000): Max characters per document
        max_pages (int): Max pages per document (default from config, max 200)

    Output:
        documents list with per-document: pages, integrity, metadata, content_basis
    """
    try:
        from app.config import settings as _pdf_settings
        default_max_pages = _pdf_settings.pdf_reader_max_pages
        default_max_response_bytes = _pdf_settings.pdf_reader_max_response_bytes
        default_timeout = _pdf_settings.pdf_reader_timeout_seconds
        ocr_enabled = _pdf_settings.pdf_reader_ocr_enabled
    except Exception:
        default_max_pages = 50
        default_max_response_bytes = 52_428_800
        default_timeout = 30
        ocr_enabled = False

    paths_raw = arguments.get("paths", [])
    if isinstance(paths_raw, str):
        paths_raw = [paths_raw]
    if not isinstance(paths_raw, list) or not paths_raw:
        return ToolResult(
            success=False,
            error_message="pdf_reader requires a 'paths' list argument (URLs or local file paths).",
            metadata={"error_type": "invalid_args", "tool_name": "pdf_reader"},
        )

    max_chars = int(arguments.get("max_chars", 16000))
    max_chars = max(500, min(max_chars, 100000))
    max_pages = int(arguments.get("max_pages", default_max_pages))
    max_pages = max(1, min(max_pages, 200))

    documents: list[dict[str, Any]] = []
    total_pages = 0
    extracted_docs = 0
    failed_docs = 0

    for raw_path in paths_raw:
        if not isinstance(raw_path, str) or not raw_path.strip():
            documents.append({
                "path": str(raw_path)[:200] if raw_path else "",
                "total_pages": 0,
                "extracted_pages": 0,
                "integrity": {"damaged": False, "truncated": False, "declared_pages": 0, "actual_pages": 0, "traversed_pages": 0},
                "metadata": {},
                "pages": [],
                "content_basis": "snippet_only",
                "extraction_method": EXTRACT_NONE,
                "error": "Empty or invalid path.",
            })
            failed_docs += 1
            continue

        path_str = raw_path.strip()
        started = time.monotonic()

        # Determine if URL or local path
        is_url = path_str.startswith(("http://", "https://"))
        pdf_bytes: bytes | None = None
        error_result: ToolResult | None = None
        doc_title = path_str

        if is_url:
            pdf_bytes, error_result = _download_pdf(
                path_str,
                timeout_seconds=default_timeout,
                max_response_bytes=default_max_response_bytes,
            )
        else:
            resolved, err = _resolve_local_path(path_str)
            if err is not None:
                error_result = err
            elif resolved is None:
                error_result = ToolResult(
                    success=False,
                    error_message="Failed to resolve local PDF path.",
                    metadata={"error_type": "path_resolution_failed", "path": path_str, "tool_name": "pdf_reader"},
                )
            elif not resolved.exists() or not resolved.is_file():
                error_result = ToolResult(
                    success=False,
                    error_message="File not found.",
                    metadata={"error_type": "not_found", "path": path_str, "resolved_path": str(resolved), "tool_name": "pdf_reader"},
                )
            else:
                try:
                    with open(resolved, "rb") as f:
                        pdf_bytes = f.read(default_max_response_bytes)
                    if not _check_pdf_magic(pdf_bytes):
                        error_result = ToolResult(
                            success=False,
                            error_message="File is not a valid PDF (missing PDF magic bytes).",
                            metadata={"error_type": "not_pdf", "path": path_str, "tool_name": "pdf_reader"},
                        )
                        pdf_bytes = None
                    else:
                        doc_title = display_path(resolved)
                except PermissionError:
                    error_result = ToolResult(
                        success=False,
                        error_message="Permission denied reading PDF file.",
                        metadata={"error_type": "permission_denied", "path": path_str, "tool_name": "pdf_reader"},
                    )
                except OSError as exc:
                    error_result = ToolResult(
                        success=False,
                        error_message=f"Failed to read PDF file: {exc}",
                        metadata={"error_type": "read_error", "path": path_str, "tool_name": "pdf_reader"},
                    )

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if error_result is not None:
            documents.append({
                "path": path_str,
                "total_pages": 0,
                "extracted_pages": 0,
                "integrity": {"damaged": False, "truncated": False, "declared_pages": 0, "actual_pages": 0, "traversed_pages": 0},
                "metadata": {},
                "pages": [],
                "content_basis": "snippet_only",
                "extraction_method": EXTRACT_NONE,
                "error": error_result.error_message,
                "elapsed_ms": elapsed_ms,
            })
            failed_docs += 1
            continue

        if pdf_bytes is None:
            documents.append({
                "path": path_str,
                "total_pages": 0,
                "extracted_pages": 0,
                "integrity": {"damaged": False, "truncated": False, "declared_pages": 0, "actual_pages": 0, "traversed_pages": 0},
                "metadata": {},
                "pages": [],
                "content_basis": "snippet_only",
                "extraction_method": EXTRACT_NONE,
                "error": "No PDF content obtained.",
                "elapsed_ms": elapsed_ms,
            })
            failed_docs += 1
            continue

        # Extract
        result = _extract_pdf(pdf_bytes, max_pages, max_chars, ocr_enabled)
        result["path"] = path_str
        result["title"] = result.get("metadata", {}).get("title") or doc_title
        result["elapsed_ms"] = elapsed_ms

        if result.get("error"):
            failed_docs += 1
        else:
            extracted_docs += 1

        total_pages += result.get("total_pages", 0)
        documents.append(result)

    return ToolResult(
        success=True,
        output={
            "documents": documents,
            "total_pages": total_pages,
            "extracted_documents": extracted_docs,
            "failed_documents": failed_docs,
            "total_documents": len(documents),
        },
        output_summary=(
            f"pdf_reader: {extracted_docs}/{len(documents)} documents extracted "
            f"({total_pages} pages total, "
            f"full_text={sum(1 for d in documents if d.get('content_basis') == 'full_text')}, "
            f"partial={sum(1 for d in documents if d.get('content_basis') == 'partial')}, "
            f"snippet_only={sum(1 for d in documents if d.get('content_basis') == 'snippet_only')})"
        ),
        metadata={
            "tool_name": "pdf_reader",
            "read_only": True,
            "result_count": len(documents),
        },
    )
