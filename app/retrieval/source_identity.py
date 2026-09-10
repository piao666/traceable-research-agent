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
    normalized_title: str | None
    publisher: str | None
    organization: str | None
    original_publisher: str | None
    syndication_source: str | None
    canonical_story_hash: str
    independence_group: str

    def to_dict(self) -> dict[str, Any]:
        return {
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
    root = original or organization or publisher or host or "unknown"
    group_seed = f"{root}|{story_hash}" if original else root
    group = "srcgrp_" + hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:24]
    return SourceLineage(normalized_title, publisher, organization, original, syndication, story_hash, group)


def canonical_story_hash(content: str) -> str:
    normalized = re.sub(r"\W+", " ", str(content or "").casefold(), flags=re.UNICODE)
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _marker_present(text: str, marker: str) -> bool:
    if len(marker) <= 3 and marker.isascii():
        return bool(re.search(rf"\b{re.escape(marker)}\b", text))
    return marker in text


def _clean(value: Any) -> str | None:
    normalized = " ".join(str(value or "").strip().casefold().split())
    return normalized or None
