"""Lightweight persisted BM25 retrieval over the shared RAG chunks."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_BM25_INDEX_PATH = Path("workspace/index/bm25_index.json")
TOKENIZER_NAME = "unicode_words+cjk_unigram_bigram"
_WORD_RE = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def tokenize_text(text: str) -> list[str]:
    """Tokenize Latin words/numbers plus CJK unigrams and bigrams."""

    normalized = str(text or "").lower()
    tokens = _WORD_RE.findall(normalized)
    cjk = _CJK_RE.findall(normalized)
    tokens.extend(cjk)
    tokens.extend(a + b for a, b in zip(cjk, cjk[1:]))
    return tokens


def tokenize_query(query: str) -> list[str]:
    return tokenize_text(query)


@dataclass(slots=True)
class BM25SearchResult:
    success: bool
    hits: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BM25RetrievalBackend:
    name = "bm25"

    def __init__(self, index_path: str | Path = DEFAULT_BM25_INDEX_PATH) -> None:
        self.index_path = Path(index_path)
        self._chunks: list[dict[str, Any]] = []
        self._tokenized_corpus: list[list[str]] = []
        self._index = None

    def is_available(self) -> bool:
        return importlib.util.find_spec("rank_bm25") is not None

    def build(self, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.is_available():
            return self._metadata(False, "rank-bm25 is not installed")
        self._chunks = [dict(chunk) for chunk in chunks]
        self._tokenized_corpus = [tokenize_text(chunk.get("text", "")) for chunk in chunks]
        self._rebuild()
        return self._metadata(True)

    def save(self, path: str | Path | None = None) -> dict[str, Any]:
        target = Path(path) if path else self.index_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tokenizer": TOKENIZER_NAME,
                        "chunks": self._chunks,
                        "tokenized_corpus": self._tokenized_corpus,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.index_path = target
            return self._metadata(True)
        except Exception as exc:
            return self._metadata(False, f"BM25 index save failed: {exc}")

    def load(self, path: str | Path | None = None) -> dict[str, Any]:
        target = Path(path) if path else self.index_path
        if not target.exists():
            return self._metadata(False, "BM25 index not found", error_type="index_missing")
        if not self.is_available():
            return self._metadata(False, "rank-bm25 is not installed", error_type="backend_unavailable")
        try:
            payload = json.loads(target.read_text(encoding="utf-8-sig"))
            self._chunks = list(payload.get("chunks") or [])
            saved_tokens = payload.get("tokenized_corpus") or []
            self._tokenized_corpus = saved_tokens if len(saved_tokens) == len(self._chunks) else [
                tokenize_text(chunk.get("text", "")) for chunk in self._chunks
            ]
            self.index_path = target
            self._rebuild()
            return self._metadata(True)
        except Exception as exc:
            return self._metadata(False, f"BM25 index load failed: {exc}", error_type="index_error")

    def search(self, query: str, top_k: int = 3) -> BM25SearchResult:
        query_tokens = tokenize_query(query)
        metadata = self._metadata(True)
        if not query_tokens:
            return BM25SearchResult(True, metadata={**metadata, "no_hits_reason": "empty_query"})
        if self._index is None:
            loaded = self.load()
            if not loaded.get("success"):
                return BM25SearchResult(False, error_message=loaded.get("error_message"), metadata=loaded)
        if not self._chunks:
            return BM25SearchResult(True, metadata={**metadata, "no_hits_reason": "empty_index"})
        try:
            scores = self._index.get_scores(query_tokens)
            ranked = sorted(enumerate(scores), key=lambda item: float(item[1]), reverse=True)
            limit = max(1, min(int(top_k), 100))
            hits = []
            for index, score in ranked[:limit]:
                chunk = self._chunks[index]
                hits.append(
                    {
                        "source": str(chunk.get("source") or ""),
                        "chunk_id": str(chunk.get("chunk_id") or index),
                        "score": round(float(score), 6),
                        "text": str(chunk.get("text") or ""),
                        "metadata": {
                            **dict(chunk.get("metadata") or {}),
                            "retrieval_mode": "bm25",
                            "tokenizer": TOKENIZER_NAME,
                            "corpus_size": len(self._chunks),
                            "backend": "bm25",
                        },
                    }
                )
            return BM25SearchResult(True, hits=hits, metadata=self._metadata(True))
        except Exception as exc:
            return BM25SearchResult(
                False,
                error_message=f"BM25 search failed: {exc}",
                metadata=self._metadata(False, str(exc), error_type="search_error"),
            )

    def _rebuild(self) -> None:
        from rank_bm25 import BM25Okapi

        self._index = BM25Okapi(self._tokenized_corpus) if self._tokenized_corpus else None

    def _metadata(
        self,
        success: bool,
        error_message: str | None = None,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": success,
            "backend": "bm25",
            "tokenizer": TOKENIZER_NAME,
            "corpus_size": len(self._chunks),
            "index_path": str(self.index_path),
            "error_type": error_type or (None if success else "backend_unavailable"),
            "error_message": error_message,
        }
