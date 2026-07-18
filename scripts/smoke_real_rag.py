"""Build and query the optional local SentenceTransformers/Chroma RAG path."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(os.getenv("RAG_MODEL_PATH", r"E:\Models\bge-small-zh-v1.5"))


def _print_and_exit(payload: dict, code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if not MODEL_PATH.is_dir():
    _print_and_exit({"real_rag": "skipped", "reason": "model_missing"})

missing_packages = [
    package
    for package in ("sentence_transformers", "chromadb")
    if importlib.util.find_spec(package) is None
]
if missing_packages:
    _print_and_exit(
        {"real_rag": "failed", "reason": "dependencies_missing", "packages": missing_packages},
        code=1,
    )

os.environ.update(
    {
        "RAG_REAL_BACKEND_ENABLED": "true",
        "RAG_EMBEDDING_BACKEND": "sentence_transformers",
        "RAG_VECTOR_BACKEND": "chroma",
        "RAG_MODEL_PATH": str(MODEL_PATH),
        "RAG_CHROMA_DIR": "workspace/chroma",
        "RAG_COLLECTION_NAME": "traceable_research_docs",
        "RAG_DEVICE": "cpu",
        "RAG_NORMALIZE_EMBEDDINGS": "true",
        "ANONYMIZED_TELEMETRY": "false",
    }
)
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.rag.build_index import build_local_index
from app.tools.rag_search import search_rag


def main() -> None:
    build_result = build_local_index(settings_obj=settings)
    if not build_result.get("success"):
        _print_and_exit(
            {"real_rag": "failed", "reason": build_result.get("error_message")},
            code=1,
        )

    result = search_rag(
        {"query": "trace persistence tool registry report", "top_k": 3}
    )
    assert result.success, result.error_message
    assert len(result.output["hits"]) >= 1
    assert result.metadata["embedding_backend"] == "sentence_transformers"
    assert result.metadata["vector_backend"] == "chroma"
    assert result.metadata["fallback_used"] is False

    print(
        json.dumps(
            {
                "real_rag": "ok",
                "model_path": str(MODEL_PATH),
                "embedding_backend": result.metadata["embedding_backend"],
                "vector_backend": result.metadata["vector_backend"],
                "dimension": result.metadata["dimension"],
                "chunks": build_result["chunks"],
                "hits": len(result.output["hits"]),
                "fallback_used": result.metadata["fallback_used"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
