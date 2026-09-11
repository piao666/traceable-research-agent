"""Cross-run claim grouping, source independence, and conflict resolution."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence.models import (
    ScopeClaimGroup,
    ScopeClaimMember,
    ScopeClaimResolution,
    ScopeReasoningRun,
)
from app.evidence.policy import load_source_policy, score_reliability
from app.evidence.reasoning import (
    ScoredRelation,
    classify_relation,
    normalize_fact,
    resolve_conflict,
)
from app.research.models import ResearchScope


SCOPE_REASONING_ENGINE_VERSION = "scope-reasoning-v1"
_CITATION_RE = re.compile(r"\bCIT-\d{3}-\d{2}\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}]+", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?<!\d)(?:Q[1-4]\s*)?(?:19|20)\d{2}"
    r"(?:\s*Q[1-4]|[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?(?!\d)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[$¥￥€£]\s*)?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?(?:\s*%|\s*(?:USD|CNY|RMB))?",
    re.IGNORECASE,
)
_PUNCTUATION_RE = re.compile(r"[^\w\s<>]+", re.UNICODE)


def scope_claim_group_key(claim: dict) -> str:
    """Normalize a claim using the fixed R12.1 grouping algorithm."""

    text = unicodedata.normalize("NFKC", _text(claim.get("claim_text"))).lower()
    text = _CITATION_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _DATE_RE.sub(" <DATE> ", text)
    text = _NUMBER_RE.sub(" <NUM> ", text)
    text = " ".join(text.split())
    text = _PUNCTUATION_RE.sub(" ", text)
    text = " ".join(text.split())
    unit_family = normalize_fact(
        _text(claim.get("claim_text")),
        unit=_text(claim.get("unit")) or None,
    ).unit
    return f"{text}|unit:{_text(unit_family).casefold() or '<none>'}"


def scope_source_cluster_id(
    document: dict,
    passage: dict,
) -> str:
    """Return a stable source cluster using fixed lineage precedence."""

    metadata = _mapping(document.get("metadata"))
    source_identity = _mapping(metadata.get("source_identity"))
    ordered_candidates = (
        ("independence_group", source_identity.get("independence_group")),
        ("canonical_story_hash", source_identity.get("canonical_story_hash")),
        ("organization", document.get("organization")),
        (
            "hostname",
            metadata.get("hostname")
            or (urlsplit(_text(document.get("canonical_uri"))).hostname or ""),
        ),
        ("canonical_uri", document.get("canonical_uri")),
        ("passage_hash", passage.get("content_hash")),
    )
    kind, value = next(
        ((kind, _text(value).casefold()) for kind, value in ordered_candidates if _text(value)),
        ("passage_hash", ""),
    )
    seed = f"{kind}:{value}"
    return f"scope_cluster_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def materialize_scope_reasoning(
    db: Session,
    scope_id: str,
    policy_path: str | Path,
) -> dict:
    """Recompute and persist reasoning across the complete raw Scope graph."""

    from app.evidence.scope_service import get_scope_provenance_bundle

    scope = db.get(ResearchScope, scope_id)
    if scope is None:
        raise ValueError("Research scope not found")
    path = Path(policy_path)
    policy_bytes = path.read_bytes()
    policy = load_source_policy(path)
    policy_hash = hashlib.sha256(
        policy_bytes + b"\0" + SCOPE_REASONING_ENGINE_VERSION.encode("utf-8")
    ).hexdigest()
    bundle = get_scope_provenance_bundle(db, scope)
    fingerprint = _evidence_fingerprint(bundle)
    reasoning_run_id = _stable_id(
        "scope_reason",
        scope_id,
        policy.version,
        policy_hash,
        fingerprint,
        SCOPE_REASONING_ENGINE_VERSION,
    )
    existing = db.get(ScopeReasoningRun, reasoning_run_id)
    if existing is not None and existing.status == "complete":
        return get_scope_reasoning_bundle(
            db, scope_id, reasoning_run_id=reasoning_run_id
        )

    claims = list(bundle.get("claims") or [])
    claim_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        claim_groups[scope_claim_group_key(claim)].append(claim)

    edges_by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in bundle.get("edges") or []:
        edges_by_claim[_text(edge.get("claim_id"))].append(edge)
    assertions = _index(bundle.get("assertions"), "assertion_id")
    passages = _index(bundle.get("passages"), "passage_id")
    snapshots = _index(bundle.get("source_snapshots"), "snapshot_id")
    documents = _index(bundle.get("source_documents"), "document_id")
    source_alias_by_member = _alias_index(
        _mapping(bundle.get("scope_identity")).get("source_aliases"),
        "member_document_ids",
    )
    passage_alias_by_member = _alias_index(
        _mapping(bundle.get("scope_identity")).get("passage_aliases"),
        "member_passage_ids",
    )

    relation_inputs: dict[str, list[dict[str, Any]]] = {}
    all_cluster_ids: list[str] = []
    for normalized_key, group_claims in claim_groups.items():
        representative = group_claims[0]
        inputs = _relation_inputs(
            group_claims,
            edges_by_claim,
            assertions,
            passages,
            snapshots,
            documents,
            source_alias_by_member,
            passage_alias_by_member,
        )
        relation_inputs[normalized_key] = inputs
        all_cluster_ids.extend(_text(item.get("source_cluster_id")) for item in inputs)
    cluster_sizes = Counter(all_cluster_ids)
    evaluation_time = max(
        (_datetime(item.get("fetched_at")) for item in snapshots.values()),
        default=datetime.now(timezone.utc),
    )

    reasoning_run = existing or ScopeReasoningRun(
        reasoning_run_id=reasoning_run_id,
        scope_id=scope_id,
        policy_version=policy.version,
        policy_hash=policy_hash,
        evidence_fingerprint=fingerprint,
        engine_version=SCOPE_REASONING_ENGINE_VERSION,
        status="building",
    )
    try:
        reasoning_run.status = "building"
        reasoning_run.updated_at = datetime.now(timezone.utc)
        db.add(reasoning_run)
        db.flush()
        if existing is not None:
            _clear_incomplete_materialization(db, reasoning_run_id)

        for normalized_key, group_claims in sorted(claim_groups.items()):
            representative = group_claims[0]
            normalized_claim = normalize_fact(
                _text(representative.get("claim_text")),
                value=_scalar(representative.get("value")),
                unit=_text(representative.get("unit")) or None,
                time_scope=_text(representative.get("time_scope")) or None,
            )
            group_id = _stable_id("scope_group", reasoning_run_id, normalized_key)
            db.add(
                ScopeClaimGroup(
                    group_id=group_id,
                    reasoning_run_id=reasoning_run_id,
                    normalized_key=normalized_key,
                    representative_claim_text=_text(representative.get("claim_text")),
                    unit=normalized_claim.unit,
                    time_scope=normalized_claim.time_scope,
                )
            )
            for claim in group_claims:
                db.add(
                    ScopeClaimMember(
                        member_id=_stable_id(
                            "scope_member",
                            group_id,
                            _text(claim.get("origin_run_id")),
                            _text(claim.get("claim_id")),
                        ),
                        group_id=group_id,
                        origin_run_id=_text(claim.get("origin_run_id")),
                        claim_id=_text(claim.get("claim_id")),
                    )
                )

            scored: list[ScoredRelation] = []
            relation_audit: list[dict[str, Any]] = []
            for item in relation_inputs.get(normalized_key, []):
                breakdown = score_reliability(
                    claim_text=_text(representative.get("claim_text")),
                    assertion_text=_text(item["assertion"].get("object_text")),
                    source_type=_text(item["document"].get("source_type")),
                    canonical_uri=_text(item["document"].get("canonical_uri")),
                    organization=_text(item["document"].get("organization")) or None,
                    source_metadata={
                        **_mapping(item["document"].get("metadata")),
                        "provider": item["document"].get("provider"),
                    },
                    passage_metadata=_mapping(item["passage"].get("metadata")),
                    locator=_mapping(item["passage"].get("locator")),
                    trace_id=_text(item["assertion"].get("origin_trace_id")) or None,
                    snapshot_hash=_text(item["snapshot"].get("content_hash")) or None,
                    fetched_at=_datetime(item["snapshot"].get("fetched_at")),
                    extraction_confidence=_float(
                        item["assertion"].get("extraction_confidence")
                    ),
                    polarity=_text(item["assertion"].get("polarity")) or "unknown",
                    scalar_present=_scalar(item["assertion"].get("value")) is not None,
                    source_cluster=item["source_cluster_id"],
                    cluster_size=cluster_sizes[item["source_cluster_id"]],
                    policy=policy,
                    now=evaluation_time,
                )
                decision = classify_relation(
                    normalized_claim,
                    normalize_fact(
                        _text(item["assertion"].get("object_text")),
                        value=_scalar(item["assertion"].get("value")),
                        unit=_text(item["assertion"].get("unit")) or None,
                        time_scope=_text(item["assertion"].get("time_scope")) or None,
                        polarity=_text(item["assertion"].get("polarity")) or None,
                    ),
                    prior_relation=_text(item["edge"].get("relation")) or "supports",
                )
                passage_metadata = _mapping(item["passage"].get("metadata"))
                scored.append(
                    ScoredRelation(
                        relation=decision.relation,
                        score=breakdown.total_score,
                        source_cluster_id=item["source_cluster_id"],
                        source_class=breakdown.source_class,
                        time_scope=_text(item["assertion"].get("time_scope")) or None,
                        scope_difference=decision.scope_difference,
                        is_correction=bool(
                            passage_metadata.get("is_correction")
                            or passage_metadata.get("correction")
                            or passage_metadata.get("supersedes")
                        ),
                    )
                )
                relation_audit.append(
                    {
                        "edge_id": item["edge"].get("edge_id"),
                        "claim_id": item["claim"].get("claim_id"),
                        "origin_run_id": item["claim"].get("origin_run_id"),
                        "assertion_id": item["assertion"].get("assertion_id"),
                        "passage_id": item["passage"].get("passage_id"),
                        "source_cluster_id": item["source_cluster_id"],
                        "source_alias_identity_key": _mapping(
                            item.get("source_alias")
                        ).get("identity_key"),
                        "representative_document_id": _mapping(
                            item.get("source_alias")
                        ).get("representative_document_id"),
                        "representative_passage_id": _mapping(
                            item.get("passage_alias")
                        ).get("representative_passage_id"),
                        "relation": decision.relation,
                        "score": breakdown.total_score,
                        "source_class": breakdown.source_class,
                        "evidence_role": _mapping(
                            item["document"].get("metadata")
                        ).get("evidence_role", "unknown"),
                        "scope_difference": decision.scope_difference,
                        "relation_rationale": decision.rationale,
                        "reliability": {
                            "dimensions": breakdown.dimensions(),
                            "rationale": breakdown.rationale,
                        },
                    }
                )
            resolution = resolve_conflict(scored, policy)
            db.add(
                ScopeClaimResolution(
                    resolution_id=_stable_id("scope_resolution", group_id),
                    group_id=group_id,
                    status=resolution.status,
                    confidence=resolution.confidence,
                    support_quality=resolution.support_quality,
                    refute_quality=resolution.refute_quality,
                    independent_support_count=resolution.independent_support_count,
                    independent_refute_count=resolution.independent_refute_count,
                    rationale_json=_json_dump(
                        {
                            **resolution.rationale,
                            "relations": relation_audit,
                            "engine_version": SCOPE_REASONING_ENGINE_VERSION,
                        }
                    ),
                )
            )
        reasoning_run.status = "complete"
        reasoning_run.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_scope_reasoning_bundle(
        db, scope_id, reasoning_run_id=reasoning_run_id
    )


def get_scope_reasoning_bundle(
    db: Session,
    scope_id: str,
    *,
    reasoning_run_id: str | None = None,
) -> dict[str, Any]:
    if reasoning_run_id:
        reasoning_run = db.get(ScopeReasoningRun, reasoning_run_id)
    else:
        reasoning_run = db.scalars(
            select(ScopeReasoningRun)
            .where(ScopeReasoningRun.scope_id == scope_id)
            .order_by(ScopeReasoningRun.created_at.desc(), ScopeReasoningRun.reasoning_run_id)
            .limit(1)
        ).first()
    if reasoning_run is None:
        return {
            "reasoning": None,
            "scope_claim_groups": [],
            "scope_resolutions": [],
        }
    groups = list(
        db.scalars(
            select(ScopeClaimGroup)
            .where(ScopeClaimGroup.reasoning_run_id == reasoning_run.reasoning_run_id)
            .order_by(ScopeClaimGroup.normalized_key, ScopeClaimGroup.group_id)
        )
    )
    group_ids = [group.group_id for group in groups]
    members = (
        list(
            db.scalars(
                select(ScopeClaimMember)
                .where(ScopeClaimMember.group_id.in_(group_ids))
                .order_by(ScopeClaimMember.origin_run_id, ScopeClaimMember.claim_id)
            )
        )
        if group_ids
        else []
    )
    resolutions = (
        list(
            db.scalars(
                select(ScopeClaimResolution)
                .where(ScopeClaimResolution.group_id.in_(group_ids))
                .order_by(ScopeClaimResolution.group_id)
            )
        )
        if group_ids
        else []
    )
    members_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        members_by_group[member.group_id].append(
            {
                "member_id": member.member_id,
                "origin_run_id": member.origin_run_id,
                "claim_id": member.claim_id,
            }
        )
    return {
        "reasoning": {
            "reasoning_run_id": reasoning_run.reasoning_run_id,
            "policy_version": reasoning_run.policy_version,
            "policy_hash": reasoning_run.policy_hash,
            "evidence_fingerprint": reasoning_run.evidence_fingerprint,
            "engine_version": reasoning_run.engine_version,
            "status": reasoning_run.status,
        },
        "scope_claim_groups": [
            {
                "group_id": group.group_id,
                "normalized_key": group.normalized_key,
                "representative_claim_text": group.representative_claim_text,
                "unit": group.unit,
                "time_scope": group.time_scope,
                "members": members_by_group[group.group_id],
            }
            for group in groups
        ],
        "scope_resolutions": [
            {
                "resolution_id": resolution.resolution_id,
                "group_id": resolution.group_id,
                "status": resolution.status,
                "confidence": resolution.confidence,
                "support_quality": resolution.support_quality,
                "refute_quality": resolution.refute_quality,
                "independent_support_count": resolution.independent_support_count,
                "independent_refute_count": resolution.independent_refute_count,
                "rationale": _mapping(_json_load(resolution.rationale_json)),
            }
            for resolution in resolutions
        ],
    }


def _relation_inputs(
    group_claims: list[dict[str, Any]],
    edges_by_claim: dict[str, list[dict[str, Any]]],
    assertions: dict[str, dict[str, Any]],
    passages: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    documents: dict[str, dict[str, Any]],
    source_alias_by_member: dict[str, dict[str, Any]],
    passage_alias_by_member: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for claim in group_claims:
        for edge in edges_by_claim.get(_text(claim.get("claim_id")), []):
            assertion = assertions.get(_text(edge.get("assertion_id")))
            passage = passages.get(_text((assertion or {}).get("passage_id")))
            snapshot = snapshots.get(_text((passage or {}).get("snapshot_id")))
            document = documents.get(_text((snapshot or {}).get("document_id")))
            if not all((assertion, passage, snapshot, document)):
                continue
            inputs.append(
                {
                    "claim": claim,
                    "edge": edge,
                    "assertion": assertion,
                    "passage": passage,
                    "snapshot": snapshot,
                    "document": document,
                    "source_cluster_id": scope_source_cluster_id(document, passage),
                    "source_alias": source_alias_by_member.get(
                        _text(document.get("document_id"))
                    ),
                    "passage_alias": passage_alias_by_member.get(
                        _text(passage.get("passage_id"))
                    ),
                }
            )
    return inputs


def _clear_incomplete_materialization(db: Session, reasoning_run_id: str) -> None:
    groups = list(
        db.scalars(
            select(ScopeClaimGroup).where(
                ScopeClaimGroup.reasoning_run_id == reasoning_run_id
            )
        )
    )
    group_ids = [group.group_id for group in groups]
    if group_ids:
        for row in db.scalars(
            select(ScopeClaimResolution).where(
                ScopeClaimResolution.group_id.in_(group_ids)
            )
        ):
            db.delete(row)
        for row in db.scalars(
            select(ScopeClaimMember).where(ScopeClaimMember.group_id.in_(group_ids))
        ):
            db.delete(row)
        for group in groups:
            db.delete(group)
        db.flush()


def _evidence_fingerprint(bundle: dict[str, Any]) -> str:
    payload = {
        key: bundle.get(key) or []
        for key in (
            "source_documents",
            "source_snapshots",
            "passages",
            "assertions",
            "claims",
            "edges",
            "scope_identity",
        )
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _index(items: Any, key: str) -> dict[str, dict[str, Any]]:
    return {
        _text(item.get(key)): item
        for item in (items or [])
        if isinstance(item, dict) and _text(item.get(key))
    }


def _alias_index(items: Any, member_key: str) -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for member_id in item.get(member_key) or []:
            aliases[_text(member_id)] = item
    return aliases


def _scalar(value: Any) -> float | None:
    raw = value.get("value") if isinstance(value, dict) else value
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_load(value: str | None) -> Any:
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:48]}"


__all__ = [
    "SCOPE_REASONING_ENGINE_VERSION",
    "get_scope_reasoning_bundle",
    "materialize_scope_reasoning",
    "scope_claim_group_key",
    "scope_source_cluster_id",
]
