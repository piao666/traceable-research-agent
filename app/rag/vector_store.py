"""Small JSON-backed vector index for local RAG smoke tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.rag.chunker import TextChunk
from app.rag.embeddings import cosine_similarity, embed_text


class LocalVectorStore:
    """In-memory vector store with optional JSON persistence."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []

    @classmethod
    def build_index(cls, chunks: list[TextChunk]) -> "LocalVectorStore":
        records = [
            {
                "chunk_id": chunk["chunk_id"],
                "source": chunk["source"],
                "text": chunk["text"],
                "metadata": chunk["metadata"],
                "embedding": embed_text(chunk["text"]),
            }
            for chunk in chunks
        ]
        return cls(records)

    @classmethod
    def load(cls, index_path: str | Path) -> "LocalVectorStore":
        path = Path(index_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(records=data.get("records", []))

    def save(self, index_path: str | Path) -> Path:
        path = Path(index_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": self.records}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        query_vector = embed_text(query)
        scored = []
        for record in self.records:
            score = cosine_similarity(query_vector, record.get("embedding", {}))
            scored.append(
                {
                    "source": record["source"],
                    "chunk_id": record["chunk_id"],
                    "score": round(score, 6),
                    "text": record["text"],
                    "metadata": record.get("metadata", {}),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: max(1, min(int(top_k), 10))]
