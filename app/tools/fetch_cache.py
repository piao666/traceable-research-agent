"""Fetch cache with TTL / ETag / content-hash-based deduplication.

Phase 8.2: Caches fetched page content locally with configurable TTL.
Cache hits produce trace events. Cache does not bypass freshness scoring.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FetchCacheEntry:
    cache_key: str
    url: str
    content_hash: str  # SHA-256 of content
    content: str
    content_type: str
    fetched_at: float  # Unix timestamp
    ttl_seconds: int
    etag: str | None = None
    extraction_method: str = "unknown"
    extraction_confidence: float = 0.0

    @property
    def is_expired(self, now: float | None = None) -> bool:
        now = now or time.time()
        return (now - self.fetched_at) > self.ttl_seconds

    @property
    def age_seconds(self, now: float | None = None) -> float:
        now = now or time.time()
        return max(0.0, now - self.fetched_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "url": self.url,
            "content_hash": self.content_hash,
            "content_length": len(self.content),
            "content_type": self.content_type,
            "fetched_at": self.fetched_at,
            "ttl_seconds": self.ttl_seconds,
            "etag": self.etag,
            "extraction_method": self.extraction_method,
            "extraction_confidence": self.extraction_confidence,
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
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass

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
        """Retrieve a cached entry. Returns None if missing or expired."""
        now = now or time.time()
        cache_key = self._compute_key(url, params)

        if cache_key not in self._index:
            return None

        entry_data = self._index[cache_key]
        content_hash = entry_data.get("content_hash", "")
        content_file = self._content_file(self.cache_dir, content_hash)

        if not content_file.is_file():
            # Stale index entry
            self._index.pop(cache_key, None)
            self._save_index()
            return None

        entry = FetchCacheEntry(
            cache_key=cache_key,
            url=entry_data.get("url", url),
            content_hash=content_hash,
            content="",  # loaded below
            content_type=entry_data.get("content_type", "text/html"),
            fetched_at=float(entry_data.get("fetched_at", 0)),
            ttl_seconds=int(entry_data.get("ttl_seconds", self.default_ttl)),
            etag=entry_data.get("etag"),
            extraction_method=entry_data.get("extraction_method", "unknown"),
            extraction_confidence=float(entry_data.get("extraction_confidence", 0)),
        )

        if entry.is_expired(now):
            # Expired: remove from index but keep file for reference
            self._index.pop(cache_key, None)
            self._save_index()
            return None

        try:
            entry.content = content_file.read_text(encoding="utf-8")
        except Exception:
            self._index.pop(cache_key, None)
            self._save_index()
            return None

        return entry

    def put(self, entry: FetchCacheEntry) -> None:
        """Store an entry in the cache."""
        content_file = self._content_file(self.cache_dir, entry.content_hash)
        try:
            content_file.write_text(entry.content, encoding="utf-8")
        except Exception:
            return  # Don't update index if write fails

        self._index[entry.cache_key] = {
            "url": entry.url,
            "content_hash": entry.content_hash,
            "content_type": entry.content_type,
            "fetched_at": entry.fetched_at,
            "ttl_seconds": entry.ttl_seconds,
            "etag": entry.etag,
            "extraction_method": entry.extraction_method,
            "extraction_confidence": entry.extraction_confidence,
        }
        self._save_index()

    def invalidate(self, url: str, params: dict[str, Any] | None = None) -> bool:
        """Remove a specific entry from the cache index. Returns True if removed."""
        cache_key = self._compute_key(url, params)
        if cache_key in self._index:
            self._index.pop(cache_key)
            self._save_index()
            return True
        return False

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = len(self._index)
        expired = 0
        now = time.time()
        for entry_data in self._index.values():
            fetched_at = float(entry_data.get("fetched_at", 0))
            ttl = int(entry_data.get("ttl_seconds", self.default_ttl))
            if (now - fetched_at) > ttl:
                expired += 1
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "cache_dir": str(self.cache_dir),
        }
