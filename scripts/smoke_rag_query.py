"""Smoke query the local RAG index."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tools.rag_search import search_rag


if __name__ == "__main__":
    result = search_rag({"query": "trace tool registry", "top_k": 3})
    if not result.success:
        raise SystemExit(result.error_message)
    payload = {
        "embedding_backend": result.metadata.get("embedding_backend"),
        "vector_backend": result.metadata.get("vector_backend"),
        "fallback_used": result.metadata.get("fallback_used"),
        "hits": [
            {
                "chunk_id": hit["chunk_id"],
                "source": hit["source"],
                "score": hit["score"],
                "preview": hit["text"][:120],
            }
            for hit in result.output["hits"]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
