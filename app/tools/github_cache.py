"""Small JSON cache for read-only GitHub search results."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any


_CACHE_LOCK = RLock()


def get_cache_key(query: str, repo: str | None, limit: int, mode: str, endpoint: str) -> str:
    """Return a stable key without exposing query text in the cache filename."""

    payload = json.dumps(
        {
            "query": query,
            "repo": repo,
            "limit": limit,
            "mode": mode,
            "endpoint": endpoint,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(path: Path) -> tuple[dict[str, Any], str | None]:
    """Load cache data; malformed or unreadable files become an empty cache."""

    with _CACHE_LOCK:
        if not path.exists():
            return {"version": 1, "entries": {}}, None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {"version": 1, "entries": {}}, f"cache_read_error:{type(exc).__name__}"
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
            return {"version": 1, "entries": {}}, "cache_read_error:invalid_format"
        return payload, None


def save_cache(path: Path, payload: dict[str, Any]) -> str | None:
    """Save cache data and return a non-fatal error marker."""

    with _CACHE_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return None
        except (OSError, TypeError, ValueError) as exc:
            return f"cache_write_error:{type(exc).__name__}"


def get_cached_result(
    path: Path,
    key: str,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return an unexpired cache entry, ignoring damaged entry data."""

    with _CACHE_LOCK:
        payload, error = load_cache(path)
        if error:
            return None, error
        entry = payload["entries"].get(key)
        if not isinstance(entry, dict):
            return None, None
        try:
            expires_at = datetime.fromisoformat(str(entry["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return None, "cache_read_error:invalid_entry"
        current = now or datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= current:
            return None, None
        if not isinstance(entry.get("results"), list):
            return None, "cache_read_error:invalid_entry"
        return entry, None


def put_cached_result(
    path: Path,
    key: str,
    results: list[dict[str, Any]],
    metadata: dict[str, Any],
    ttl_seconds: int,
    *,
    now: datetime | None = None,
) -> str | None:
    """Store results and non-secret metadata with an explicit expiry."""

    with _CACHE_LOCK:
        payload, _ = load_cache(path)
        current = now or datetime.now(timezone.utc)
        expires_at = current + timedelta(seconds=max(0, ttl_seconds))
        payload["entries"][key] = {
            "created_at": current.isoformat(),
            "expires_at": expires_at.isoformat(),
            "results": results,
            "metadata": metadata,
        }
        return save_cache(path, payload)
