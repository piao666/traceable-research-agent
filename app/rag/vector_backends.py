"""Vector backend contracts and JSON persistence implementation."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.rag.embedding_backends import DenseVector, EmbeddingVector, SparseVector
from app.rag.embeddings import cosine_similarity

if TYPE_CHECKING:
    from app.config import Settings


DEFAULT_JSON_INDEX_PATH = Path("workspace/index/rag_index.json")


@dataclass(slots=True)
class VectorHit:
    source: str
    chunk_id: str
    score: float
    text: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class VectorSearchResult:
    success: bool
    hits: list[VectorHit] = field(default_factory=list)
    backend: str = ""
    error_message: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class VectorIndexSummary:
    success: bool
    documents: int = 0
    chunks: int = 0
    backend: str = ""
    index_path: str | None = None
    error_message: str | None = None
    metadata: dict = field(default_factory=dict)


class VectorBackend(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether this backend can execute in the current runtime."""

    @abstractmethod
    def describe(self) -> dict:
        """Return backend metadata."""

    @abstractmethod
    def build_index(
        self,
        chunks: list[dict],
        vectors: list[EmbeddingVector],
        persist_path: str | Path | None = None,
    ) -> VectorIndexSummary:
        """Build and persist an index."""

    @abstractmethod
    def search(self, query_vector: EmbeddingVector, top_k: int = 3) -> VectorSearchResult:
        """Search the current persisted index."""


class JsonVectorBackend(VectorBackend):
    """JSON index compatible with the existing Day8 index format."""

    name = "json"

    def __init__(self, index_path: str | Path = DEFAULT_JSON_INDEX_PATH) -> None:
        self.index_path = Path(index_path)

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "index_path": str(self.index_path),
            "runtime_only": True,
        }

    def build_index(
        self,
        chunks: list[dict],
        vectors: list[EmbeddingVector],
        persist_path: str | Path | None = None,
    ) -> VectorIndexSummary:
        path = Path(persist_path) if persist_path else self.index_path
        if len(chunks) != len(vectors):
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                error_message="Chunk and vector counts do not match.",
            )
        try:
            records = [
                {
                    "chunk_id": chunk["chunk_id"],
                    "source": chunk["source"],
                    "text": chunk["text"],
                    "metadata": chunk.get("metadata", {}),
                    "embedding": vector,
                }
                for chunk, vector in zip(chunks, vectors)
            ]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": 1, "records": records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return VectorIndexSummary(
                success=True,
                documents=len({chunk.get("source") for chunk in chunks}),
                chunks=len(chunks),
                backend=self.name,
                index_path=str(path),
                metadata={"record_count": len(records), "format_version": 1},
            )
        except Exception as exc:
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                index_path=str(path),
                error_message=f"JSON index build failed: {exc}",
            )

    def search(self, query_vector: EmbeddingVector, top_k: int = 3) -> VectorSearchResult:
        if not self.index_path.exists():
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message="JSON vector index not found.",
                metadata={"error_type": "index_missing", "index_path": str(self.index_path)},
            )
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
            hits = []
            for record in payload.get("records", []):
                score = _vector_similarity(query_vector, record.get("embedding", {}))
                hits.append(
                    VectorHit(
                        source=record["source"],
                        chunk_id=record["chunk_id"],
                        score=round(score, 6),
                        text=record["text"],
                        metadata=record.get("metadata", {}),
                    )
                )
            hits.sort(key=lambda item: item.score, reverse=True)
            limit = max(1, min(int(top_k), 10))
            return VectorSearchResult(
                success=True,
                hits=hits[:limit],
                backend=self.name,
                metadata={"index_path": str(self.index_path), "record_count": len(hits)},
            )
        except Exception as exc:
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message=f"JSON vector search failed: {exc}",
                metadata={"error_type": "search_error", "index_path": str(self.index_path)},
            )


class UnavailableVectorBackend(VectorBackend):
    """Stable placeholder for vector databases not installed yet."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "available": False, "reason": self.reason}

    def build_index(
        self,
        chunks: list[dict],
        vectors: list[EmbeddingVector],
        persist_path: str | Path | None = None,
    ) -> VectorIndexSummary:
        return VectorIndexSummary(
            success=False,
            backend=self.name,
            error_message=f"Vector backend unavailable: {self.reason}",
            metadata={"reason": self.reason},
        )

    def search(self, query_vector: EmbeddingVector, top_k: int = 3) -> VectorSearchResult:
        return VectorSearchResult(
            success=False,
            backend=self.name,
            error_message=f"Vector backend unavailable: {self.reason}",
            metadata={"reason": self.reason},
        )


def create_vector_backend(
    settings: "Settings",
    index_path: str | Path = DEFAULT_JSON_INDEX_PATH,
) -> VectorBackend:
    """Create the requested backend without importing optional vector packages."""

    name = settings.rag_vector_backend.strip().lower()
    if name == "json":
        return JsonVectorBackend(index_path)
    if name == "chroma":
        return UnavailableVectorBackend(name, "chroma backend planned for Day27/Day28")
    if name == "faiss":
        return UnavailableVectorBackend(name, "faiss backend planned as an optional future backend")
    return UnavailableVectorBackend(name or "unknown", "unsupported vector backend")


def _vector_similarity(left: EmbeddingVector, right: EmbeddingVector) -> float:
    if isinstance(left, dict) and isinstance(right, dict):
        return cosine_similarity(left, right)
    if isinstance(left, list) and isinstance(right, list):
        return _dense_cosine_similarity(left, right)
    return 0.0


def _dense_cosine_similarity(left: DenseVector, right: DenseVector) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
