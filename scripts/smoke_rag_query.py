"""Smoke query the local RAG index."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag.vector_store import LocalVectorStore


if __name__ == "__main__":
    store = LocalVectorStore.load("workspace/index/rag_index.json")
    hits = store.search("trace tool registry", top_k=3)
    for hit in hits:
        print(
            {
                "chunk_id": hit["chunk_id"],
                "source": hit["source"],
                "score": hit["score"],
                "preview": hit["text"][:120],
            }
        )
