"""Memory injection policy, cold-start behaviour, and budget control."""

from __future__ import annotations

from app.memory.models import UserMemory

# ── Constants ─────────────────────────────────────────────────────────

COLD_START_REASON = "cold_start"
MIN_SAMPLE_THRESHOLD = 2  # same preference signal must appear ≥2 times
MAX_INJECTION_CHARS = 800  # hard cap for memory context injected into planner


# ── Injection budget ──────────────────────────────────────────────────

def should_inject_memory(active_memories: list[UserMemory]) -> bool:
    """Return True when there is at least one active memory to inject."""

    return len(active_memories) > 0


def select_memories_for_injection(
    active_memories: list[UserMemory],
    max_chars: int = MAX_INJECTION_CHARS,
) -> list[UserMemory]:
    """Select memories to inject, sorted by recency then confidence.

    Drops items that would exceed max_chars total content length.
    """

    sorted_memories = sorted(
        active_memories,
        key=lambda m: (m.updated_at, m.confidence),
        reverse=True,
    )
    selected: list[UserMemory] = []
    total_chars = 0
    for memory in sorted_memories:
        content_len = len(memory.content)
        if total_chars + content_len > max_chars:
            # Skip this memory — would exceed budget.
            # Already-selected items are retained.
            continue
        selected.append(memory)
        total_chars += content_len
    return selected


def format_memory_context(memories: list[UserMemory]) -> str:
    """Format a list of memories into a string for planner injection.

    Returns an empty string when no memories are available (cold-start).
    """

    if not memories:
        return ""

    lines: list[str] = ["## User Context (from previous research)"]
    for memory in memories:
        kind_label = _kind_label(memory.kind)
        method_label = _method_label(memory.extraction_method)
        lines.append(
            f"- [{kind_label}] {memory.content} "
            f"(confidence: {memory.confidence:.1f}, source: {method_label})"
        )
    return "\n".join(lines)


def build_cold_start_trace_event() -> dict:
    """Return a trace event payload for cold-start memory recall."""

    return {
        "event_type": "memory_recall",
        "recalled": 0,
        "reason": COLD_START_REASON,
        "injected_chars": 0,
    }


def build_memory_recall_trace_event(
    recalled: int,
    injected_chars: int,
    memory_ids: list[str],
) -> dict:
    """Return a trace event payload for successful memory recall."""

    return {
        "event_type": "memory_recall",
        "recalled": recalled,
        "injected_chars": injected_chars,
        "memory_ids": memory_ids,
        "reason": None,
    }


def build_memory_injection_trimmed_trace_event(
    total: int,
    selected: int,
    injected_chars: int,
    max_chars: int,
) -> dict:
    """Return a trace event when injection budget caused trimming."""

    return {
        "event_type": "memory_recall",
        "recalled": selected,
        "injected_chars": injected_chars,
        "total_available": total,
        "budget_max_chars": max_chars,
        "reason": "budget_trimmed",
    }


# ── Internal helpers ──────────────────────────────────────────────────

def _kind_label(kind: str) -> str:
    labels = {
        "profile": "Profile",
        "preference": "Preference",
        "fact": "Fact",
        "interest": "Interest",
    }
    return labels.get(kind, kind.title())


def _method_label(method: str) -> str:
    labels = {
        "rule": "rule",
        "llm": "LLM",
        "manual": "manual",
    }
    return labels.get(method, method)
