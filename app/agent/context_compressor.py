"""Context compressor for tool evidence.

Phase A optimization: compress and deduplicate tool observations
before passing to LLM for report synthesis. Prevents prompt overflow
and improves synthesis quality by removing noise.
"""

from __future__ import annotations

import json
from typing import Any


# Max chars per tool type in the compressed context
_TOOL_CHAR_LIMITS: dict[str, int] = {
    "rag_search":        1500,
    "tavily_search":     1500,
    "mcp_github_search": 1200,
    "file_reader":        800,
    "sql_query":          600,
    "finish":             800,
    "report_writer":        0,   # excluded from synthesis context
}

_DEFAULT_TOOL_LIMIT = 600
_DEFAULT_TOTAL_LIMIT = 6000


def _extract_text_from_output(tool_name: str, output: Any) -> str:
    """Extract the most informative text from a tool's raw output."""
    if not output:
        return ""

    if not isinstance(output, dict):
        return str(output)[:400]

    if tool_name == "rag_search":
        hits = output.get("hits") or []
        parts = []
        for hit in hits[:4]:
            source = hit.get("source", "")
            text   = hit.get("text", "")[:300]
            score  = hit.get("score", 0)
            parts.append(f"[{source} score={score:.3f}] {text}")
        return "\n".join(parts)

    if tool_name == "tavily_search":
        results = output.get("results") or []
        parts = []
        for r in results[:4]:
            title   = r.get("title", "")
            url     = r.get("url", "")
            content = (r.get("clean_content") or r.get("content") or "")[:300]
            parts.append(f"[{title}]({url})\n{content}")
        return "\n".join(parts)

    if tool_name == "mcp_github_search":
        items = output if isinstance(output, list) else []
        parts = []
        for item in items[:4]:
            name    = item.get("name") or item.get("title", "")
            url     = item.get("url", "")
            desc    = item.get("description") or item.get("snippet", "")[:200]
            stars   = item.get("stars")
            star_str = f" ⭐{stars}" if stars else ""
            parts.append(f"[{name}]({url}){star_str}: {desc}")
        return "\n".join(parts)

    if tool_name == "file_reader":
        content = output.get("content", "")
        return content[:700]

    if tool_name == "sql_query":
        rows    = output.get("rows", [])
        columns = output.get("columns", [])
        if columns and rows:
            header = " | ".join(str(c) for c in columns)
            body   = "\n".join(" | ".join(str(v) for v in row.values()) for row in rows[:5])
            return f"{header}\n{body}"
        return json.dumps(rows[:5], ensure_ascii=False)

    if tool_name == "finish":
        return (
            output.get("summary")
            or output.get("observation_summary")
            or str(output)[:600]
        )

    # Generic fallback
    for key in ("content", "text", "result", "output", "summary"):
        val = output.get(key)
        if val:
            return str(val)[:400]
    return str(output)[:300]


def compress_evidence(
    observations: list[dict[str, Any]],
    max_total_chars: int = _DEFAULT_TOTAL_LIMIT,
) -> str:
    """Compress all tool observations into a single context string.

    Steps:
    1. Skip failed tools and excluded tools (report_writer).
    2. Extract most informative text per tool type.
    3. Proportionally truncate to stay within max_total_chars.

    Returns an empty string if no useful evidence exists.
    """
    chunks: list[tuple[str, str]] = []   # (tool_label, text)

    for obs in observations:
        tool   = obs.get("tool_name", "unknown")
        status = obs.get("status") or obs.get("success")
        # Skip failed steps and tools that don't contribute to synthesis
        if status in (False, "failed", "rejected"):
            continue
        char_limit = _TOOL_CHAR_LIMITS.get(tool, _DEFAULT_TOOL_LIMIT)
        if char_limit == 0:
            continue

        raw_output = obs.get("output") or {}
        text = _extract_text_from_output(tool, raw_output)
        if not text.strip():
            # Try the observation_summary as a fallback
            text = obs.get("observation_summary") or obs.get("output_summary") or ""

        text = text[:char_limit].strip()
        if text:
            chunks.append((f"[{tool}]", text))

    if not chunks:
        return ""

    # Proportional truncation if still over limit
    raw_total = sum(len(label) + len(text) + 4 for label, text in chunks)
    if raw_total <= max_total_chars:
        return "\n\n".join(f"{label}\n{text}" for label, text in chunks)

    ratio = max_total_chars / raw_total
    parts = []
    for label, text in chunks:
        keep = max(60, int(len(text) * ratio))
        parts.append(f"{label}\n{text[:keep]}")
    return "\n\n".join(parts)


def has_useful_evidence(observations: list[dict[str, Any]]) -> bool:
    """Return True if at least one tool produced non-empty, non-failed output."""
    for obs in observations:
        tool   = obs.get("tool_name", "")
        status = obs.get("status") or obs.get("success")
        if status in (False, "failed", "rejected"):
            continue
        if tool in ("report_writer",):
            continue
        output = obs.get("output") or {}
        if output or obs.get("observation_summary"):
            return True
    return False
