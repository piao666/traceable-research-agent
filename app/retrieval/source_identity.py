"""Source lineage and syndicated-copy grouping."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.retrieval.url_normalizer import canonicalize_url


_WIRE_SERVICES = {
    "reuters": ("reuters", "thomson reuters"),
    "associated-press": ("associated press", "ap news", "the ap"),
    "afp": ("agence france-presse", "afp"),
    "xinhua": ("xinhua", "新华社"),
}


@dataclass(frozen=True)
class SourceLineage:
    resource_identity_kind: str
    resource_identity: str
    normalized_title: str | None
    publisher: str | None
    organization: str | None
    original_publisher: str | None
    syndication_source: str | None
    canonical_story_hash: str
    independence_group: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_identity_kind": self.resource_identity_kind,
            "resource_identity": self.resource_identity,
            "normalized_title": self.normalized_title,
            "publisher": self.publisher,
            "organization": self.organization,
            "original_publisher": self.original_publisher,
            "syndication_source": self.syndication_source,
            "canonical_story_hash": self.canonical_story_hash,
            "possible_original_source": self.original_publisher,
            "syndication_hash": self.canonical_story_hash if self.syndication_source else None,
            "independence_group": self.independence_group,
        }


def source_lineage(
    url: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> SourceLineage:
    meta = dict(metadata or {})
    canonical = canonicalize_url(url).normalized_url
    resource_kind, resource_identity = _resource_identity(canonical, meta)
    host = (urlsplit(canonical).hostname or "").casefold()
    publisher = _clean(meta.get("publisher") or meta.get("organization") or host)
    normalized_title = _clean(meta.get("title"))
    organization = _clean(meta.get("organization") or publisher)
    original = _clean(meta.get("original_publisher"))
    syndication = _clean(meta.get("syndication_source"))
    haystack = " ".join((str(meta.get("byline") or ""), str(content or "")[:2500])).casefold()
    if not original:
        for name, markers in _WIRE_SERVICES.items():
            if any(_marker_present(haystack, marker) for marker in markers):
                original = name
                syndication = syndication or name
                break
    story_hash = canonical_story_hash(content)
    syndication_fingerprint = _syndication_fingerprint(
        normalized_title,
        story_hash,
        meta,
    )
    if original or syndication:
        group_seed = (
            f"syndication:{original or syndication}|{syndication_fingerprint}"
        )
    else:
        group_seed = f"resource:{resource_kind}|{resource_identity}"
    group = "srcgrp_" + hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:24]
    return SourceLineage(
        resource_kind,
        resource_identity,
        normalized_title,
        publisher,
        organization,
        original,
        syndication,
        story_hash,
        group,
    )


def canonical_story_hash(content: str) -> str:
    normalized = re.sub(r"\W+", " ", str(content or "").casefold(), flags=re.UNICODE)
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _resource_identity(canonical_url: str, metadata: dict[str, Any]) -> tuple[str, str]:
    identifiers = metadata.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, dict) else {}
    candidates = (
        ("doi", metadata.get("doi") or metadata.get("DOI") or identifiers.get("doi")),
        ("arxiv_id", metadata.get("arxiv_id") or identifiers.get("arxiv_id")),
        ("pmid", metadata.get("pmid") or identifiers.get("pmid")),
    )
    for kind, value in candidates:
        normalized = _normalize_identifier(kind, value)
        if normalized:
            return kind, normalized
    if canonical_url.startswith("file://"):
        return "local_file", canonical_url
    return "canonical_url", canonical_url


def _normalize_identifier(kind: str, value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return ""
    if kind == "doi":
        normalized = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", normalized)
        normalized = normalized.removeprefix("doi:").strip()
    elif kind == "arxiv_id":
        normalized = re.sub(r"^(?:https?://)?arxiv\.org/(?:abs|pdf)/", "", normalized)
        normalized = normalized.removesuffix(".pdf")
    elif kind == "pmid":
        normalized = normalized.removeprefix("pmid:").strip()
    return normalized


def _syndication_fingerprint(
    normalized_title: str | None,
    story_hash: str,
    metadata: dict[str, Any],
) -> str:
    """Return a bounded fingerprint only for explicit syndication candidates."""

    title = re.sub(r"\W+", " ", str(normalized_title or ""), flags=re.UNICODE)
    title = " ".join(title.split())
    published = str(metadata.get("published_at") or "")[:10]
    if len(title) >= 12:
        seed = f"{title}|{published}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return story_hash


def _marker_present(text: str, marker: str) -> bool:
    if len(marker) <= 3 and marker.isascii():
        return bool(re.search(rf"\b{re.escape(marker)}\b", text))
    return marker in text


def _clean(value: Any) -> str | None:
    normalized = " ".join(str(value or "").strip().casefold().split())
    return normalized or None
