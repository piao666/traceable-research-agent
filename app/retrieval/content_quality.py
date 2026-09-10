"""Research-usability quality gate for extracted web pages."""

from __future__ import annotations

import re
from collections import Counter

from app.retrieval.classifier import make_failure
from app.retrieval.contracts import FetchFailure, FetchFailureCode, PageQualityAssessment
from app.tools.web_content_cleaner import page_content_issue


_ISSUE_PATTERNS: tuple[tuple[FetchFailureCode, tuple[str, ...]], ...] = (
    (FetchFailureCode.CLOUDFLARE_CHALLENGE, ("cf-chl-", "cloudflare ray id", "checking your browser")),
    (FetchFailureCode.BOT_CHALLENGE, ("bot verification", "automated access", "unusual traffic", "are you a robot")),
    (FetchFailureCode.CAPTCHA, ("captcha", "verify you are human", "人机验证", "安全验证")),
    (FetchFailureCode.JAVASCRIPT_REQUIRED, ("please enable javascript", "javascript is required", "requires javascript", "启用 javascript")),
    (FetchFailureCode.LOGIN_REQUIRED, ("login required", "sign in to continue", "请登录后继续", "登录后查看")),
    (FetchFailureCode.PAYWALL, ("subscribe to continue reading", "subscription required", "订阅后阅读全文", "付费后阅读")),
    (FetchFailureCode.COOKIE_WALL, ("accept cookies to continue", "cookie consent", "同意 cookie 后继续")),
)


def assess_page_quality(
    content: str,
    *,
    title: str = "",
    raw_html: str = "",
    extraction_method: str = "none",
    extraction_confidence: float = 0.0,
    truncated: bool = False,
    minimum_score: float = 0.0,
    structured_data: bool = False,
) -> tuple[PageQualityAssessment, FetchFailure | None]:
    normalized = " ".join(str(content or "").split())
    visible_and_raw = f"{title}\n{normalized}\n{raw_html[:12000]}".casefold()
    issues: list[str] = []
    failure: FetchFailure | None = None

    for code, markers in _ISSUE_PATTERNS:
        if any(marker in visible_and_raw for marker in markers):
            issues.append(code.value)
            failure = make_failure(code)
            break

    if failure is None and (
        normalized.startswith("%PDF-")
        or raw_html.lstrip().startswith("%PDF-")
    ):
        issues.append(FetchFailureCode.PDF_ROUTED.value)
        failure = make_failure(FetchFailureCode.PDF_ROUTED, "Raw PDF binary is not web-page text.")

    title_lower = str(title or "").casefold()
    if failure is None and (
        any(marker in title_lower for marker in ("404", "page not found", "页面不存在"))
        or (
            len(normalized) < 1500
            and any(
                marker in visible_and_raw
                for marker in ("404 not found", "the page you requested does not exist", "页面不存在")
            )
        )
    ):
        issues.append(FetchFailureCode.SOFT_NOT_FOUND.value)
        failure = make_failure(FetchFailureCode.SOFT_NOT_FOUND, "HTTP 200 response is a soft 404 page.")

    if failure is None and len(normalized) < 1500 and any(
        marker in visible_and_raw
        for marker in ("too many requests", "rate limit exceeded", "请求过于频繁")
    ):
        issues.append(FetchFailureCode.HTTP_429.value)
        failure = make_failure(FetchFailureCode.HTTP_429, "HTTP 200 response contains a rate-limit page.")

    legacy_issue = page_content_issue(normalized)
    if legacy_issue and failure is None:
        code = (
            FetchFailureCode.JAVASCRIPT_REQUIRED
            if legacy_issue == "unrendered_page"
            else FetchFailureCode.BOILERPLATE_ONLY
        )
        issues.append(code.value)
        failure = make_failure(code, legacy_issue)

    if failure is None and raw_html:
        script_count = len(re.findall(r"<script\b", raw_html, flags=re.IGNORECASE))
        visible_ratio = len(normalized) / max(len(raw_html), 1)
        if script_count >= 5 and len(normalized) < 500 and visible_ratio < 0.08:
            issues.append(FetchFailureCode.JAVASCRIPT_REQUIRED.value)
            failure = make_failure(
                FetchFailureCode.JAVASCRIPT_REQUIRED,
                "HTML is a script-heavy application shell with insufficient rendered text.",
            )

    if (
        failure is None
        and len(normalized) > 3000
        and len(re.findall(r"[.!?。！？]", normalized)) < 3
    ):
        issues.append(FetchFailureCode.BOILERPLATE_ONLY.value)
        failure = make_failure(
            FetchFailureCode.BOILERPLATE_ONLY,
            "Large extracted token list has no sentence structure.",
        )

    boilerplate_ratio = _boilerplate_ratio(normalized)
    if not normalized:
        issues.append(FetchFailureCode.EMPTY_DOCUMENT.value)
        failure = failure or make_failure(FetchFailureCode.EMPTY_DOCUMENT)
    elif len(normalized) < 80 and failure is None and not structured_data:
        issues.append(FetchFailureCode.EMPTY_DOCUMENT.value)
        failure = make_failure(FetchFailureCode.EMPTY_DOCUMENT, "Extracted document is too short")
    elif (
        boilerplate_ratio >= 0.72
        and len(normalized) < 1500
        and _navigation_marker_count(normalized) >= 3
        and failure is None
    ):
        issues.append(FetchFailureCode.BOILERPLATE_ONLY.value)
        failure = make_failure(FetchFailureCode.BOILERPLATE_ONLY)

    title_quality = _title_quality(title)
    language = _language(normalized)
    content_basis = "snippet_only"
    if failure is None:
        content_basis = "partial" if truncated or extraction_method in {"raw_regex", "none"} else "full_text"
    usable = failure is None
    quality_score = _quality_score(
        usable=usable,
        content_length=len(normalized),
        extraction_confidence=extraction_confidence,
        boilerplate_ratio=boilerplate_ratio,
        title_quality=title_quality,
    )
    if failure is None and quality_score < max(0.0, min(minimum_score, 1.0)):
        issues.append(FetchFailureCode.EXTRACTION_FAILED.value)
        failure = make_failure(
            FetchFailureCode.EXTRACTION_FAILED,
            f"Content quality {quality_score:.3f} is below the configured threshold.",
        )
        usable = False
        quality_score = 0.0
        content_basis = "snippet_only"
    assessment = PageQualityAssessment(
        usable=usable,
        quality_score=quality_score,
        content_length=len(normalized),
        boilerplate_ratio=round(boilerplate_ratio, 4),
        language=language,
        title_quality=title_quality,
        extraction_confidence=max(0.0, min(float(extraction_confidence or 0.0), 1.0)),
        content_basis=content_basis,
        issues=tuple(dict.fromkeys(issues)),
        recommended_backend=(failure.recommended_next_strategy if failure else None),
    )
    return assessment, failure


def _title_quality(title: str) -> float:
    normalized = " ".join(str(title or "").split())
    if not normalized:
        return 0.0
    if normalized.casefold() in {"home", "untitled", "loading", "首页", "登录"}:
        return 0.2
    return 1.0 if 4 <= len(normalized) <= 200 else 0.6


def _language(text: str) -> str | None:
    if not text:
        return None
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk > latin * 0.25:
        return "zh"
    return "en" if latin else None


def _boilerplate_ratio(text: str) -> float:
    tokens = re.findall(r"[\w\u3400-\u9fff]+", text.casefold())
    if not tokens:
        return 1.0
    counts = Counter(tokens)
    repeated = sum(count for count in counts.values() if count >= 8)
    navigation = _navigation_marker_count(text)
    return min(1.0, repeated / len(tokens) + min(navigation / 20, 0.35))


def _navigation_marker_count(text: str) -> int:
    return sum(
        text.casefold().count(marker)
        for marker in (
            "privacy",
            "terms",
            "cookie",
            "login",
            "sign in",
            "home",
            "contact",
            "首页",
            "导航",
            "隐私",
            "登录",
            "联系我们",
        )
    )


def _quality_score(
    *,
    usable: bool,
    content_length: int,
    extraction_confidence: float,
    boilerplate_ratio: float,
    title_quality: float,
) -> float:
    if not usable:
        return 0.0
    # A focused 300-500 character source passage can already be useful;
    # length is only one quality signal and must not dominate the gate.
    length_score = min(content_length / 500, 1.0)
    score = (
        0.10
        + 0.35 * max(0.0, min(float(extraction_confidence or 0.0), 1.0))
        + 0.30 * length_score
        + 0.20 * max(0.0, 1.0 - boilerplate_ratio)
        + 0.15 * title_quality
    )
    return round(max(0.0, min(score, 1.0)), 4)
