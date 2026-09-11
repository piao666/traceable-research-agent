"""Backend-neutral contracts for page retrieval."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FetchBackend(str, Enum):
    HTTP = "http"
    BROWSER = "browser"
    PDF = "pdf"
    REMOTE_EXTRACT = "remote_extract"
    CACHE = "cache"


class FetchStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    JAVASCRIPT_REQUIRED = "javascript_required"
    PAYWALLED = "paywalled"
    CAPTCHA = "captcha"
    UNSUPPORTED_CONTENT = "unsupported_content"
    CONTENT_TOO_LARGE = "content_too_large"
    EXTRACT_FAILED = "extract_failed"
    UNSAFE_URL = "unsafe_url"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"


class FetchFailureCode(str, Enum):
    INVALID_URL = "invalid_url"
    SSRF_BLOCKED = "ssrf_blocked"
    DNS_ERROR = "dns_error"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    HTTP_401 = "http_401"
    HTTP_403 = "http_403"
    HTTP_404 = "http_404"
    HTTP_410 = "http_410"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    SOFT_NOT_FOUND = "soft_not_found"
    JAVASCRIPT_REQUIRED = "javascript_required"
    BOT_CHALLENGE = "bot_challenge"
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    CAPTCHA = "captcha"
    LOGIN_REQUIRED = "login_required"
    COOKIE_WALL = "cookie_wall"
    PAYWALL = "paywall"
    EMPTY_DOCUMENT = "empty_document"
    BOILERPLATE_ONLY = "boilerplate_only"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    CONTENT_TOO_LARGE = "content_too_large"
    PDF_ROUTED = "pdf_routed"
    PDF_CORRUPT = "pdf_corrupt"
    EXTRACTION_FAILED = "extraction_failed"
    ROBOTS_RESTRICTED = "robots_restricted"
    REDIRECT_ERROR = "redirect_error"
    BATCH_DEADLINE = "batch_deadline_exceeded"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    REMOTE_EXTRACT_UNAVAILABLE = "remote_extract_unavailable"
    UNKNOWN = "unknown"


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    max_chars: int = Field(default=50000, ge=500, le=50000)
    preferred_backends: list[FetchBackend] = Field(default_factory=list)
    allow_browser: bool = True
    allow_remote_extract: bool = True
    research_run_id: str | None = None
    trace_id: str | None = None
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class FetchFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: FetchFailureCode
    message: str
    retryable: bool
    page_scoped: bool = True
    tool_scoped: bool = False
    recommended_next_strategy: FetchBackend | None = None
    http_status: int | None = None
    retry_after_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "page_scoped": self.page_scoped,
            "tool_scoped": self.tool_scoped,
            "recommended_next_strategy": (
                self.recommended_next_strategy.value
                if self.recommended_next_strategy is not None
                else None
            ),
            "http_status": self.http_status,
            "retry_after_seconds": self.retry_after_seconds,
        }


class ContentQualityAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    usable: bool
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    content_length: int
    boilerplate_ratio: float | None
    language: str | None
    title_quality: float
    extraction_confidence: float
    content_basis: str
    issues: tuple[str, ...] = ()
    recommended_backend: FetchBackend | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "usable": self.usable,
            "quality_score": self.quality_score,
            "content_length": self.content_length,
            "boilerplate_ratio": self.boilerplate_ratio,
            "language": self.language,
            "title_quality": self.title_quality,
            "extraction_confidence": self.extraction_confidence,
            "content_basis": self.content_basis,
            "issues": list(self.issues),
            "recommended_backend": (
                self.recommended_backend.value
                if self.recommended_backend is not None
                else None
            ),
        }


PageQualityAssessment = ContentQualityAssessment


class FetchResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    requested_url: str
    transport_url: str | None = None
    final_url: str | None = None
    title: str = ""
    content: str = ""
    published_at: str | None = None
    content_type: str = ""
    content_basis: str = "snippet_only"
    extraction_method: str = "none"
    extraction_confidence: float = 0.0
    fetch_status: FetchStatus = FetchStatus.EXTRACT_FAILED
    fetch_backend: FetchBackend = FetchBackend.HTTP
    provider: str = "local_http"
    quality: ContentQualityAssessment | None = None
    failure: FetchFailure | None = None
    failure_reason: str | None = None
    content_hash: str | None = None
    source_content_hash: str | None = None
    canonical_url: str | None = None
    canonical_hint: str | None = None
    fragment_locator: str | None = None
    redirect_chain: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return (
            self.fetch_status in {FetchStatus.SUCCESS, FetchStatus.PARTIAL}
            and self.failure is None
            and bool(self.content.strip())
            and (self.quality is None or self.quality.usable)
        )

    def to_page_dict(self) -> dict[str, Any]:
        page: dict[str, Any] = {
            "url": self.requested_url,
            "requested_url": self.requested_url,
            "transport_url": self.transport_url,
            "final_url": self.final_url,
            "canonical_url": self.canonical_url or self.final_url or self.requested_url,
            "title": self.title,
            "content": self.content,
            "published_at": self.published_at,
            "content_type": self.content_type,
            "content_basis": self.content_basis,
            "extraction_method": self.extraction_method,
            "extraction_confidence": self.extraction_confidence,
            "fetch_status": self.fetch_status.value,
            "fetch_backend": self.fetch_backend.value,
            "provider": self.provider,
            **self.metadata,
        }
        if self.content_hash:
            page["content_hash"] = self.content_hash
        if self.source_content_hash:
            page["source_content_hash"] = self.source_content_hash
        if self.canonical_hint:
            page["canonical_hint"] = self.canonical_hint
        if self.fragment_locator:
            page["fragment_locator"] = self.fragment_locator
        if self.redirect_chain:
            page["redirect_chain"] = list(self.redirect_chain)
        if self.quality is not None:
            page["quality"] = self.quality.to_dict()
        if self.failure is not None:
            page["error"] = self.failure.message
            page["error_code"] = self.failure.code.value
            page["error_detail"] = self.failure.to_dict()
        elif self.failure_reason:
            page["error"] = self.failure_reason
        return {key: value for key, value in page.items() if value is not None}


PageFetchResult = FetchResult
