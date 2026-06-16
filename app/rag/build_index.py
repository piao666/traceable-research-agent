"""Build the local lightweight RAG index."""

from __future__ import annotations

from pathlib import Path

from app.rag.chunker import chunk_documents
from app.rag.loader import load_documents
from app.rag.vector_store import LocalVectorStore


def build_local_index(
    docs_dir: str | Path = "workspace/docs",
    index_path: str | Path = "workspace/index/rag_index.json",
) -> dict[str, int | str]:
    """Load local docs, chunk them, build vectors, and persist an index."""

    documents = load_documents(docs_dir)
    chunks = chunk_documents(documents)
    store = LocalVectorStore.build_index(chunks)
    saved_path = store.save(index_path)
    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "index_path": str(saved_path),
    }
