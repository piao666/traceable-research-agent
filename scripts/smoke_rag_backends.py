"""Validate Day26 RAG abstractions without optional model dependencies."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, settings
from app.rag.build_index import build_local_index
from app.rag.embedding_backends import create_embedding_backend
from app.rag.vector_backends import create_vector_backend
from app.tools.rag_search import search_rag


def main() -> None:
    embedding = create_embedding_backend(settings)
    vector = create_vector_backend(settings)
    assert embedding.name == "deterministic" and embedding.is_available()
    assert vector.name == "json" and vector.is_available()

    query_result = embedding.embed_query("trace tool registry")
    assert query_result.success and len(query_result.vectors) == 1

    build_result = build_local_index()
    assert build_result["success"]
    search_result = search_rag({"query": "trace tool registry", "top_k": 3})
    assert search_result.success and search_result.output["hits"]

    unavailable_settings = Settings(
        rag_embedding_backend="sentence_transformers",
        rag_vector_backend="chroma",
        rag_real_backend_enabled=True,
    )
    unavailable_embedding = create_embedding_backend(unavailable_settings)
    unavailable_vector = create_vector_backend(unavailable_settings)
    assert not unavailable_embedding.is_available()
    assert not unavailable_vector.is_available()
    assert not unavailable_embedding.embed_query("query").success
    assert not unavailable_vector.search({}, top_k=3).success

    fallback_settings = Settings(
        rag_embedding_backend="sentence_transformers",
        rag_vector_backend="chroma",
        rag_real_backend_enabled=False,
    )
    fallback_result = build_local_index(settings_obj=fallback_settings)
    assert fallback_result["success"]
    assert fallback_result["embedding_backend"] == "deterministic"
    assert fallback_result["vector_backend"] == "json"
    assert fallback_result["fallback_used"] is True

    optional_modules_loaded = any(
        name == package or name.startswith(f"{package}.")
        for package in ("sentence_transformers", "chromadb", "faiss")
        for name in sys.modules
    )
    assert not optional_modules_loaded

    payload = {
        "rag_backends": "ok",
        "config": settings.get_safe_rag_config_summary(),
        "embedding_backend": embedding.name,
        "vector_backend": vector.name,
        "deterministic_embedding": "ok",
        "json_vector_search": "ok",
        "unavailable_backends": "safe",
        "lightweight_fallback": "ok",
        "optional_packages_loaded": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
