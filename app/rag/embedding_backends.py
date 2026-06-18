"""Embedding backend contracts and the lightweight deterministic backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

from app.rag.embeddings import embed_text

if TYPE_CHECKING:
    from app.config import Settings


SparseVector: TypeAlias = dict[str, float]
DenseVector: TypeAlias = list[float]
EmbeddingVector: TypeAlias = SparseVector | DenseVector


@dataclass(slots=True)
class EmbeddingResult:
    """Structured embedding result shared by sparse and future dense backends."""

    success: bool
    vectors: list[EmbeddingVector] = field(default_factory=list)
    backend: str = ""
    dimension: int | None = None
    error_message: str | None = None
    metadata: dict = field(default_factory=dict)


class EmbeddingBackend(ABC):
    """Interface implemented by every embedding backend."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether this backend can execute in the current runtime."""

    @abstractmethod
    def describe(self) -> dict:
        """Return non-secret backend metadata."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        """Embed a batch of document texts."""

    def embed_query(self, query: str) -> EmbeddingResult:
        """Embed one query through the batch contract."""

        return self.embed_texts([query])


class DeterministicEmbeddingBackend(EmbeddingBackend):
    """Existing normalized token-frequency embedding implementation."""

    name = "deterministic"

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": True,
            "vector_format": "sparse_token_frequency",
            "fallback_safe": True,
        }

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        try:
            vectors = [embed_text(text) for text in texts]
            return EmbeddingResult(
                success=True,
                vectors=vectors,
                backend=self.name,
                dimension=None,
                metadata={
                    "vector_format": "sparse_token_frequency",
                    "fallback_safe": True,
                    "count": len(vectors),
                },
            )
        except Exception as exc:
            return EmbeddingResult(
                success=False,
                backend=self.name,
                error_message=f"Deterministic embedding failed: {exc}",
                metadata={"fallback_safe": True},
            )


class UnavailableEmbeddingBackend(EmbeddingBackend):
    """Stable placeholder for configured backends not installed yet."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "available": False, "reason": self.reason}

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            success=False,
            backend=self.name,
            error_message=f"Embedding backend unavailable: {self.reason}",
            metadata={"requested_count": len(texts), "reason": self.reason},
        )


def create_embedding_backend(settings: "Settings") -> EmbeddingBackend:
    """Create the requested backend without importing optional model packages."""

    name = settings.rag_embedding_backend.strip().lower()
    if name == "deterministic":
        return DeterministicEmbeddingBackend()
    if name == "sentence_transformers":
        return UnavailableEmbeddingBackend(
            name,
            "sentence_transformers backend planned for Day27",
        )
    return UnavailableEmbeddingBackend(name or "unknown", "unsupported embedding backend")
