"""Local RAG search tool handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.rag.vector_store import LocalVectorStore
from app.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_PATH = ROOT / "workspace" / "index" / "rag_index.json"
DEFAULT_TOP_K = 3
MAX_TOP_K = 10


def _failure(
    message: str,
    *,
    error_type: str,
    query: str | None = None,
    top_k: int | None = None,
    index_path: Path = DEFAULT_INDEX_PATH,
) -> ToolResult:
    return ToolResult(
        success=False,
        error_message=message,
        metadata={
            "error_type": error_type,
            "query": query,
            "top_k": top_k,
            "index_path": str(index_path),
        },
    )


def _coerce_top_k(value: Any) -> int:
    try:
        top_k = int(value) if value is not None else DEFAULT_TOP_K
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    return max(1, min(top_k, MAX_TOP_K))


def search_rag(arguments: dict[str, Any]) -> ToolResult:
    """Search the local JSON RAG index and return top-k chunks."""

    query = str(arguments.get("query") or "").strip()
    top_k = _coerce_top_k(arguments.get("top_k"))

    if not query:
        return _failure(
            "Missing required argument: query.",
            error_type="invalid_args",
            query=query,
            top_k=top_k,
        )

    if not DEFAULT_INDEX_PATH.exists():
        return _failure(
            "RAG index not found, run scripts/build_rag_index.py first.",
            error_type="index_missing",
            query=query,
            top_k=top_k,
        )

    try:
        store = LocalVectorStore.load(DEFAULT_INDEX_PATH)
        hits = store.search(query, top_k=top_k)
    except Exception as exc:
        return _failure(
            f"RAG search failed: {exc}",
            error_type="search_error",
            query=query,
            top_k=top_k,
        )

    output = {
        "query": query,
        "top_k": top_k,
        "hits": [
            {
                "source": hit["source"],
                "chunk_id": hit["chunk_id"],
                "score": hit["score"],
                "text": hit["text"],
            }
            for hit in hits
        ],
    }
    if not hits:
        summary = f"rag_search returned no hits for query: {query}"
    else:
        summary = f"rag_search returned {len(hits)} hits for query: {query}"

    return ToolResult(
        success=True,
        output=output,
        output_summary=summary,
        metadata={
            "error_type": None,
            "index_path": str(DEFAULT_INDEX_PATH),
            "top_k": top_k,
            "hit_count": len(hits),
        },
    )
