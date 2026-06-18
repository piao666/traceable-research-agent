"""Build a local RAG index through configurable backend abstractions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings, settings
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
) -> dict[str, Any]:
    """Load local docs, chunk them, build vectors, and persist an index."""

    active_settings = settings_obj or settings
    documents = load_documents(docs_dir)
    chunks = chunk_documents(documents)
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
    )


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
) -> dict[str, Any]:
    path = Path(index_path)
    resolved_persist_dir = persist_dir or (
        active_settings.rag_chroma_dir if vector_backend == "chroma" else str(path.parent)
    )
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
    }
