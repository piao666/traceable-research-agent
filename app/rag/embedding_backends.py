"""Embedding backend contracts and the lightweight deterministic backend."""

from __future__ import annotations

import importlib.util
import math
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from app.rag.embeddings import embed_text

if TYPE_CHECKING:
    from app.config import Settings


SparseVector: TypeAlias = dict[str, float]
DenseVector: TypeAlias = list[float]
EmbeddingVector: TypeAlias = SparseVector | DenseVector
_MODEL_CACHE: dict[tuple[str, str], object] = {}
_MODEL_CACHE_LOCK = threading.Lock()


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


class SentenceTransformersEmbeddingBackend(EmbeddingBackend):
    """Dense embeddings from a local SentenceTransformers model."""

    name = "sentence_transformers"

    def __init__(
        self,
        model_path: str | None,
        device: str = "cpu",
        normalize_embeddings: bool = True,
    ) -> None:
        self.model_path = Path(model_path).resolve() if model_path else None
        self.device = device
        self.normalize_embeddings = normalize_embeddings

    def is_available(self) -> bool:
        return bool(
            self.model_path
            and self.model_path.is_dir()
            and importlib.util.find_spec("sentence_transformers") is not None
        )

    def describe(self) -> dict:
        reason = self._unavailable_reason()
        return {
            "name": self.name,
            "available": reason is None,
            "reason": reason,
            "model_path": str(self.model_path) if self.model_path else None,
            "device": self.device,
            "normalize_embeddings": self.normalize_embeddings,
            "local_files_only": True,
        }

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        metadata = self._metadata(dimension=None)
        if not texts:
            return EmbeddingResult(
                success=True,
                vectors=[],
                backend=self.name,
                dimension=None,
                metadata=metadata,
            )

        reason = self._unavailable_reason()
        if reason:
            return EmbeddingResult(
                success=False,
                backend=self.name,
                error_message=f"SentenceTransformers backend unavailable: {reason}",
                metadata=metadata,
            )

        try:
            model = self._load_model()
            normalized_by_model = True
            try:
                encoded = model.encode(
                    texts,
                    convert_to_numpy=True,
                    normalize_embeddings=self.normalize_embeddings,
                    show_progress_bar=False,
                )
            except TypeError:
                encoded = model.encode(
                    texts,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                normalized_by_model = False

            vectors = [[float(value) for value in vector] for vector in encoded]
            if self.normalize_embeddings and not normalized_by_model:
                vectors = [_l2_normalize(vector) for vector in vectors]
            dimension = len(vectors[0]) if vectors else None
            metadata = self._metadata(dimension=dimension)
            metadata["normalization_mode"] = (
                "encode_parameter" if normalized_by_model else "manual_l2"
            )
            metadata["count"] = len(vectors)
            return EmbeddingResult(
                success=True,
                vectors=vectors,
                backend=self.name,
                dimension=dimension,
                metadata=metadata,
            )
        except Exception as exc:
            return EmbeddingResult(
                success=False,
                backend=self.name,
                error_message=f"SentenceTransformers embedding failed: {exc}",
                metadata=metadata,
            )

    def _load_model(self):
        cache_key = (str(self.model_path), self.device)
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(cache_key)
            if model is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    str(self.model_path),
                    device=self.device,
                    local_files_only=True,
                )
                _MODEL_CACHE[cache_key] = model
        return model

    def _unavailable_reason(self) -> str | None:
        if importlib.util.find_spec("sentence_transformers") is None:
            return "sentence-transformers is not installed"
        if self.model_path is None:
            return "model path is not configured"
        if not self.model_path.is_dir():
            return f"model path missing: {self.model_path}"
        return None

    def _metadata(self, dimension: int | None) -> dict:
        return {
            "backend": self.name,
            "model_path": str(self.model_path) if self.model_path else None,
            "device": self.device,
            "normalize_embeddings": self.normalize_embeddings,
            "dimension": dimension,
            "local_files_only": True,
        }


def create_embedding_backend(settings: "Settings") -> EmbeddingBackend:
    """Create the requested backend while preserving lightweight fallback."""

    name = settings.rag_embedding_backend.strip().lower()
    if name == "deterministic":
        return DeterministicEmbeddingBackend()
    if name == "sentence_transformers":
        if not settings.rag_real_backend_enabled:
            return DeterministicEmbeddingBackend()
        backend = SentenceTransformersEmbeddingBackend(
            model_path=settings.rag_model_path,
            device=settings.rag_device,
            normalize_embeddings=settings.rag_normalize_embeddings,
        )
        if backend.is_available():
            return backend
        return UnavailableEmbeddingBackend(
            name,
            backend.describe().get("reason") or "sentence-transformers unavailable",
        )
    return UnavailableEmbeddingBackend(name or "unknown", "unsupported embedding backend")


def _l2_normalize(vector: DenseVector) -> DenseVector:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
