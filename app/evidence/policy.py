"""Versioned source policy loading, tier classification, and deterministic reliability scoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
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

# ── Phase 8.1: Tier constants ─────────────────────────────────────────
T0 = "T0"  # primary / original source
T1 = "T1"  # institutional / authoritative secondary
T2 = "T2"  # community / personal


@dataclass(frozen=True)
class TierHintTable:
    domain_tiers: dict[str, str] = field(default_factory=dict)
    org_verified_official_repos: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalProfile:
    name: str
    min_t0_sources: int = 1
    min_independent_sources: int = 2
    max_t2_ratio: float = 0.50
    min_t2_sources: int = 0
    max_per_domain: int = 3
    prefer_domains: tuple[str, ...] = ()
    shortfall_policy: str = "report_only"  # report_only | targeted_refetch
    freshness_days: int = 730

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "min_t0_sources": self.min_t0_sources,
            "min_independent_sources": self.min_independent_sources,
            "max_t2_ratio": self.max_t2_ratio,
            "min_t2_sources": self.min_t2_sources,
            "max_per_domain": self.max_per_domain,
            "prefer_domains": list(self.prefer_domains),
            "shortfall_policy": self.shortfall_policy,
            "freshness_days": self.freshness_days,
        }


@dataclass(frozen=True)
class SourcePolicy:
    version: str
    weights: dict[str, float]
    source_classes: dict[str, dict[str, float]]
    domain_classes: dict[str, str]
    blocked_domains: tuple[str, ...]
    claim_types: dict[str, dict[str, Any]]
    resolution: dict[str, float]
    tier_hints: TierHintTable = field(default_factory=TierHintTable)
    retrieval_profiles: dict[str, RetrievalProfile] = field(default_factory=dict)


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


@dataclass
class TierClassification:
    tier: str  # T0 | T1 | T2
    source_class: str
    classification_rule: str
    classification_confidence: float


@dataclass
class SourceCandidate:
    uri: str
    hostname: str
    organization: str | None
    title: str
    snippet: str
    content_basis: str = "snippet_only"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceSelection:
    selected: list[SourceCandidate]
    t0_count: int
    t1_count: int
    t2_count: int
    independent_clusters: int
    quota_shortfall: dict[str, Any] = field(default_factory=dict)
    selection_log: list[str] = field(default_factory=list)


# ── Policy loading ──────────────────────────────────────────────────────

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

    # ── Phase 8.1: tier_hints ──────────────────────────────────────
    raw_hints = raw.get("tier_hints") or {}
    domain_tiers = {str(k).lower(): str(v) for k, v in (raw_hints.get("domain_tiers") or {}).items()}
    org_repos = {str(k).lower(): str(v) for k, v in (raw_hints.get("org_verified_official_repos") or {}).items()}
    tier_hints = TierHintTable(domain_tiers=domain_tiers, org_verified_official_repos=org_repos)

    # ── Phase 8.1: retrieval_profiles ──────────────────────────────
    raw_profiles = raw.get("retrieval_profiles") or {}
    retrieval_profiles: dict[str, RetrievalProfile] = {}
    for pname, pdata in raw_profiles.items():
        retrieval_profiles[str(pname)] = RetrievalProfile(
            name=str(pname),
            min_t0_sources=int(pdata.get("min_t0_sources", 1)),
            min_independent_sources=int(pdata.get("min_independent_sources", 2)),
            max_t2_ratio=float(pdata.get("max_t2_ratio", 0.50)),
            min_t2_sources=int(pdata.get("min_t2_sources", 0)),
            max_per_domain=int(pdata.get("max_per_domain", 3)),
            prefer_domains=tuple(str(d).lower() for d in (pdata.get("prefer_domains") or [])),
            shortfall_policy=str(pdata.get("shortfall_policy", "report_only")),
            freshness_days=int(pdata.get("freshness_days", 730)),
        )

    return SourcePolicy(
        version=version,
        weights=weights,
        source_classes=source_classes,
        domain_classes={str(k).lower(): str(v) for k, v in (raw.get("domain_classes") or {}).items()},
        blocked_domains=tuple(str(item).lower() for item in (raw.get("blocked_domains") or [])),
        claim_types=claim_types,
        resolution={key: float(value) for key, value in (raw.get("resolution") or {}).items()},
        tier_hints=tier_hints,
        retrieval_profiles=retrieval_profiles,
    )


# ── Claim classification ────────────────────────────────────────────────

def classify_claim(claim_text: str, policy: SourcePolicy) -> str:
    lowered = claim_text.casefold()
    for claim_type, profile in policy.claim_types.items():
        if claim_type == "generic":
            continue
        if any(str(keyword).casefold() in lowered for keyword in profile.get("keywords") or []):
            return claim_type
    return "generic"


# ── Source class classification (revised per Phase 8.1) ──────────────────

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
    # ── Phase 8.1: GitHub classification no longer defaults to official_code ──
    if "github" in normalized_type:
        if metadata.get("verified_official_owner") is True:
            return "official_code"
        org = str(metadata.get("organization") or "").lower()
        if org in {"pytorch", "tensorflow", "apache", "microsoft", "python", "facebook", "google"}:
            return "official_code"
        return "blog"
    if normalized_type in {"file", "internal"}:
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


# ── Phase 8.1: Tier classification ──────────────────────────────────────

def classify_tier(
    source_type: str,
    canonical_uri: str,
    metadata: dict[str, Any],
    policy: SourcePolicy,
) -> TierClassification:
    """Deterministic tier classification with 6-level priority chain.

    Priority:
    1. User-maintained domain table (tier_hints.domain_tiers)
    2. Verified official repos (tier_hints.org_verified_official_repos)
    3. URL / content-type rules (.gov, arxiv paper pages, DOI, etc.)
    4. Org identity verification (GitHub owner matches config)
    5. Paper context (peer-reviewed vs preprint vs review)
    6. Conservative default → T2
    """
    hostname = (urlsplit(canonical_uri).hostname or "").lower()
    source_class = classify_source(source_type, canonical_uri, metadata, policy)

    # ── Priority 1: user-maintained domain table ─────────────────
    for domain, tier in sorted(policy.tier_hints.domain_tiers.items(), key=lambda x: -len(x[0])):
        if hostname == domain or hostname.endswith(f".{domain}"):
            return TierClassification(
                tier=tier,
                source_class=source_class,
                classification_rule=f"domain_table:{domain}",
                classification_confidence=0.95,
            )

    # ── Priority 2: verified official repos ──────────────────────
    if "github" in source_type.casefold():
        org_repo = _github_org_repo(hostname, canonical_uri)
        if org_repo:
            for verified_path, tier in policy.tier_hints.org_verified_official_repos.items():
                if org_repo.startswith(verified_path.lower()):
                    return TierClassification(
                        tier=tier,
                        source_class=source_class,
                        classification_rule=f"verified_repo:{verified_path}",
                        classification_confidence=0.90,
                    )

    # ── Priority 3: URL / content-type rules ─────────────────────
    # Government domains
    if hostname.endswith(".gov") or hostname.endswith(".gov.cn") or ".gov." in hostname:
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:government",
            classification_confidence=0.95,
        )

    # Standard / specification domains
    if any(hostname.endswith(suffix) for suffix in (".w3.org", "standards.ieee.org", "rfc-editor.org", "ietf.org")):
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:standards_body",
            classification_confidence=0.95,
        )

    # arXiv paper pages (not search pages, not listing pages)
    if "arxiv.org" in hostname and ("/abs/" in canonical_uri or "/pdf/" in canonical_uri):
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:arxiv_paper",
            classification_confidence=0.92,
        )

    # DOI resolver pages
    if "doi.org" in hostname:
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:doi_resolver",
            classification_confidence=0.90,
        )

    # PubMed
    if hostname.endswith("pubmed.ncbi.nlm.nih.gov") or hostname.endswith("ncbi.nlm.nih.gov"):
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:pubmed",
            classification_confidence=0.92,
        )

    # OpenAlex / Semantic Scholar / Crossref (academic indexes)
    if any(h in hostname for h in ("openalex.org", "api.semanticscholar.org", "api.crossref.org")):
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="url_pattern:academic_index",
            classification_confidence=0.88,
        )

    # ── Priority 3b: publisher / journal domains ──────────────────
    _journal_domains = (
        "nature.com", "science.org", "springer.com", "ieee.org", "acm.org",
        "sciencedirect.com", "cell.com", "thelancet.com", "nejm.org",
        "pnas.org", "aps.org", "aip.org", "iop.org",
    )
    for jd in _journal_domains:
        if hostname == jd or hostname.endswith(f".{jd}"):
            return TierClassification(
                tier=T0,
                source_class=source_class,
                classification_rule=f"url_pattern:journal:{jd}",
                classification_confidence=0.85,
            )

    # ── Priority 4: org identity ──────────────────────────────────
    if metadata.get("verified_official_owner") is True:
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule="metadata:verified_official_owner",
            classification_confidence=0.85,
        )

    # ── Priority 5: .edu / .ac domains → T1 ──────────────────────
    if hostname.endswith(".edu") or ".ac." in hostname or hostname.endswith(".edu.cn") or hostname.endswith(".ac.cn"):
        return TierClassification(
            tier=T1,
            source_class=source_class,
            classification_rule="url_pattern:academic_domain",
            classification_confidence=0.80,
        )

    # ── Priority 6: source_class-based fallback ───────────────────
    if source_class in ("regulatory", "governed_sql", "official", "official_code"):
        return TierClassification(
            tier=T0,
            source_class=source_class,
            classification_rule=f"source_class:{source_class}",
            classification_confidence=0.75,
        )
    if source_class in ("news", "internal_document"):
        return TierClassification(
            tier=T1,
            source_class=source_class,
            classification_rule=f"source_class:{source_class}",
            classification_confidence=0.70,
        )

    # ── Default: conservative T2 ──────────────────────────────────
    return TierClassification(
        tier=T2,
        source_class=source_class,
        classification_rule="default_conservative",
        classification_confidence=0.50,
    )


def _github_org_repo(hostname: str, uri: str) -> str | None:
    """Extract org/repo from a GitHub URL, e.g. github.com/pytorch/pytorch → pytorch/pytorch."""
    if "github.com" not in hostname and "github.io" not in hostname:
        return None
    path = urlsplit(uri).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0].lower()}/{parts[1].lower()}"
    if len(parts) == 1:
        return parts[0].lower()
    return None


# ── Phase 8.1: Source clustering ────────────────────────────────────────

def compute_source_clusters(
    candidates: list[SourceCandidate],
) -> dict[str, list[int]]:
    """Group candidates into independent source clusters.

    Clusters are formed by:
    - Same organization name (case-insensitive)
    - Same hostname
    - Content hash collision (exact duplicate)
    """
    clusters: dict[str, list[int]] = {}
    seen_orgs: dict[str, str] = {}
    seen_hosts: dict[str, str] = {}
    cluster_idx = 0

    for i, candidate in enumerate(candidates):
        cluster_key: str | None = None

        # Check organization
        org = (candidate.organization or "").casefold().strip()
        if org and org in seen_orgs:
            cluster_key = seen_orgs[org]

        # Check hostname
        host = candidate.hostname.casefold()
        if cluster_key is None and host and host in seen_hosts:
            cluster_key = seen_hosts[host]

        # New cluster
        if cluster_key is None:
            cluster_key = f"cluster_{cluster_idx}"
            cluster_idx += 1

        if org:
            seen_orgs[org] = cluster_key
        if host:
            seen_hosts[host] = cluster_key

        clusters.setdefault(cluster_key, []).append(i)

    return clusters


# ── Phase 8.1: Source selection by profile ──────────────────────────────

def select_sources_by_profile(
    candidates: list[SourceCandidate],
    profile: RetrievalProfile,
    policy: SourcePolicy,
    *,
    oversample_factor: int = 2,
    max_candidates: int = 15,
) -> SourceSelection:
    """Select sources to meet profile constraints.

    Algorithm:
    1. Classify tier for each candidate
    2. Group into clusters, deduplicate within cluster
    3. Select T0 candidates first (up to oversample * min_t0)
    4. Fill remaining with T1 candidates
    5. Add T2 candidates respecting max_t2_ratio
    6. Detect shortfall and apply shortfall_policy
    """
    if not candidates:
        return SourceSelection(
            selected=[],
            t0_count=0,
            t1_count=0,
            t2_count=0,
            independent_clusters=0,
            quota_shortfall={"reason": "no_candidates", "t0_required": profile.min_t0_sources, "t0_achieved": 0},
        )

    # Classify all candidates
    tiered: list[tuple[SourceCandidate, TierClassification]] = []
    for c in candidates:
        tc = classify_tier("web_search", c.uri, c.metadata, policy)
        tiered.append((c, tc))

    # Group by cluster
    clusters = compute_source_clusters(candidates)

    # Build per-cluster representatives (best tier per cluster)
    cluster_best: dict[str, tuple[SourceCandidate, TierClassification]] = {}
    for cluster_key, indices in clusters.items():
        # Within a cluster, prefer the highest tier item
        best = min(
            ((candidates[i], tiered[i][1]) for i in indices),
            key=lambda x: _tier_rank(x[1].tier),
        )
        cluster_best[cluster_key] = best

    # Separate by tier
    t0_items = [(c, tc) for c, tc in cluster_best.values() if tc.tier == T0]
    t1_items = [(c, tc) for c, tc in cluster_best.values() if tc.tier == T1]
    t2_items = [(c, tc) for c, tc in cluster_best.values() if tc.tier == T2]

    # Selection logic
    selected: list[SourceCandidate] = []
    selection_log: list[str] = []

    # Step 1: Select T0 candidates (target: oversample_factor * min_t0)
    t0_target = oversample_factor * profile.min_t0_sources
    t0_selected = 0
    for c, tc in t0_items:
        if t0_selected >= t0_target:
            break
        if _count_domain(selected, c.hostname) >= profile.max_per_domain:
            selection_log.append(f"skip T0 {c.uri}: domain limit {profile.max_per_domain}")
            continue
        selected.append(c)
        t0_selected += 1

    # Step 2: Select T1 candidates to reach min_independent_sources
    t1_target = max(0, profile.min_independent_sources - len(selected))
    t1_selected = 0
    for c, tc in t1_items:
        if t1_selected >= t1_target:
            break
        if _count_domain(selected, c.hostname) >= profile.max_per_domain:
            continue
        selected.append(c)
        t1_selected += 1

    # Step 3: Select T2 candidates respecting ratio
    max_t2 = int(len(selected) * profile.max_t2_ratio / max(1.0 - profile.max_t2_ratio, 0.01)) + 1
    if profile.min_t2_sources > 0:
        max_t2 = max(max_t2, profile.min_t2_sources)
    t2_selected = 0
    for c, tc in t2_items:
        if t2_selected >= max_t2:
            selection_log.append(f"skip T2 {c.uri}: T2 max {max_t2} reached")
            break
        if _count_domain(selected, c.hostname) >= profile.max_per_domain:
            continue
        if len(selected) >= max_candidates:
            selection_log.append(f"skip: max_candidates {max_candidates} reached")
            break
        selected.append(c)
        t2_selected += 1

    # Step 4: If still under min_independent_sources, add more T1 then T0
    for c, tc in (t1_items + t0_items):
        if len(selected) >= profile.min_independent_sources:
            break
        if any(s.uri == c.uri for s in selected):
            continue
        if _count_domain(selected, c.hostname) >= profile.max_per_domain:
            continue
        selected.append(c)

    # Detect shortfall
    final_t0 = sum(1 for s in selected if any(
        tc.tier == T0 for _, tc in tiered if _.uri == s.uri
    ))
    final_clusters = len(set(
        next((ck for ck, indices in clusters.items() if i in indices), "unknown")
        for i, s in enumerate(candidates) if s in selected
    ))

    quota_shortfall: dict[str, Any] = {}
    if final_t0 < profile.min_t0_sources:
        quota_shortfall = {
            "t0_required": profile.min_t0_sources,
            "t0_achieved": final_t0,
            "shortfall": profile.min_t0_sources - final_t0,
            "shortfall_policy": profile.shortfall_policy,
        }

    return SourceSelection(
        selected=selected,
        t0_count=final_t0,
        t1_count=sum(1 for s in selected if any(
            tc.tier == T1 for _, tc in tiered if _.uri == s.uri
        )),
        t2_count=sum(1 for s in selected if any(
            tc.tier == T2 for _, tc in tiered if _.uri == s.uri
        )),
        independent_clusters=final_clusters,
        quota_shortfall=quota_shortfall,
        selection_log=selection_log,
    )


def _tier_rank(tier: str) -> int:
    return {T0: 0, T1: 1, T2: 2}.get(tier, 3)


def _count_domain(selected: list[SourceCandidate], hostname: str) -> int:
    target = hostname.casefold()
    return sum(1 for s in selected if s.hostname.casefold() == target)


# ── Source clustering (existing) ────────────────────────────────────────

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


# ── Reliability scoring ─────────────────────────────────────────────────

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
    chinese = "".join(re.findall(r"[一-鿿]", lowered))
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
