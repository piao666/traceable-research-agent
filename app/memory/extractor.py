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
    "Agent", "LLM", "LangChain", "CrewAI", "AutoGen",
    "prompt", "fine-tuning", "transformer", "GPT", "Claude",
    "DeepSeek", "Qwen", "evaluation", "benchmark", "safety",
    "alignment", "multi-agent", "tool-calling", "function calling",
    "planning", "reasoning", "chain-of-thought", "ReAct",
    "knowledge graph",
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
            "content": f"偏好使用{'中文' if lang == 'zh' else '英文'}研究报告",
            "confidence": 0.6,
            "source_run_id": run.run_id,
        })

    # 2. Report format preference
    fmt = _detect_format_preference(task_text)
    if fmt:
        candidates.append({
            "kind": "preference",
            "extraction_method": "rule",
            "content": f"偏好 {fmt.upper()} 报告格式",
            "confidence": 0.5,
            "source_run_id": run.run_id,
        })

    # 3. Domain keywords (interest signals)
    keywords = _detect_domain_keywords(task_text)
    for kw in keywords:
        candidates.append({
            "kind": "interest",
            "extraction_method": "rule",
            "content": f"经常调研：{kw}",
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
    existing = list_user_memories(db)
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
        # Extract the core keyword from content like "User researches LLM".
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

    'User researches LLM' → 'LLM'
    'User prefers Chinese research reports' → 'Chinese'
    'User prefers WORD report format' → 'WORD'
    """
    import re as _re

    # For interest: "User researches X"
    m = _re.match(r"User researches (.+)", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = _re.match(r"经常调研[：:]\s*(.+)", content)
    if m:
        return m.group(1).strip()

    # For language: "User prefers Chinese/English research reports"
    m = _re.match(r"User prefers (Chinese|English) research reports", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = _re.match(r"偏好使用(中文|英文)研究报告", content)
    if m:
        return m.group(1).strip()

    # For format: "User prefers WORD report format"
    m = _re.match(r"User prefers ([A-Z]+) report format", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = _re.match(r"偏好\s+([A-Z]+)\s+报告格式", content, _re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback: return the last word
    parts = content.split()
    if parts:
        return parts[-1]
    return None


def should_extract_for_run(db: Session) -> bool:
    """Return True if the deployment has completed at least two runs."""
    completed_count = db.scalar(
        select(func.count(AgentRun.run_id)).where(
            AgentRun.status == "completed",
        )
    )
    return (completed_count or 0) >= MIN_SAMPLE_THRESHOLD


def count_completed_runs(db: Session) -> int:
    """Return the number of completed runs for cold-start progress."""
    return (
        db.scalar(
            select(func.count(AgentRun.run_id)).where(
                AgentRun.status == "completed",
            )
        )
        or 0
    )


# ── Phase 5: LLM-based memory distillation ──────────────────────────

_LLM_EXTRACTION_SYSTEM = """You are a user profile analyst. Given a research task description and
the tool observations collected, extract the user's preferences and interests.

Output ONLY valid JSON:
{
  "preferences": [
    {"kind": "preference|interest|fact", "content": "one-sentence description", "confidence": 0.7}
  ]
}

Rules:
- kind: "preference" for format/language/workflow choices, "interest" for research topics, "fact" for known facts
- content: concise one-sentence description in the user's preferred language
- confidence: 0.0-1.0 based on how clearly the signal appears
- Only extract signals that are clearly evidenced — do not guess
- Return empty preferences array if no clear signals found
- Max 5 preferences total"""


def extract_preferences_with_llm(
    run: AgentRun,
    observations: list[dict[str, Any]],
    llm_client: Any,
) -> list[dict[str, Any]]:
    """Use LLM to distill user preferences from a completed run.

    Args:
        run: The completed AgentRun.
        observations: The tool observations from the run.
        llm_client: An available LLMClient instance.

    Returns:
        List of candidate preference dicts (kind, extraction_method="llm",
        content, confidence, source_run_id).
    """
    if llm_client is None or not llm_client.is_available():
        return []

    # Build compact observation summary
    obs_parts: list[str] = []
    for obs in observations[-15:]:
        tool = obs.get("tool_name") or obs.get("action") or "unknown"
        summary = obs.get("output_summary") or obs.get("observation_summary") or ""
        if summary:
            obs_parts.append(f"[{tool}] {str(summary)[:300]}")
    obs_text = "\n".join(obs_parts) if obs_parts else "(no observations)"

    try:
        from app.llm.base import LLMMessage

        messages = [
            LLMMessage(role="system", content=_LLM_EXTRACTION_SYSTEM),
            LLMMessage(
                role="user",
                content=(
                    f"Research task: {run.task}\n\n"
                    f"Tool observations:\n{obs_text}\n\n"
                    "Extract user preferences from this research session."
                ),
            ),
        ]
        response = llm_client.complete(messages, temperature=0.0, max_tokens=600)
        if not response.success or not response.content:
            return []

        # Parse JSON from response
        import json as _json
        import re as _re

        text = response.content.strip()
        match = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not match:
            return []
        parsed = _json.loads(match.group(0))
        if not isinstance(parsed, dict):
            return []

        prefs = parsed.get("preferences") or []
        if not isinstance(prefs, list):
            return []

        candidates: list[dict[str, Any]] = []
        for pref in prefs[:5]:
            if not isinstance(pref, dict):
                continue
            kind = str(pref.get("kind") or "preference")
            content = str(pref.get("content") or "").strip()
            if not content:
                continue
            try:
                confidence = float(pref.get("confidence") or 0.5)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.1, min(confidence, 1.0))

            candidates.append({
                "kind": kind if kind in ("preference", "interest", "fact") else "preference",
                "extraction_method": "llm",
                "content": content,
                "confidence": confidence,
                "source_run_id": run.run_id,
            })

        return candidates
    except Exception:
        return []
