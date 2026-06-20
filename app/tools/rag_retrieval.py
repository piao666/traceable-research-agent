"""Dense, BM25, and RRF hybrid local RAG retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.bm25_backend import BM25RetrievalBackend, TOKENIZER_NAME
from app.rag.embedding_backends import DeterministicEmbeddingBackend, create_embedding_backend
from app.rag.hybrid_search import reciprocal_rank_fusion
from app.rag.vector_backends import JsonVectorBackend, create_vector_backend
from app.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
DENSE_INDEX = ROOT / "workspace" / "index" / "rag_index.json"
BM25_INDEX = ROOT / "workspace" / "index" / "bm25_index.json"


def _dense(query: str, limit: int) -> tuple[list[dict] | None, dict, str | None]:
    requested_embedding = settings.rag_embedding_backend.strip().lower()
    requested_vector = settings.rag_vector_backend.strip().lower()
    embedding = create_embedding_backend(settings)
    vector = create_vector_backend(settings, index_path=DENSE_INDEX)
    backend_fallback = embedding.name != requested_embedding or vector.name != requested_vector
    if (not embedding.is_available() or not vector.is_available()) and settings.rag_real_backend_enabled:
        reasons = [backend.describe().get("reason") for backend in (embedding, vector) if not backend.is_available()]
        return None, {}, "; ".join(reason for reason in reasons if reason) or "Configured dense backend unavailable."
    if not embedding.is_available():
        embedding = DeterministicEmbeddingBackend()
        backend_fallback = True
    if not vector.is_available():
        vector = JsonVectorBackend(DENSE_INDEX)
        backend_fallback = True
    embedded = embedding.embed_query(query)
    metadata = {
        "embedding_backend": embedding.name,
        "vector_backend": vector.name,
        "requested_embedding_backend": requested_embedding,
        "requested_vector_backend": requested_vector,
        "dense_backend_fallback_used": backend_fallback,
        "dimension": embedded.dimension,
        "persist_dir": settings.rag_chroma_dir if vector.name == "chroma" else str(DENSE_INDEX.parent),
        "collection_name": settings.rag_collection_name,
    }
    if not embedded.success or not embedded.vectors:
        return None, metadata, embedded.error_message or "RAG query embedding failed."
    result = vector.search(embedded.vectors[0], top_k=limit)
    if not result.success:
        return None, {**metadata, **result.metadata}, result.error_message or "Dense search failed."
    return [hit.to_dict() for hit in result.hits], metadata, None


def _bm25(query: str, limit: int) -> tuple[list[dict] | None, dict, str | None]:
    if not settings.rag_bm25_enabled:
        return None, {}, "BM25 retrieval is disabled."
    result = BM25RetrievalBackend(BM25_INDEX).search(query, top_k=limit)
    if not result.success:
        return None, result.metadata, result.error_message or "BM25 search failed."
    return result.hits, result.metadata, None


def search_rag(arguments: dict[str, Any]) -> ToolResult:
    query = str(arguments.get("query") or "").strip()
    try:
        top_k = max(1, min(int(arguments.get("top_k") or 3), 10))
    except (TypeError, ValueError):
        top_k = 3
    requested = str(arguments.get("retrieval_mode") or settings.rag_retrieval_mode).strip().lower()
    requested = requested if requested in {"dense", "bm25", "hybrid"} else "dense"
    metadata: dict[str, Any] = {
        "retrieval_mode": requested,
        "requested_retrieval_mode": requested,
        "fallback_used": False,
        "dense_hit_count": 0,
        "bm25_hit_count": 0,
        "rrf_k": settings.rag_rrf_k,
        "bm25_enabled": settings.rag_bm25_enabled,
        "hybrid_enabled": settings.rag_hybrid_enabled,
        "tokenizer": TOKENIZER_NAME,
        "index_paths": {"dense": str(DENSE_INDEX), "bm25": str(BM25_INDEX)},
    }
    if not query:
        return ToolResult(success=False, error_message="Missing required argument: query.", metadata={"error_type": "invalid_args", **metadata})

    dense_hits = bm25_hits = None
    dense_meta: dict[str, Any] = {}
    bm25_meta: dict[str, Any] = {}
    dense_error = bm25_error = None
    if requested in {"dense", "hybrid"}:
        multiplier = settings.rag_dense_candidate_multiplier if requested == "hybrid" else 1
        dense_hits, dense_meta, dense_error = _dense(query, top_k * multiplier)
    if requested in {"bm25", "hybrid"}:
        multiplier = settings.rag_bm25_candidate_multiplier if requested == "hybrid" else 1
        bm25_hits, bm25_meta, bm25_error = _bm25(query, top_k * multiplier)

    actual = requested
    if requested == "dense" and dense_hits is not None:
        hits = dense_hits[:top_k]
    elif requested == "bm25" and bm25_hits is not None:
        hits = bm25_hits[:top_k]
    elif requested == "hybrid" and not settings.rag_hybrid_enabled:
        return ToolResult(success=False, error_message="Hybrid retrieval is disabled.", metadata={"error_type": "backend_disabled", **metadata})
    elif requested == "hybrid" and dense_hits is not None and bm25_hits is not None:
        hits = reciprocal_rank_fusion([dense_hits, bm25_hits], settings.rag_rrf_k, top_k)
    elif requested == "hybrid" and dense_hits is not None:
        actual, hits = "dense", dense_hits[:top_k]
    elif requested == "hybrid" and bm25_hits is not None:
        actual, hits = "bm25", bm25_hits[:top_k]
    else:
        message = bm25_error if requested == "bm25" else dense_error
        if requested == "hybrid":
            message = f"Hybrid retrieval unavailable: dense={dense_error}; bm25={bm25_error}"
        error_type = bm25_meta.get("error_type") if requested == "bm25" else dense_meta.get("error_type")
        return ToolResult(success=False, error_message=message or "RAG search unavailable.", metadata={"error_type": error_type or "search_error", **metadata})

    metadata.update(dense_meta)
    metadata.update({f"bm25_{key}": value for key, value in bm25_meta.items() if key != "success"})
    metadata.update(
        {
            "error_type": None,
            "retrieval_mode": actual,
            "fallback_used": actual != requested,
            "fallback_reason": None if actual == requested else (bm25_error if actual == "dense" else dense_error),
            "dense_hit_count": len(dense_hits or []),
            "bm25_hit_count": len(bm25_hits or []),
            "hit_count": len(hits),
            "top_k": top_k,
        }
    )
    public_hits = [
        {
            "source": hit.get("source"),
            "chunk_id": hit.get("chunk_id"),
            "score": hit.get("score"),
            "text": hit.get("text"),
            "metadata": hit.get("metadata") or {},
        }
        for hit in hits
    ]
    output = {
        "query": query,
        "top_k": top_k,
        "embedding_backend": metadata.get("embedding_backend"),
        "vector_backend": metadata.get("vector_backend"),
        "retrieval_mode": actual,
        "fallback_used": metadata["fallback_used"],
        "metadata": metadata,
        "hits": public_hits,
    }
    return ToolResult(
        success=True,
        output=output,
        output_summary=f"rag_search returned {len(public_hits)} hits using {actual} for query: {query}",
        metadata=metadata,
    )
