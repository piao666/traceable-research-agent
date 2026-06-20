"""Build a local RAG index through configurable backend abstractions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings, settings
from app.rag.bm25_backend import BM25RetrievalBackend
from app.rag.chunker import chunk_documents
from app.rag.embedding_backends import (
    DeterministicEmbeddingBackend,
    create_embedding_backend,
)
from app.rag.loader import load_documents
from app.rag.vector_backends import JsonVectorBackend, create_vector_backend


def build_local_index(
    docs_dir: str | Path = "workspace/docs",
    index_path: str | Path = "workspace/index/rag_index.json",
    settings_obj: Settings | None = None,
    chunk_size: int = 500,
    chunk_overlap: int | None = None,
    bm25_index_path: str | Path = "workspace/index/bm25_index.json",
) -> dict[str, Any]:
    """Load local docs, chunk them, build vectors, and persist an index."""

    active_settings = settings_obj or settings
    documents = load_documents(docs_dir)
    overlap = chunk_overlap if chunk_overlap is not None else min(80, max(0, chunk_size // 5))
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=overlap)
    requested_embedding = active_settings.rag_embedding_backend.strip().lower()
    requested_vector = active_settings.rag_vector_backend.strip().lower()
    embedding_backend = create_embedding_backend(active_settings)
    vector_backend = create_vector_backend(active_settings, index_path=index_path)
    fallback_used = (
        embedding_backend.name != requested_embedding
        or vector_backend.name != requested_vector
    )

    unavailable = not embedding_backend.is_available() or not vector_backend.is_available()
    if unavailable and active_settings.rag_real_backend_enabled:
        reasons = [
            backend.describe().get("reason")
            for backend in (embedding_backend, vector_backend)
            if not backend.is_available()
        ]
        return _summary(
            active_settings,
            success=False,
            documents=len(documents),
            chunks=len(chunks),
            index_path=index_path,
            embedding_backend=embedding_backend.name,
            vector_backend=vector_backend.name,
            requested_embedding_backend=requested_embedding,
            requested_vector_backend=requested_vector,
            fallback_used=False,
            error_message="; ".join(reason for reason in reasons if reason),
        )
    if not embedding_backend.is_available():
        embedding_backend = DeterministicEmbeddingBackend()
        fallback_used = True
    if not vector_backend.is_available():
        vector_backend = JsonVectorBackend(index_path)
        fallback_used = True

    embedding_result = embedding_backend.embed_texts([chunk["text"] for chunk in chunks])
    if not embedding_result.success:
        return _summary(
            active_settings,
            success=False,
            documents=len(documents),
            chunks=len(chunks),
            index_path=index_path,
            embedding_backend=embedding_backend.name,
            vector_backend=vector_backend.name,
            requested_embedding_backend=requested_embedding,
            requested_vector_backend=requested_vector,
            fallback_used=fallback_used,
            error_message=embedding_result.error_message,
        )

    persist_path = index_path if vector_backend.name == "json" else None
    index_result = vector_backend.build_index(
        chunks,
        embedding_result.vectors,
        persist_path,
    )
    bm25_summary = _build_bm25(active_settings, chunks, bm25_index_path)
    return _summary(
        active_settings,
        success=index_result.success,
        documents=len(documents),
        chunks=len(chunks),
        index_path=index_result.index_path or index_path,
        embedding_backend=embedding_backend.name,
        vector_backend=vector_backend.name,
        requested_embedding_backend=requested_embedding,
        requested_vector_backend=requested_vector,
        fallback_used=fallback_used,
        error_message=index_result.error_message,
        dimension=embedding_result.dimension,
        persist_dir=index_result.metadata.get("persist_dir"),
        bm25_summary=bm25_summary,
        chunk_size=chunk_size,
    )


def _build_bm25(
    active_settings: Settings,
    chunks: list[dict[str, Any]],
    index_path: str | Path,
) -> dict[str, Any]:
    if not active_settings.rag_bm25_enabled:
        return {"success": True, "enabled": False, "index_path": str(index_path), "corpus_size": 0}
    backend = BM25RetrievalBackend(index_path)
    built = backend.build(chunks)
    if not built.get("success"):
        return {**built, "enabled": True}
    saved = backend.save(index_path)
    return {**saved, "enabled": True}


def _summary(
    active_settings: Settings,
    *,
    success: bool,
    documents: int,
    chunks: int,
    index_path: str | Path,
    embedding_backend: str,
    vector_backend: str,
    requested_embedding_backend: str,
    requested_vector_backend: str,
    fallback_used: bool,
    error_message: str | None,
    dimension: int | None = None,
    persist_dir: str | None = None,
    bm25_summary: dict[str, Any] | None = None,
    chunk_size: int = 500,
) -> dict[str, Any]:
    path = Path(index_path)
    resolved_persist_dir = persist_dir or (
        active_settings.rag_chroma_dir if vector_backend == "chroma" else str(path.parent)
    )
    bm25 = bm25_summary or {
        "success": not active_settings.rag_bm25_enabled,
        "enabled": active_settings.rag_bm25_enabled,
        "index_path": str(path.parent / "bm25_index.json"),
        "corpus_size": 0,
    }
    available_modes = ["dense"]
    if bm25.get("success") and bm25.get("enabled"):
        available_modes.append("bm25")
        if active_settings.rag_hybrid_enabled:
            available_modes.append("hybrid")
    return {
        "success": success,
        "documents": documents,
        "chunks": chunks,
        "index_path": str(path),
        "embedding_backend": embedding_backend,
        "vector_backend": vector_backend,
        "requested_embedding_backend": requested_embedding_backend,
        "requested_vector_backend": requested_vector_backend,
        "fallback_used": fallback_used,
        "model_path": active_settings.rag_model_path,
        "collection_name": active_settings.rag_collection_name,
        "persist_dir": resolved_persist_dir,
        "dimension": dimension,
        "error_message": error_message,
        "chunk_size": chunk_size,
        "bm25_enabled": active_settings.rag_bm25_enabled,
        "bm25_success": bool(bm25.get("success")),
        "bm25_index_path": bm25.get("index_path"),
        "bm25_corpus_size": bm25.get("corpus_size", 0),
        "bm25_error_message": bm25.get("error_message"),
        "retrieval_modes_available": available_modes,
    }
