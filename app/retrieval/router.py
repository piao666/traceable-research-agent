"""Adaptive one-URL retrieval router with bounded, auditable fallback."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

from app.retrieval.contracts import FetchBackend, FetchFailureCode, FetchRequest, FetchResult


class Backend(Protocol):
    def fetch(self, request: FetchRequest) -> FetchResult: ...


class RetrievalRouter:
    """Route HTTP → Browser → Remote, with PDF as a content-specific branch."""

    def __init__(
        self,
        *,
        http_backend: Backend,
        browser_backend: Backend | None = None,
        remote_backend: Backend | None = None,
        pdf_backend: Backend | None = None,
    ) -> None:
        self.http_backend = http_backend
        self.browser_backend = browser_backend
        self.remote_backend = remote_backend
        self.pdf_backend = pdf_backend

    def fetch(self, request: FetchRequest) -> FetchResult:
        attempts: list[dict[str, Any]] = []
        if _looks_like_pdf(request.url) and self.pdf_backend is not None:
            result = self._attempt("pdf", self.pdf_backend, request, attempts)
            return self._finalize(result, attempts)

        preferred = list(dict.fromkeys(request.preferred_backends))
        order = preferred or [FetchBackend.HTTP, FetchBackend.BROWSER, FetchBackend.REMOTE_EXTRACT]
        last: FetchResult | None = None
        attempted: set[FetchBackend] = set()
        for backend_name in order:
            if backend_name in attempted:
                continue
            attempted.add(backend_name)
            backend = self._backend(backend_name)
            if backend is None:
                continue
            if backend_name == FetchBackend.BROWSER and not request.allow_browser:
                continue
            if backend_name == FetchBackend.REMOTE_EXTRACT and not request.allow_remote_extract:
                continue
            result = self._attempt(backend_name.value, backend, request, attempts)
            last = result
            if result.usable:
                return self._finalize(result, attempts)
            if (
                result.failure is not None
                and result.failure.code == FetchFailureCode.PDF_ROUTED
                and self.pdf_backend is not None
            ):
                pdf_result = self._attempt("pdf", self.pdf_backend, request, attempts)
                return self._finalize(pdf_result, attempts)
            if not self._should_continue(result, backend_name):
                break
        assert last is not None, "RetrievalRouter requires at least one configured backend"
        return self._finalize(last, attempts)

    def _backend(self, name: FetchBackend) -> Backend | None:
        return {
            FetchBackend.HTTP: self.http_backend,
            FetchBackend.BROWSER: self.browser_backend,
            FetchBackend.PDF: self.pdf_backend,
            FetchBackend.REMOTE_EXTRACT: self.remote_backend,
            FetchBackend.CACHE: self.http_backend,
        }.get(name)

    @staticmethod
    def _attempt(label: str, backend: Backend, request: FetchRequest, attempts: list[dict[str, Any]]) -> FetchResult:
        result = backend.fetch(request)
        attempts.append(
            {
                "backend": label,
                "provider": result.provider,
                "status": result.fetch_status.value,
                "usable": result.usable,
                "failure_code": result.failure.code.value if result.failure else None,
                "duration_ms": int(result.metadata.get("fetched_at_ms") or 0),
            }
        )
        return result

    @staticmethod
    def _should_continue(result: FetchResult, backend_name: FetchBackend) -> bool:
        if result.failure is None:
            return False
        next_backend = result.failure.recommended_next_strategy
        if next_backend is not None:
            return True
        if result.failure.code in {
            FetchFailureCode.BACKEND_UNAVAILABLE,
            FetchFailureCode.REMOTE_EXTRACT_UNAVAILABLE,
        }:
            return True
        return backend_name == FetchBackend.BROWSER and result.failure.tool_scoped

    @staticmethod
    def _finalize(result: FetchResult, attempts: list[dict[str, Any]]) -> FetchResult:
        result.metadata["retrieval_attempts"] = list(attempts)
        result.metadata["retrieval_attempt_count"] = len(attempts)
        return result


def _looks_like_pdf(url: str) -> bool:
    try:
        return urlsplit(url).path.casefold().endswith(".pdf")
    except ValueError:
        return False


__all__ = ["RetrievalRouter"]
