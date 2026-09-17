"""Source lineage and syndicated-copy grouping."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit

from app.retrieval.url_normalizer import canonicalize_url


_WIRE_SERVICES = {
    "reuters": ("reuters", "thomson reuters"),
    "associated-press": ("associated press", "ap news", "the ap"),
    "afp": ("agence france-presse", "afp"),
    "xinhua": ("xinhua", "新华社"),
}
SYNDICATION_TITLE_SIMILARITY_THRESHOLD = 0.70
SYNDICATION_BODY_SIMILARITY_THRESHOLD = 0.75
SYNDICATION_DATE_TOLERANCE_DAYS = 2
SYNDICATION_BODY_CHAR_LIMIT = 5000
SYNDICATION_SHINGLE_SIZE = 3


@dataclass(frozen=True)
class SourceLineage:
    resource_identity_kind: str
    resource_identity: str
    normalized_title: str | None
    publisher: str | None
    organization: str | None
    original_publisher: str | None
    syndication_source: str | None
    published_at: str | None
    canonical_story_hash: str
    syndication_story_fingerprint: str | None
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
            "published_at": self.published_at,
            "canonical_story_hash": self.canonical_story_hash,
            "syndication_story_fingerprint": self.syndication_story_fingerprint,
            "possible_original_source": self.original_publisher,
            "syndication_hash": self.canonical_story_hash if self.syndication_source else None,
            "independence_group": self.independence_group,
        }


def source_lineage(
    url: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    *,
    identity_url: str | None = None,
) -> SourceLineage:
    """Build Source-owned lineage independent of backend-specific Views.

    Retrieval backends pass ``identity_url`` explicitly so redirects or
    provider result URLs do not make one requested Resource look independent
    merely because HTTP, Browser, and Remote returned different final URLs.
    """

    meta = dict(metadata or {})
    identity_source = str(identity_url or url or "").strip()
    canonical = canonicalize_url(identity_source).normalized_url
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
    syndication_fingerprint = (
        syndication_story_fingerprint(
            normalized_title,
            content,
            meta.get("published_at"),
        )
        if original or syndication
        else None
    )
    if original or syndication:
        group_seed = (
            f"syndication:{original or syndication}|{syndication_fingerprint or story_hash}"
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
        _clean(meta.get("published_at")),
        story_hash,
        syndication_fingerprint,
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


def syndication_story_fingerprint(
    title: str | None,
    content: str,
    published_at: Any = None,
) -> str:
    """Return a lightweight 64-bit SimHash for one wire-story candidate."""

    title_tokens = _tokens(title)
    body_tokens = _tokens(str(content or "")[:SYNDICATION_BODY_CHAR_LIMIT])
    features = [f"title:{token}" for token in title_tokens]
    features.extend(
        "body:" + "\x1f".join(shingle)
        for shingle in _shingles(body_tokens, SYNDICATION_SHINGLE_SIZE)
    )
    published_day = _published_day(published_at)
    if published_day is not None:
        features.append(f"published:{published_day.isoformat()}")
    if not features:
        return canonical_story_hash(content)[:16]
    weights = [0] * 64
    for feature in features:
        digest = int.from_bytes(
            hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        for bit in range(64):
            weights[bit] += 1 if digest & (1 << bit) else -1
    value = sum(1 << bit for bit, weight in enumerate(weights) if weight >= 0)
    return f"{value:016x}"


def syndication_near_duplicate(
    *,
    first_wire_service: str | None,
    first_title: str | None,
    first_content: str,
    first_published_at: Any = None,
    second_wire_service: str | None,
    second_title: str | None,
    second_content: str,
    second_published_at: Any = None,
) -> bool:
    """Compare only two explicit candidates from the same wire service."""

    first_wire = _clean(first_wire_service)
    second_wire = _clean(second_wire_service)
    if not first_wire or first_wire != second_wire or first_wire not in _WIRE_SERVICES:
        return False
    first_day = _published_day(first_published_at)
    second_day = _published_day(second_published_at)
    if (
        first_day is not None
        and second_day is not None
        and abs((first_day - second_day).days) > SYNDICATION_DATE_TOLERANCE_DAYS
    ):
        return False
    first_title_tokens = _tokens(first_title)
    second_title_tokens = _tokens(second_title)
    title_similarity = max(
        _jaccard(set(first_title_tokens), set(second_title_tokens)),
        SequenceMatcher(None, first_title_tokens, second_title_tokens).ratio(),
    )
    first_body = set(
        _shingles(
            _tokens(str(first_content or "")[:SYNDICATION_BODY_CHAR_LIMIT]),
            SYNDICATION_SHINGLE_SIZE,
        )
    )
    second_body = set(
        _shingles(
            _tokens(str(second_content or "")[:SYNDICATION_BODY_CHAR_LIMIT]),
            SYNDICATION_SHINGLE_SIZE,
        )
    )
    return (
        title_similarity >= SYNDICATION_TITLE_SIMILARITY_THRESHOLD
        and _jaccard(first_body, second_body) >= SYNDICATION_BODY_SIMILARITY_THRESHOLD
    )


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[\w]+", str(value or "").casefold(), flags=re.UNICODE)


def _shingles(tokens: list[str], size: int) -> list[tuple[str, ...]]:
    if not tokens:
        return []
    if len(tokens) < size:
        return [tuple(tokens)]
    return [tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)]


def _jaccard(first: set[Any], second: set[Any]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _published_day(value: Any):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _marker_present(text: str, marker: str) -> bool:
    if len(marker) <= 3 and marker.isascii():
        return bool(re.search(rf"\b{re.escape(marker)}\b", text))
    return marker in text


def _clean(value: Any) -> str | None:
    normalized = " ".join(str(value or "").strip().casefold().split())
    return normalized or None


__all__ = [
    "SYNDICATION_BODY_CHAR_LIMIT",
    "SYNDICATION_BODY_SIMILARITY_THRESHOLD",
    "SYNDICATION_DATE_TOLERANCE_DAYS",
    "SYNDICATION_SHINGLE_SIZE",
    "SYNDICATION_TITLE_SIMILARITY_THRESHOLD",
    "SourceLineage",
    "canonical_story_hash",
    "source_lineage",
    "syndication_near_duplicate",
    "syndication_story_fingerprint",
]
