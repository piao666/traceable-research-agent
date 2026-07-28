"""Memory retriever with keyword and optional vector recall.

Provides:
- retrieve_memories: recall active memories matching a query
- retrieve_for_injection: budget-controlled recall for planner context injection
- memory_search_handler: Tool Registry handler for the memory_search tool
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings as _settings
from app.database import SessionLocal
from app.memory.models import UserMemory
from app.memory.policy import (
    MAX_INJECTION_CHARS,
    format_memory_context,
    select_memories_for_injection,
)
from app.memory.store import list_user_memories
from app.rag.embeddings import cosine_similarity, embed_text, tokenize
from app.tools.base import ToolResult


# ── Keyword recall (default, offline-capable) ──────────────────────────

def _keyword_score(query: str, text: str) -> float:
    """Score a memory against a query using token overlap."""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize(text))
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    # Jaccard-like score biased toward query coverage
    return len(overlap) / len(query_tokens)


def retrieve_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
    query: str,
    top_k: int = 5,
    use_vector: bool = False,
) -> list[UserMemory]:
    """Recall active memories relevant to query.

    Default: keyword token-overlap scoring.
    Vector mode (use_vector=True): also computes dense/sparse embedding
    similarity and merges scores. Requires embedding backend to be available.
    """
    active = list_user_memories(db, tenant_id, user_id, status="active")
    if not active:
        return []

    if not query.strip():
        # No query → return most recent active memories
        active.sort(key=lambda m: m.updated_at, reverse=True)
        return active[:top_k]

    # ── Keyword scoring ────────────────────────────────────────────
    scored: list[tuple[UserMemory, float]] = []
    for mem in active:
        score = _keyword_score(query, mem.content)
        if score > 0:
            scored.append((mem, score))

    # ── Optional vector scoring ────────────────────────────────────
    if use_vector:
        try:
            from app.rag.embedding_backends import create_embedding_backend

            backend = create_embedding_backend(_settings)
            if backend.is_available():
                query_result = backend.embed_query(query)
                if query_result.success and query_result.vectors:
                    query_vec = query_result.vectors[0]
                    # Re-score all active memories with vector similarity
                    contents = [m.content for m in active]
                    doc_result = backend.embed_texts(contents)
                    if doc_result.success and len(doc_result.vectors) == len(active):
                        vector_scored: list[tuple[UserMemory, float]] = []
                        for mem, vec in zip(active, doc_result.vectors):
                            if isinstance(query_vec, dict) and isinstance(vec, dict):
                                sim = cosine_similarity(query_vec, vec)
                            elif isinstance(query_vec, list) and isinstance(vec, list):
                                sim = _dense_cosine(query_vec, vec)
                            else:
                                sim = 0.0
                            # Merge: keyword * 0.3 + vector * 0.7
                            kw_score = _keyword_score(query, mem.content)
                            merged = kw_score * 0.3 + sim * 0.7
                            if merged > 0:
                                vector_scored.append((mem, merged))
                        scored = vector_scored
        except Exception:
            pass  # vector recall failure → fall back to keyword scores

    # Sort by score descending, then by recency
    scored.sort(key=lambda item: (item[1], item[0].updated_at), reverse=True)
    return [mem for mem, _ in scored[:top_k]]


# ── Injection helper ───────────────────────────────────────────────────

def retrieve_for_injection(
    db: Session,
    tenant_id: str,
    user_id: str,
    task: str,
    max_chars: int = MAX_INJECTION_CHARS,
) -> tuple[list[UserMemory], str]:
    """Retrieve and format active memories for planner context injection.

    Returns (selected_memories, formatted_context_string).
    """
    active = list_user_memories(db, tenant_id, user_id, status="active")
    if not active:
        return [], ""

    # Use the task text as the query for relevance scoring
    relevant = retrieve_memories(db, tenant_id, user_id, task, top_k=10)
    selected = select_memories_for_injection(relevant, max_chars=max_chars)
    context = format_memory_context(selected)
    return selected, context


# ── Tool handler ───────────────────────────────────────────────────────

def memory_search_handler(arguments: dict[str, Any]) -> ToolResult:
    """Tool Registry handler for memory_search.

    Recalls active user memories relevant to the query. Falls back to
    default tenant/user when request context is unavailable.
    """
    query = str(arguments.get("query") or "")
    top_k_raw = arguments.get("top_k")
    try:
        top_k = int(top_k_raw) if top_k_raw is not None else 5
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 20))

    tenant_id = str(arguments.get("tenant_id") or _settings.default_tenant_id)
    user_id = str(arguments.get("user_id") or _settings.default_user_id)

    db: Session = SessionLocal()
    try:
        memories = retrieve_memories(db, tenant_id, user_id, query, top_k=top_k)
        result_list: list[dict[str, Any]] = []
        for mem in memories:
            result_list.append({
                "memory_id": mem.memory_id,
                "kind": mem.kind,
                "content": mem.content,
                "confidence": mem.confidence,
                "extraction_method": mem.extraction_method,
                "source_run_id": mem.source_run_id,
                "created_at": mem.created_at.isoformat() if mem.created_at else None,
            })
        return ToolResult(
            success=True,
            output={
                "memories": result_list,
                "recalled": len(result_list),
                "query": query,
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
            output_summary=f"Recalled {len(result_list)} active memories for query '{query[:80]}'",
            metadata={"recalled": len(result_list), "tenant_id": tenant_id, "user_id": user_id},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error_message=f"memory_search failed: {exc}",
            metadata={"error_type": "handler_error", "query": query},
        )
    finally:
        db.close()


# ── Internal helpers ───────────────────────────────────────────────────

def _dense_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for dense vectors."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
