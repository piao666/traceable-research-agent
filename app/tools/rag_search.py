"""Local RAG search tool handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.embedding_backends import (
    DeterministicEmbeddingBackend,
    create_embedding_backend,
)
from app.rag.vector_backends import JsonVectorBackend, create_vector_backend
from app.tools.base import ToolResult
from app.tools.rag_retrieval import search_rag as search_rag_with_mode


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
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    details = {
        "error_type": error_type,
        "query": query,
        "top_k": top_k,
        "index_path": str(index_path),
    }
    details.update(metadata or {})
    return ToolResult(
        success=False,
        error_message=message,
        metadata=details,
    )


def _coerce_top_k(value: Any) -> int:
    try:
        top_k = int(value) if value is not None else DEFAULT_TOP_K
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    return max(1, min(top_k, MAX_TOP_K))


def _legacy_dense_search(arguments: dict[str, Any]) -> ToolResult:
    """Retain the Day27 dense implementation for compatibility reference."""

    query = str(arguments.get("query") or "").strip()
    top_k = _coerce_top_k(arguments.get("top_k"))
    requested_embedding = settings.rag_embedding_backend.strip().lower()
    requested_vector = settings.rag_vector_backend.strip().lower()
    embedding_backend = create_embedding_backend(settings)
    vector_backend = create_vector_backend(settings, index_path=DEFAULT_INDEX_PATH)
    fallback_used = (
        embedding_backend.name != requested_embedding
        or vector_backend.name != requested_vector
    )

    unavailable = not embedding_backend.is_available() or not vector_backend.is_available()
    if unavailable and settings.rag_real_backend_enabled:
        reasons = [
            backend.describe().get("reason")
            for backend in (embedding_backend, vector_backend)
            if not backend.is_available()
        ]
        return _failure(
            "; ".join(reason for reason in reasons if reason) or "Configured RAG backend unavailable.",
            error_type="backend_unavailable",
            query=query,
            top_k=top_k,
            metadata=_backend_metadata(
                embedding_backend.name,
                vector_backend.name,
                requested_embedding,
                requested_vector,
                False,
            ),
        )
    if not embedding_backend.is_available():
        embedding_backend = DeterministicEmbeddingBackend()
        fallback_used = True
    if not vector_backend.is_available():
        vector_backend = JsonVectorBackend(DEFAULT_INDEX_PATH)
        fallback_used = True

    backend_metadata = _backend_metadata(
        embedding_backend.name,
        vector_backend.name,
        requested_embedding,
        requested_vector,
        fallback_used,
        dimension=None,
    )

    if not query:
        return _failure(
            "Missing required argument: query.",
            error_type="invalid_args",
            query=query,
            top_k=top_k,
            metadata=backend_metadata,
        )

    if vector_backend.name == "json" and not DEFAULT_INDEX_PATH.exists():
        return _failure(
            "RAG index not found, run scripts/build_rag_index.py first.",
            error_type="index_missing",
            query=query,
            top_k=top_k,
            metadata=backend_metadata,
        )

    embedding_result = embedding_backend.embed_query(query)
    if not embedding_result.success or not embedding_result.vectors:
        return _failure(
            embedding_result.error_message or "RAG query embedding failed.",
            error_type="embedding_error",
            query=query,
            top_k=top_k,
            metadata=backend_metadata,
        )

    backend_metadata = _backend_metadata(
        embedding_backend.name,
        vector_backend.name,
        requested_embedding,
        requested_vector,
        fallback_used,
        dimension=embedding_result.dimension,
    )

    search_result = vector_backend.search(embedding_result.vectors[0], top_k=top_k)
    if not search_result.success:
        return _failure(
            search_result.error_message or "RAG search failed.",
            error_type=search_result.metadata.get("error_type", "search_error"),
            query=query,
            top_k=top_k,
            metadata=backend_metadata,
        )

    hits = [hit.to_dict() for hit in search_result.hits]

    output = {
        "query": query,
        "top_k": top_k,
        "embedding_backend": embedding_backend.name,
        "vector_backend": vector_backend.name,
        "fallback_used": fallback_used,
        "metadata": backend_metadata,
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
            "persist_dir": str(DEFAULT_INDEX_PATH.parent),
            "top_k": top_k,
            "hit_count": len(hits),
            **backend_metadata,
        },
    )


def _backend_metadata(
    embedding_backend: str,
    vector_backend: str,
    requested_embedding_backend: str,
    requested_vector_backend: str,
    fallback_used: bool,
    dimension: int | None = None,
) -> dict[str, Any]:
    persist_dir = (
        settings.rag_chroma_dir
        if vector_backend == "chroma"
        else str(DEFAULT_INDEX_PATH.parent)
    )
    return {
        "embedding_backend": embedding_backend,
        "vector_backend": vector_backend,
        "requested_embedding_backend": requested_embedding_backend,
        "requested_vector_backend": requested_vector_backend,
        "fallback_used": fallback_used,
        "model_path": settings.rag_model_path,
        "persist_dir": persist_dir,
        "collection_name": settings.rag_collection_name,
        "dimension": dimension,
    }


def search_rag(arguments: dict[str, Any]) -> ToolResult:
    """Dispatch to the Day33 dense/BM25/hybrid retrieval implementation."""

    return search_rag_with_mode(arguments)
