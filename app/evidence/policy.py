"""Versioned source policy loading and deterministic reliability scoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DIMENSIONS = (
    "authority",
    "traceability",
    "freshness",
    "relevance",
    "independence",
    "extraction_completeness",
)


@dataclass(frozen=True)
class SourcePolicy:
    version: str
    weights: dict[str, float]
    source_classes: dict[str, dict[str, float]]
    domain_classes: dict[str, str]
    blocked_domains: tuple[str, ...]
    claim_types: dict[str, dict[str, Any]]
    resolution: dict[str, float]


@dataclass(frozen=True)
class ReliabilityBreakdown:
    policy_version: str
    claim_type: str
    source_class: str
    source_cluster_id: str
    authority: float
    traceability: float
    freshness: float
    relevance: float
    independence: float
    extraction_completeness: float
    total_score: float
    rationale: dict[str, Any]

    def dimensions(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in DIMENSIONS}


def load_source_policy(path: str | Path) -> SourcePolicy:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Source policy root must be an object")
    version = str(raw.get("version") or "").strip()
    if not version:
        raise ValueError("Source policy must define a non-empty version")
    weights = {name: float(raw.get("weights", {}).get(name, -1)) for name in DIMENSIONS}
    if any(value < 0 or value > 1 for value in weights.values()):
        raise ValueError("Source policy weights must define every dimension in [0, 1]")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("Source policy weights must sum to 1.0")
    claim_types = raw.get("claim_types")
    if not isinstance(claim_types, dict) or "generic" not in claim_types:
        raise ValueError("Source policy must define a generic claim type")
    if any(not isinstance(profile, dict) for profile in claim_types.values()):
        raise ValueError("Every claim type policy must be an object")
    source_classes = dict(raw.get("source_classes") or {})
    for name, profile in source_classes.items():
        if not isinstance(profile, dict):
            raise ValueError(f"Source class policy must be an object: {name}")
        authority = float(profile.get("authority", -1))
        score_cap = float(profile.get("score_cap", 1.0))
        if authority < 0 or authority > 1 or score_cap < 0 or score_cap > 1:
            raise ValueError(f"Invalid authority or score cap for source class: {name}")
    return SourcePolicy(
        version=version,
        weights=weights,
        source_classes=source_classes,
        domain_classes={str(k).lower(): str(v) for k, v in (raw.get("domain_classes") or {}).items()},
        blocked_domains=tuple(str(item).lower() for item in (raw.get("blocked_domains") or [])),
        claim_types=claim_types,
        resolution={key: float(value) for key, value in (raw.get("resolution") or {}).items()},
    )


def classify_claim(claim_text: str, policy: SourcePolicy) -> str:
    lowered = claim_text.casefold()
    for claim_type, profile in policy.claim_types.items():
        if claim_type == "generic":
            continue
        if any(str(keyword).casefold() in lowered for keyword in profile.get("keywords") or []):
            return claim_type
    return "generic"


def classify_source(
    source_type: str,
    canonical_uri: str,
    metadata: dict[str, Any],
    policy: SourcePolicy,
) -> str:
    if metadata.get("is_mock"):
        return "mock"
    if metadata.get("is_fallback"):
        return "fallback"
    normalized_type = source_type.casefold()
    if normalized_type == "sql":
        return "governed_sql"
    if "github" in normalized_type:
        return "official_code"
    if normalized_type in {"rag", "file", "internal"}:
        return "internal_document"
    if metadata.get("official") is True:
        return "official"
    hostname = (urlsplit(canonical_uri).hostname or "").lower()
    for domain, source_class in policy.domain_classes.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return source_class
    provider = str(metadata.get("provider") or metadata.get("data_source") or "").casefold()
    if "news" in normalized_type or "news" in provider:
        return "news"
    if canonical_uri.startswith(("http://", "https://")):
        return "blog"
    return "unknown"


def source_cluster_id(
    *,
    passage_hash: str,
    canonical_uri: str,
    organization: str | None,
    duplicate_passage_hashes: set[str],
) -> str:
    if passage_hash in duplicate_passage_hashes:
        identity = f"content:{passage_hash}"
    elif organization:
        identity = f"organization:{organization.casefold()}"
    elif canonical_uri:
        identity = f"uri:{canonical_uri}"
    else:
        identity = f"content:{passage_hash}"
    return f"cluster_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def score_reliability(
    *,
    claim_text: str,
    assertion_text: str,
    source_type: str,
    canonical_uri: str,
    organization: str | None,
    source_metadata: dict[str, Any],
    passage_metadata: dict[str, Any],
    locator: dict[str, Any],
    trace_id: str | None,
    snapshot_hash: str | None,
    fetched_at: datetime,
    extraction_confidence: float,
    polarity: str,
    scalar_present: bool,
    source_cluster: str,
    cluster_size: int,
    policy: SourcePolicy,
    now: datetime | None = None,
) -> ReliabilityBreakdown:
    claim_type = classify_claim(claim_text, policy)
    source_class = classify_source(source_type, canonical_uri, source_metadata, policy)
    authority = float(policy.source_classes.get(source_class, {}).get("authority", 0.35))
    profile = policy.claim_types[claim_type]
    preferred = set(profile.get("preferred_source_classes") or [])
    if preferred and source_class not in preferred:
        authority *= 0.9
    hostname = (urlsplit(canonical_uri).hostname or "").lower()
    allowed_domains = tuple(str(item).lower() for item in (profile.get("allowed_domains") or []))
    domain_allowed = not hostname or not any(
        _domain_matches(hostname, domain) for domain in policy.blocked_domains
    )
    if allowed_domains and hostname:
        domain_allowed = domain_allowed and any(
            _domain_matches(hostname, domain) for domain in allowed_domains
        )

    locator_identity = any(locator.get(key) for key in ("url", "document", "query_hash", "repository", "path"))
    traceability = _mean((bool(trace_id), bool(snapshot_hash), locator_identity))
    published_at = _parse_datetime(
        passage_metadata.get("published_at")
        or passage_metadata.get("publishedDate")
        or source_metadata.get("published_at")
    )
    if published_at is None and source_class in {"governed_sql", "internal_document"}:
        published_at = fetched_at
    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    freshness = _freshness(
        published_at,
        int(profile.get("max_age_days") or 730),
        evaluated_at,
    )
    relevance = lexical_relevance(claim_text, assertion_text)
    independence = 1.0 / max(cluster_size, 1)
    extraction_completeness = _mean(
        (
            bool(assertion_text.strip()),
            bool(locator_identity),
            polarity != "unknown",
            scalar_present or extraction_confidence >= 0.5,
        )
    ) * max(0.0, min(1.0, extraction_confidence))
    dimensions = {
        "authority": authority,
        "traceability": traceability,
        "freshness": freshness,
        "relevance": relevance,
        "independence": independence,
        "extraction_completeness": extraction_completeness,
    }
    total = sum(dimensions[name] * policy.weights[name] for name in DIMENSIONS)
    score_cap = float(policy.source_classes.get(source_class, {}).get("score_cap", 1.0))
    total = min(total, score_cap)
    if not domain_allowed:
        total = 0.0
    rounded = {name: round(max(0.0, min(1.0, value)), 6) for name, value in dimensions.items()}
    return ReliabilityBreakdown(
        policy_version=policy.version,
        claim_type=claim_type,
        source_class=source_class,
        source_cluster_id=source_cluster,
        total_score=round(max(0.0, min(1.0, total)), 6),
        rationale={
            "preferred_source": source_class in preferred,
            "domain_allowed": domain_allowed,
            "score_cap": score_cap,
            "cluster_size": cluster_size,
            "published_at": published_at.isoformat() if published_at else None,
            "evaluated_at": evaluated_at.isoformat(),
            "weights": policy.weights,
        },
        **rounded,
    )


def lexical_relevance(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return round(len(left_tokens & right_tokens) / math.sqrt(len(left_tokens) * len(right_tokens)), 6)


def _tokens(text: str) -> set[str]:
    lowered = text.casefold()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {token for token in words if token}


def _freshness(published_at: datetime | None, max_age_days: int, now: datetime) -> float:
    if published_at is None:
        return 0.5
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - published_at).total_seconds() / 86400)
    return max(0.0, min(1.0, 1.0 - age_days / max(max_age_days, 1)))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _mean(values: tuple[bool, ...]) -> float:
    return sum(1.0 if value else 0.0 for value in values) / len(values)


def _domain_matches(hostname: str, domain: str) -> bool:
    return bool(domain) and (hostname == domain or hostname.endswith(f".{domain}"))
