"""Keyword-based recall for single-instance local memory."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.memory.models import UserMemory
from app.memory.policy import (
    MAX_INJECTION_CHARS,
    format_memory_context,
    select_memories_for_injection,
)
from app.memory.store import list_user_memories
from app.tools.base import ToolResult


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def _keyword_score(query: str, text: str) -> float:
    """Score memory text by query-token coverage."""

    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text))
    return len(query_tokens & text_tokens) / len(query_tokens)


def retrieve_memories(
    db: Session,
    query: str,
    top_k: int = 5,
) -> list[UserMemory]:
    """Recall active local memories relevant to a query."""

    active = list_user_memories(db, status="active")
    if not active:
        return []
    if not query.strip():
        return sorted(active, key=lambda item: item.updated_at, reverse=True)[:top_k]

    scored = [
        (memory, _keyword_score(query, memory.content))
        for memory in active
    ]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: (item[1], item[0].updated_at), reverse=True)
    return [memory for memory, _ in scored[:top_k]]


def retrieve_for_injection(
    db: Session,
    task: str,
    max_chars: int = MAX_INJECTION_CHARS,
) -> tuple[list[UserMemory], str]:
    """Retrieve and format memories for planner context injection."""

    if not list_user_memories(db, status="active"):
        return [], ""
    relevant = retrieve_memories(db, task, top_k=10)
    selected = select_memories_for_injection(relevant, max_chars=max_chars)
    return selected, format_memory_context(selected)


def memory_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Tool Registry handler for local memory search."""

    query = str(arguments.get("query") or "")
    try:
        top_k = int(arguments.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 20))

    db: Session = SessionLocal()
    try:
        memories = retrieve_memories(db, query, top_k=top_k)
        result_list = [
            {
                "memory_id": memory.memory_id,
                "kind": memory.kind,
                "content": memory.content,
                "confidence": memory.confidence,
                "extraction_method": memory.extraction_method,
                "source_run_id": memory.source_run_id,
                "created_at": memory.created_at.isoformat() if memory.created_at else None,
            }
            for memory in memories
        ]
        return ToolResult(
            success=True,
            output={"memories": result_list, "recalled": len(result_list), "query": query},
            output_summary=f"Recalled {len(result_list)} active memories for query '{query[:80]}'",
            metadata={"recalled": len(result_list)},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"memory_search failed: {exc}",
            metadata={"error_type": "handler_error", "query": query},
        )
    finally:
        db.close()
