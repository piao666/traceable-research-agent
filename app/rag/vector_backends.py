"""Vector backend contracts with JSON and Chroma persistence."""

from __future__ import annotations

import importlib.util
import json
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.rag.embedding_backends import DenseVector, EmbeddingVector
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
            limit = max(1, min(int(top_k), 100))
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


class ChromaVectorBackend(VectorBackend):
    """Persistent Chroma collection backed by caller-provided dense vectors."""

    name = "chroma"

    def __init__(self, persist_dir: str | Path, collection_name: str) -> None:
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name

    def is_available(self) -> bool:
        return self._unavailable_reason() is None

    def describe(self) -> dict:
        reason = self._unavailable_reason()
        return {
            "name": self.name,
            "available": reason is None,
            "reason": reason,
            "persist_dir": str(self.persist_dir),
            "collection_name": self.collection_name,
            "runtime_only": True,
        }

    def build_index(
        self,
        chunks: list[dict],
        vectors: list[EmbeddingVector],
        persist_path: str | Path | None = None,
    ) -> VectorIndexSummary:
        persist_dir = Path(persist_path) if persist_path else self.persist_dir
        reason = self._unavailable_reason()
        if reason:
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                index_path=str(persist_dir),
                error_message=f"Chroma backend unavailable: {reason}",
                metadata=self._metadata(error_type="backend_unavailable"),
            )
        if len(chunks) != len(vectors):
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                index_path=str(persist_dir),
                error_message="Chunk and vector counts do not match.",
                metadata=self._metadata(error_type="invalid_vectors"),
            )
        if any(not isinstance(vector, list) for vector in vectors):
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                index_path=str(persist_dir),
                error_message="Chroma requires dense list embeddings.",
                metadata=self._metadata(error_type="invalid_vectors"),
            )
        try:
            client = self._client(persist_dir)
            try:
                client.delete_collection(self.collection_name)
            except Exception:
                pass
            collection = client.create_collection(
                self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            if chunks:
                collection.add(
                    ids=[str(chunk["chunk_id"]) for chunk in chunks],
                    documents=[str(chunk["text"]) for chunk in chunks],
                    embeddings=vectors,
                    metadatas=[_chroma_metadata(chunk) for chunk in chunks],
                )
            count = collection.count()
            return VectorIndexSummary(
                success=True,
                documents=len({chunk.get("source") for chunk in chunks}),
                chunks=len(chunks),
                backend=self.name,
                index_path=str(persist_dir),
                metadata={**self._metadata(), "collection_count": count},
            )
        except Exception as exc:
            return VectorIndexSummary(
                success=False,
                backend=self.name,
                index_path=str(persist_dir),
                error_message=f"Chroma index build failed: {exc}",
                metadata=self._metadata(error_type="index_build_error"),
            )

    def search(self, query_vector: EmbeddingVector, top_k: int = 3) -> VectorSearchResult:
        reason = self._unavailable_reason()
        if reason:
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message=f"Chroma backend unavailable: {reason}",
                metadata=self._metadata(error_type="backend_unavailable"),
            )
        if not isinstance(query_vector, list):
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message="Chroma requires a dense query embedding.",
                metadata=self._metadata(error_type="invalid_vectors"),
            )
        if not self.persist_dir.exists():
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message="Chroma RAG index not found, run scripts/build_rag_index.py first.",
                metadata=self._metadata(error_type="index_missing"),
            )
        try:
            client = self._client(self.persist_dir)
            collection = client.get_collection(self.collection_name)
            count = collection.count()
            if count == 0:
                return VectorSearchResult(
                    success=True,
                    hits=[],
                    backend=self.name,
                    metadata={**self._metadata(), "collection_count": 0},
                )
            limit = min(max(1, int(top_k)), 100, count)
            result = collection.query(
                query_embeddings=[query_vector],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
            ids = (result.get("ids") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            hits = []
            for chunk_id, document, metadata, distance in zip(
                ids, documents, metadatas, distances
            ):
                details = dict(metadata or {})
                distance_value = float(distance)
                details["distance"] = distance_value
                details["score_transform"] = "1/(1+cosine_distance)"
                hits.append(
                    VectorHit(
                        source=str(details.get("source") or ""),
                        chunk_id=str(details.get("chunk_id") or chunk_id),
                        score=round(1.0 / (1.0 + max(distance_value, 0.0)), 6),
                        text=str(document or ""),
                        metadata=details,
                    )
                )
            return VectorSearchResult(
                success=True,
                hits=hits,
                backend=self.name,
                metadata={**self._metadata(), "collection_count": count},
            )
        except Exception as exc:
            message = str(exc)
            error_type = "index_missing" if "does not exist" in message.lower() else "search_error"
            return VectorSearchResult(
                success=False,
                backend=self.name,
                error_message=f"Chroma vector search failed: {message}",
                metadata=self._metadata(error_type=error_type),
            )

    def _client(self, persist_dir: Path):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        persist_dir.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def _unavailable_reason(self) -> str | None:
        if importlib.util.find_spec("chromadb") is None:
            return "chromadb is not installed"
        if not self.collection_name.strip():
            return "collection name is not configured"
        return None

    def _metadata(self, error_type: str | None = None) -> dict:
        return {
            "persist_dir": str(self.persist_dir),
            "collection_name": self.collection_name,
            "vector_backend": self.name,
            "error_type": error_type,
        }


class UnavailableVectorBackend(VectorBackend):
    """Stable placeholder for vector databases unavailable at runtime."""

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
    """Create the requested backend while preserving lightweight fallback."""

    name = settings.rag_vector_backend.strip().lower()
    if name == "json":
        return JsonVectorBackend(index_path)
    if name == "chroma":
        if not settings.rag_real_backend_enabled:
            return JsonVectorBackend(index_path)
        backend = ChromaVectorBackend(
            persist_dir=settings.rag_chroma_dir,
            collection_name=settings.rag_collection_name,
        )
        if backend.is_available():
            return backend
        return UnavailableVectorBackend(
            name,
            backend.describe().get("reason") or "chromadb unavailable",
        )
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


def _chroma_metadata(chunk: dict) -> dict:
    metadata = {
        "source": str(chunk.get("source") or ""),
        "chunk_id": str(chunk.get("chunk_id") or ""),
    }
    for key, value in (chunk.get("metadata") or {}).items():
        if isinstance(value, (str, int, float, bool)):
            metadata[str(key)] = value
    return metadata
