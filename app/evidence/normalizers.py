"""Source-specific identity and locator normalization for V2 evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.agent.evidence import EvidenceItem


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def canonical_source_uri(item: EvidenceItem) -> str:
    source_ref = str(item.source_ref or "").strip()
    if source_ref.startswith(("http://", "https://")):
        return canonicalize_url(source_ref)
    if item.source_type == "sql":
        return f"sql://{item.run_id}/{item.trace_id or item.evidence_id}"
    if item.source_type == "rag":
        return f"rag://{_safe_component(source_ref or item.evidence_id)}"
    if item.source_type == "file":
        return f"file://{source_ref.replace(chr(92), '/')}"
    if "github" in item.source_type:
        return f"github://{_safe_component(source_ref or item.evidence_id)}"
    return f"evidence://{item.run_id}/{item.evidence_id}"


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query_items)), ""))


def source_provider(item: EvidenceItem) -> str:
    metadata = item.metadata or {}
    return str(
        metadata.get("remote_server")
        or metadata.get("data_source")
        or item.tool_name
        or "unknown"
    )[:128]


def source_organization(item: EvidenceItem, canonical_uri: str) -> str | None:
    if canonical_uri.startswith(("http://", "https://")):
        return (urlsplit(canonical_uri).hostname or "")[:255] or None
    if canonical_uri.startswith("github://"):
        value = canonical_uri.removeprefix("github://")
        return value.split("/", 1)[0][:255] or None
    return None


def passage_locator(item: EvidenceItem, trace_input: dict[str, Any]) -> dict[str, Any]:
    metadata = item.metadata or {}
    source_ref = str(item.source_ref or "")
    base: dict[str, Any] = {
        "source_type": item.source_type,
        "evidence_id": item.evidence_id,
        "trace_id": item.trace_id,
        "step_no": item.step_no,
    }
    if item.source_type == "rag":
        hit_metadata = metadata.get("hit_metadata") if isinstance(metadata.get("hit_metadata"), dict) else {}
        base.update(
            {
                "kind": "rag",
                "document": source_ref or None,
                "chunk_id": metadata.get("chunk_id"),
                "start": hit_metadata.get("start"),
                "end": hit_metadata.get("end"),
            }
        )
        return base
    if item.source_type == "sql":
        query = str(trace_input.get("query") or "")
        base.update(
            {
                "kind": "sql",
                "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest() if query else None,
                "database": trace_input.get("database") or trace_input.get("db_path") or "sqlite",
                "table": metadata.get("table"),
                "columns": metadata.get("columns"),
                "row_key": metadata.get("row_key"),
            }
        )
        return base
    if "github" in item.source_type or "github.com" in source_ref:
        parsed = urlsplit(source_ref) if source_ref.startswith("http") else None
        parts = [part for part in (parsed.path.split("/") if parsed else source_ref.split("/")) if part]
        repo = "/".join(parts[:2]) if len(parts) >= 2 else source_ref
        base.update(
            {
                "kind": "github",
                "repository": repo or None,
                "commit": metadata.get("commit") or metadata.get("sha"),
                "path": metadata.get("path"),
                "line_start": metadata.get("line_start"),
                "line_end": metadata.get("line_end"),
                "url": canonicalize_url(source_ref) if source_ref.startswith("http") else None,
            }
        )
        return base
    if source_ref.startswith(("http://", "https://")):
        base.update({"kind": "web", "url": canonicalize_url(source_ref)})
        return base
    if item.source_type == "file":
        base.update({"kind": "file", "path": source_ref})
        return base
    base.update({"kind": "generic", "source_ref": source_ref or None})
    return base


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "_", value.strip()).strip("/") or "unknown"
