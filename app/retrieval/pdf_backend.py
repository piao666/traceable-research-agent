"""Adapter from the existing page-aware PDF reader to the R11 fetch contract."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from app.retrieval.classifier import classify_legacy_error, failure_status, make_failure
from app.retrieval.content_quality import assess_page_quality
from app.retrieval.contracts import (
    FetchBackend,
    FetchFailureCode,
    FetchRequest,
    FetchResult,
    FetchStatus,
)
from app.retrieval.source_identity import source_lineage
from app.retrieval.url_normalizer import canonicalize_url
from app.tools.base import ToolResult


class PdfBackend:
    """Preserve page locators while exposing PDF text as one fetch result."""

    name = "pdf"

    def __init__(
        self,
        reader: Callable[[dict[str, Any]], ToolResult] | None = None,
        *,
        enabled: bool = True,
        quality_min_score: float = 0.55,
    ) -> None:
        if reader is None:
            from app.tools.pdf_reader import pdf_read

            reader = pdf_read
        self.reader = reader
        self.enabled = enabled
        self.quality_min_score = quality_min_score

    def fetch(self, request: FetchRequest) -> FetchResult:
        started = time.monotonic()
        if not self.enabled:
            failure = make_failure(FetchFailureCode.BACKEND_UNAVAILABLE, "PDF reader is disabled.", tool_scoped=True)
            return self._failed(request, failure, started)
        result = self.reader({"paths": [request.url], "max_chars": request.max_chars})
        documents = (result.output or {}).get("documents") if isinstance(result.output, dict) else []
        document = documents[0] if isinstance(documents, list) and documents else None
        if not isinstance(document, dict) or document.get("error"):
            message = str((document or {}).get("error") or result.error_message or "PDF extraction failed.")
            failure = classify_legacy_error(message)
            if failure.code == FetchFailureCode.UNKNOWN:
                failure = make_failure(FetchFailureCode.PDF_CORRUPT, message)
            return self._failed(request, failure, started)

        pages = [page for page in document.get("pages") or [] if isinstance(page, dict)]
        content = "\n\n".join(
            f"[Page {page.get('page_number')}] {str(page.get('text') or '').strip()}"
            for page in pages
            if str(page.get("text") or "").strip()
        )[: request.max_chars]
        method = str(document.get("extraction_method") or "native")
        basis = str(document.get("content_basis") or "partial")
        confidence = 0.85 if method == "native" else 0.65 if method in {"ocr", "mixed"} else 0.0
        quality, quality_failure = assess_page_quality(
            content,
            title=str(document.get("title") or request.url),
            extraction_method=f"pdf_{method}",
            extraction_confidence=confidence,
            truncated=basis == "partial",
            minimum_score=self.quality_min_score,
        )
        canonical = canonicalize_url(request.url).normalized_url
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
        status = failure_status(quality_failure) if quality_failure else (
            FetchStatus.PARTIAL if basis == "partial" else FetchStatus.SUCCESS
        )
        return FetchResult(
            requested_url=request.url,
            final_url=request.url,
            title=str(document.get("title") or request.url),
            content=content,
            content_type="application/pdf",
            content_basis=basis,
            extraction_method=f"pdf_{method}",
            extraction_confidence=confidence,
            fetch_status=status,
            fetch_backend=FetchBackend.PDF,
            provider="local_pdf_reader",
            quality=quality,
            failure=quality_failure,
            failure_reason=quality_failure.message if quality_failure else None,
            content_hash=content_hash,
            canonical_url=canonical,
            fragment_locator=canonicalize_url(request.url).fragment_locator,
            metadata={
                "page_locators": [
                    {"page_number": page.get("page_number"), "char_count": page.get("char_count", 0)}
                    for page in pages
                ],
                "pdf_integrity": dict(document.get("integrity") or {}),
                "pdf_metadata": dict(document.get("metadata") or {}),
                "source_identity": source_lineage(canonical, content, document.get("metadata")).to_dict(),
                "fetched_at_ms": int((time.monotonic() - started) * 1000),
            },
        )

    @staticmethod
    def _failed(request: FetchRequest, failure, started: float) -> FetchResult:
        return FetchResult(
            requested_url=request.url,
            canonical_url=canonicalize_url(request.url).normalized_url,
            content_type="application/pdf",
            fetch_status=failure_status(failure),
            fetch_backend=FetchBackend.PDF,
            provider="local_pdf_reader",
            failure=failure,
            failure_reason=failure.message,
            metadata={"fetched_at_ms": int((time.monotonic() - started) * 1000)},
        )


__all__ = ["PdfBackend"]
