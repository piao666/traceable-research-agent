"""Fetch cache with TTL / ETag / content-hash-based deduplication.

Phase 8.2: Caches fetched page content locally with configurable TTL.
Cache hits produce trace events. Cache does not bypass freshness scoring.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any


_CACHE_LOCK = RLock()
FETCH_CACHE_SCHEMA_VERSION = "fetch-cache-v2"
FETCH_CACHE_MAX_SOURCE_CHARS = 500_000


@dataclass
class FetchCacheEntry:
    cache_key: str
    canonical_url: str
    final_url: str
    source_content: str
    source_content_length: int
    source_content_hash: str
    source_truncated_at_cache_limit: bool
    content_basis: str
    content_type: str
    cached_at_epoch: float
    ttl_seconds: int
    etag: str | None = None
    extraction_method: str = "unknown"
    extraction_confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = FETCH_CACHE_SCHEMA_VERSION
    legacy_unknown_completeness: bool = False

    @property
    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.cached_at_epoch) > self.ttl_seconds

    @property
    def age_seconds(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, now - self.cached_at_epoch)

    @property
    def url(self) -> str:
        """Compatibility alias for callers written against fetch-cache-v1."""
        return self.canonical_url

    @property
    def content(self) -> str:
        """Compatibility alias; v2 stores the source extraction."""
        return self.source_content

    @property
    def content_hash(self) -> str:
        """Compatibility alias; v2 hashes the cached source extraction."""
        return self.source_content_hash

    @property
    def fetched_at(self) -> float:
        """Compatibility alias for the epoch cache timestamp."""
        return self.cached_at_epoch

    @fetched_at.setter
    def fetched_at(self, value: float) -> None:
        self.cached_at_epoch = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "cache_key": self.cache_key,
            "canonical_url": self.canonical_url,
            "final_url": self.final_url,
            "source_content": self.source_content,
            "source_content_length": self.source_content_length,
            "source_content_hash": self.source_content_hash,
            "source_truncated_at_cache_limit": self.source_truncated_at_cache_limit,
            "content_basis": self.content_basis,
            "content_type": self.content_type,
            "cached_at_epoch": self.cached_at_epoch,
            "ttl_seconds": self.ttl_seconds,
            "etag": self.etag,
            "extraction_method": self.extraction_method,
            "extraction_confidence": self.extraction_confidence,
            "legacy_unknown_completeness": self.legacy_unknown_completeness,
            "metadata": self.metadata,
        }


class FetchCache:
    """TTL / ETag / hash-based fetch cache backed by local JSON + content files."""

    def __init__(self, cache_dir: str, default_ttl: int = 3600):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self._index_path = self.cache_dir / "_index.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self._index_path.is_file():
            return {}
        try:
            loaded = json.loads(self._index_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _save_index(self) -> bool:
        try:
            temp_path = self._index_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(self._index, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            temp_path.replace(self._index_path)
            return True
        except Exception:
            return False

    @staticmethod
    def _compute_key(url: str, params: dict[str, Any] | None = None) -> str:
        normalized = url.strip().lower()
        if params:
            sorted_params = json.dumps(params, sort_keys=True, ensure_ascii=False)
            normalized += "|" + sorted_params
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _content_file(cache_dir: Path, content_hash: str) -> Path:
        return cache_dir / f"{content_hash}.txt"

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> FetchCacheEntry | None:
        """Retrieve a fresh cached entry. Returns None for every non-hit state."""
        entry, status = self.lookup(url, params=params, now=now)
        return entry if status == "hit" else None

    def lookup(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> tuple[FetchCacheEntry | None, str]:
        """Return an entry and one of hit/miss/expired/corrupt."""

        now = time.time() if now is None else now
        cache_key = self._compute_key(url, params)

        with _CACHE_LOCK:
            self._index = self._load_index()
            if cache_key not in self._index:
                return None, "miss"

            entry_data = self._index[cache_key]
            if not isinstance(entry_data, dict):
                self._index.pop(cache_key, None)
                self._save_index()
                return None, "corrupt"
            is_v2 = (
                entry_data.get("schema_version") == FETCH_CACHE_SCHEMA_VERSION
                and "source_content_length" in entry_data
            )
            content_hash = str(
                entry_data.get("source_content_hash" if is_v2 else "content_hash") or ""
            )
            content_file = self._content_file(self.cache_dir, content_hash)

            if not content_hash or not content_file.is_file():
                self._index.pop(cache_key, None)
                self._save_index()
                return None, "corrupt"

            try:
                content = content_file.read_text(encoding="utf-8")
                if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
                    raise ValueError("content hash mismatch")
                entry = FetchCacheEntry(
                    cache_key=cache_key,
                    canonical_url=str(
                        entry_data.get("canonical_url") or entry_data.get("url") or url
                    ),
                    final_url=str(
                        entry_data.get("final_url")
                        or (entry_data.get("metadata") or {}).get("final_url")
                        or entry_data.get("url")
                        or url
                    ),
                    source_content=content,
                    source_content_length=(
                        int(entry_data.get("source_content_length", len(content)))
                        if is_v2
                        else len(content)
                    ),
                    source_content_hash=content_hash,
                    source_truncated_at_cache_limit=(
                        bool(entry_data.get("source_truncated_at_cache_limit"))
                        if is_v2
                        else False
                    ),
                    content_basis=(
                        str(entry_data.get("content_basis") or "partial")
                        if is_v2
                        else "partial"
                    ),
                    content_type=str(entry_data.get("content_type") or "text/html"),
                    cached_at_epoch=float(
                        entry_data.get("cached_at_epoch" if is_v2 else "fetched_at", 0)
                    ),
                    ttl_seconds=int(entry_data.get("ttl_seconds", self.default_ttl)),
                    etag=entry_data.get("etag"),
                    extraction_method=str(entry_data.get("extraction_method") or "unknown"),
                    extraction_confidence=float(entry_data.get("extraction_confidence", 0)),
                    metadata=(
                        dict(entry_data.get("metadata"))
                        if isinstance(entry_data.get("metadata"), dict)
                        else {}
                    ),
                    schema_version=(
                        FETCH_CACHE_SCHEMA_VERSION if is_v2 else "fetch-cache-v1"
                    ),
                    legacy_unknown_completeness=not is_v2,
                )
                if is_v2:
                    entry.legacy_unknown_completeness = bool(
                        entry_data.get("legacy_unknown_completeness", False)
                    )
                if entry.source_content_length != len(content):
                    raise ValueError("source content length mismatch")
            except Exception:
                self._index.pop(cache_key, None)
                self._save_index()
                return None, "corrupt"

            if (now - entry.cached_at_epoch) > entry.ttl_seconds:
                return entry, "expired"
            return entry, "hit"

    def put(self, entry: FetchCacheEntry) -> bool:
        """Store an entry in the cache."""
        if len(entry.source_content) > FETCH_CACHE_MAX_SOURCE_CHARS:
            return False
        if entry.source_content_length != len(entry.source_content):
            return False
        expected_hash = hashlib.sha256(entry.source_content.encode("utf-8")).hexdigest()
        if entry.source_content_hash != expected_hash:
            return False
        content_file = self._content_file(self.cache_dir, entry.source_content_hash)
        with _CACHE_LOCK:
            self._index = self._load_index()
            try:
                temp_path = content_file.with_suffix(".tmp")
                temp_path.write_text(entry.source_content, encoding="utf-8")
                temp_path.replace(content_file)
            except Exception:
                return False  # Don't update index if write fails

            self._index[entry.cache_key] = {
                "schema_version": FETCH_CACHE_SCHEMA_VERSION,
                "canonical_url": entry.canonical_url,
                "final_url": entry.final_url,
                "source_content_length": entry.source_content_length,
                "source_content_hash": entry.source_content_hash,
                "source_truncated_at_cache_limit": entry.source_truncated_at_cache_limit,
                "content_basis": entry.content_basis,
                "legacy_unknown_completeness": entry.legacy_unknown_completeness,
                "content_type": entry.content_type,
                "cached_at_epoch": entry.cached_at_epoch,
                "ttl_seconds": entry.ttl_seconds,
                "etag": entry.etag,
                "extraction_method": entry.extraction_method,
                "extraction_confidence": entry.extraction_confidence,
                "metadata": entry.metadata,
            }
            return self._save_index()

    def invalidate(self, url: str, params: dict[str, Any] | None = None) -> bool:
        """Remove a specific entry from the cache index. Returns True if removed."""
        cache_key = self._compute_key(url, params)
        with _CACHE_LOCK:
            self._index = self._load_index()
            if cache_key in self._index:
                self._index.pop(cache_key)
                return self._save_index()
        return False

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with _CACHE_LOCK:
            self._index = self._load_index()
            total = len(self._index)
            expired = 0
            now = time.time()
            for entry_data in self._index.values():
                fetched_at = float(
                    entry_data.get("cached_at_epoch", entry_data.get("fetched_at", 0))
                )
                ttl = int(entry_data.get("ttl_seconds", self.default_ttl))
                if (now - fetched_at) > ttl:
                    expired += 1
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "cache_dir": str(self.cache_dir),
        }
