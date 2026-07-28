"""Rule-based user preference extraction from completed research runs.

Deterministic extractor that produces pending UserMemory records without LLM.
Enforces MIN_SAMPLE_THRESHOLD: the same signal must appear in ≥2 distinct runs
before a pending memory is created.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.memory.models import UserMemory
from app.memory.policy import MIN_SAMPLE_THRESHOLD
from app.memory.store import create_user_memory, list_user_memories
from app.trace.models import AgentRun


# ── Language detection ─────────────────────────────────────────────────

_CJK_RANGES: list[tuple[int, int]] = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
]


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _cjk_ratio(text: str) -> float:
    """Return the proportion of CJK characters in text."""
    if not text:
        return 0.0
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    return cjk_count / len(text)


def _detect_language_preference(task_text: str) -> str | None:
    """Return 'zh' if CJK ratio > 0.3, 'en' if CJK ratio == 0, else None."""
    ratio = _cjk_ratio(task_text)
    if ratio > 0.3:
        return "zh"
    if ratio == 0.0 and len(task_text) > 20:
        return "en"
    return None


# ── Format detection ───────────────────────────────────────────────────

_FORMAT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("word", re.compile(r"\bWord\b|\.docx?\b", re.IGNORECASE)),
    ("pdf", re.compile(r"\bPDF\b", re.IGNORECASE)),
    ("markdown", re.compile(r"\bMarkdown\b|\.md\b", re.IGNORECASE)),
]


def _detect_format_preference(task_text: str) -> str | None:
    """Return the first matching report format keyword, or None."""
    for fmt, pattern in _FORMAT_PATTERNS:
        if pattern.search(task_text):
            return fmt
    return None


# ── Domain keyword extraction ──────────────────────────────────────────

# Keywords that indicate a research domain interest.
_DOMAIN_KEYWORDS: list[str] = [
    "Agent", "RAG", "LLM", "LangChain", "CrewAI", "AutoGen",
    "embedding", "chunking", "rerank", "vector", "retrieval",
    "prompt", "fine-tuning", "transformer", "GPT", "Claude",
    "DeepSeek", "Qwen", "evaluation", "benchmark", "safety",
    "alignment", "multi-agent", "tool-calling", "function calling",
    "planning", "reasoning", "chain-of-thought", "ReAct",
    "knowledge graph", "graph RAG", "hybrid search",
]

# Match domain keywords anywhere in text (no word-boundary requirement
# since CJK characters break Python's \b).
_DOMAIN_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in _DOMAIN_KEYWORDS),
    re.IGNORECASE,
)


def _detect_domain_keywords(task_text: str) -> list[str]:
    """Return domain keywords that appear in task text, deduplicated and normalized."""
    found = _DOMAIN_PATTERN.findall(task_text)
    # Normalize to title case for consistency
    normalized: list[str] = []
    seen: set[str] = set()
    for kw in found:
        norm = kw.strip()
        if norm.lower() not in seen:
            seen.add(norm.lower())
            normalized.append(norm)
    return normalized


# ── Main extraction entry point ────────────────────────────────────────

def extract_preferences_from_run(
    db: Session,
    run: AgentRun,
    tenant_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Extract preference signals from a single completed run.

    Returns a list of candidate dicts (not persisted). Each candidate has:
        kind, extraction_method, content, confidence, source_run_id
    """
    task_text = run.task or ""
    candidates: list[dict[str, Any]] = []

    # 1. Language preference
    lang = _detect_language_preference(task_text)
    if lang:
        candidates.append({
            "kind": "preference",
            "extraction_method": "rule",
            "content": f"User prefers {'Chinese' if lang == 'zh' else 'English'} research reports",
            "confidence": 0.6,
            "source_run_id": run.run_id,
        })

    # 2. Report format preference
    fmt = _detect_format_preference(task_text)
    if fmt:
        candidates.append({
            "kind": "preference",
            "extraction_method": "rule",
            "content": f"User prefers {fmt.upper()} report format",
            "confidence": 0.5,
            "source_run_id": run.run_id,
        })

    # 3. Domain keywords (interest signals)
    keywords = _detect_domain_keywords(task_text)
    for kw in keywords:
        candidates.append({
            "kind": "interest",
            "extraction_method": "rule",
            "content": f"User researches {kw}",
            "confidence": 0.5,
            "source_run_id": run.run_id,
        })

    return candidates


# ── Persistence with sample threshold ──────────────────────────────────

def _normalize_signal(kind: str, content: str) -> str:
    """Return a stable key for deduplication across runs."""
    return f"{kind}::{content.strip().lower()}"


def _signal_key(memory: UserMemory) -> str:
    return _normalize_signal(memory.kind, memory.content)


def commit_pending_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
    run: AgentRun,
    candidates: list[dict[str, Any]],
) -> int:
    """Persist candidate memories that meet the ≥2 sample threshold.

    For each candidate, we query AgentRun for historical runs whose task
    text contains the same keyword/signal. Only signals appearing in
    ≥MIN_SAMPLE_THRESHOLD distinct completed runs are committed as pending.

    Returns the number of newly created pending memories.
    """
    if not candidates:
        return 0

    # Load existing active + pending memories for dedup
    existing = list_user_memories(db, tenant_id, user_id)
    existing_active = [m for m in existing if m.status == "active"]
    existing_pending = [m for m in existing if m.status == "pending"]

    new_count = 0
    for candidate in candidates:
        key = _normalize_signal(candidate["kind"], candidate["content"])

        # Check if already active → skip
        if any(_signal_key(m) == key and m.status == "active" for m in existing):
            continue

        # Check if already pending → skip duplicate
        if any(_signal_key(m) == key and m.status == "pending" for m in existing):
            continue

        # Count how many completed runs contain this signal's keyword
        # Extract the core keyword from content like "User researches RAG" → "RAG"
        content = candidate["content"]
        keyword = _extract_keyword_from_content(content)
        if not keyword:
            continue

        run_count = db.scalar(
            select(func.count(AgentRun.run_id)).where(
                AgentRun.status == "completed",
                AgentRun.task.contains(keyword),
            )
        ) or 0

        # Only create pending memory if signal seen in ≥MIN_SAMPLE_THRESHOLD runs
        if run_count >= MIN_SAMPLE_THRESHOLD:
            confidence = min(0.5 + 0.2 * (run_count - 1), 0.9)
            create_user_memory(
                db=db,
                tenant_id=tenant_id,
                user_id=user_id,
                kind=candidate["kind"],
                extraction_method=candidate["extraction_method"],
                content=content,
                confidence=confidence,
                source_session_id=run.session_id,
                source_run_id=candidate.get("source_run_id"),
            )
            new_count += 1

    return new_count


def _extract_keyword_from_content(content: str) -> str | None:
    """Extract the core keyword from a content string.

    'User researches RAG' → 'RAG'
    'User prefers Chinese research reports' → 'Chinese'
    'User prefers WORD report format' → 'WORD'
    """
    import re as _re

    # For interest: "User researches X"
    m = _re.match(r"User researches (.+)", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # For language: "User prefers Chinese/English research reports"
    m = _re.match(r"User prefers (Chinese|English) research reports", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # For format: "User prefers WORD report format"
    m = _re.match(r"User prefers ([A-Z]+) report format", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback: return the last word
    parts = content.split()
    if parts:
        return parts[-1]
    return None


def should_extract_for_run(db: Session, tenant_id: str, user_id: str) -> bool:
    """Return True if this user has completed ≥2 runs (triggers extraction)."""
    completed_count = db.scalar(
        select(func.count(AgentRun.run_id)).where(
            AgentRun.status == "completed",
        )
    )
    return (completed_count or 0) >= MIN_SAMPLE_THRESHOLD


def count_completed_runs(db: Session, tenant_id: str, user_id: str) -> int:
    """Return the number of completed runs for display in UI cold-start progress."""
    return (
        db.scalar(
            select(func.count(AgentRun.run_id)).where(
                AgentRun.status == "completed",
            )
        )
        or 0
    )
