"""Lightweight cleanup for web-search result snippets."""

from __future__ import annotations

import re
from typing import Any


_NOISE_PHRASES = {
    "overview",
    "login",
    "sign in",
    "share",
    "contact us",
    "privacy",
    "terms",
    "cookies",
    "all rights reserved",
    "概览",
    "登录",
    "注册",
    "分享",
    "联系我们",
    "隐私",
    "条款",
    "首页",
    "产品",
    "领取课程",
    "按技术方向选课",
    "按学习模式选课",
    "ai 培训班",
    "nvidia 认证",
    "course path icon",
}


def page_content_issue(text: str) -> str | None:
    """Recognize obvious unrendered shells, not arbitrary short documents.

    Keep substantive tables even when surrounding navigation has placeholders.
    This cannot certify semantic relevance or complete JS rendering.
    """
    placeholders = re.findall(r"\{\{.*?\}\}|@[a-zA-Z_]+@", text)
    without = re.sub(r"\{\{.*?\}\}|@[a-zA-Z_]+@", "", text)
    dated_numbers = re.findall(r"\d{4}[-/]\d{2}[-/]\d{2}\s+[-+]?\d", without)
    loading = any(marker in without for marker in ("正在加载", "暂无相关数据", "Loading...", "loading…"))
    if len(placeholders) >= 2 and not dated_numbers and (loading or len(without.strip()) < 40):
        return "unrendered_page"
    labels = ("日线", "周线", "月线", "查看更多", "快速链接", "最新公告", "指标名称")
    if len(text) < 240 and sum(label in text for label in labels) >= 5 and not re.search(r"\d", text):
        return "navigation_only"
    return None


def clean_web_snippet(text: Any, *, max_chars: int = 900) -> str:
    """Remove common navigation/page-shell noise from a search snippet."""

    raw = str(text or "").replace("\r", "\n")
    if not raw.strip():
        return ""

    lines = []
    seen: set[str] = set()
    for part in re.split(r"[\n\u2022]+", raw):
        line = re.sub(r"\s+", " ", part).strip(" -*\t")
        if not line:
            continue
        normalized = line.lower().strip(" .:：|")
        if normalized in seen:
            continue
        seen.add(normalized)
        if _is_noise_line(normalized):
            continue
        lines.append(line)

    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = re.sub(r"\s+", " ", raw).strip()
    return cleaned[:max_chars]


def clean_tavily_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a Tavily result with an added clean_content field."""

    item = dict(result)
    raw_content = item.get("content") or item.get("raw_content") or ""
    clean_content = clean_web_snippet(raw_content)
    item["clean_content"] = clean_content
    item["content_was_cleaned"] = clean_content != str(raw_content or "").strip()
    item["content_quality"] = _content_quality(clean_content)
    return item


def _is_noise_line(normalized: str) -> bool:
    if normalized in _NOISE_PHRASES:
        return True
    if any(phrase in normalized for phrase in _NOISE_PHRASES) and len(normalized) < 30:
        return True
    if re.fullmatch(r"[\w\s,-]{1,20}", normalized) and len(normalized.split()) <= 3:
        return normalized in _NOISE_PHRASES
    if len(normalized) <= 2:
        return True
    return False


def _content_quality(text: str) -> str:
    if not text:
        return "empty"
    if len(text) < 80:
        return "low"
    return "high"
