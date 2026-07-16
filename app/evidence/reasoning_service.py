"""Persist policy-scored evidence relations and Claim conflict resolutions."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence.models import (
    ClaimEvidenceEdge,
    ClaimResolution,
    EvidenceAssertion,
    EvidencePassage,
    EvidenceReasoningRun,
    EvidenceReliabilityScore,
    ResearchClaim,
    SourceDocument,
    SourceSnapshot,
)
from app.evidence.policy import load_source_policy, score_reliability, source_cluster_id
from app.evidence.policy import classify_claim
from app.evidence.reasoning import (
    REASONING_ENGINE_VERSION,
    ScoredRelation,
    classify_relation,
    normalize_fact,
    resolve_conflict,
)


def materialize_reasoning(
    db: Session,
    run_id: str,
    policy_path: str | Path,
) -> dict[str, Any]:
    path = Path(policy_path)
    policy_bytes = path.read_bytes()
    policy = load_source_policy(path)
    policy_hash = hashlib.sha256(
        policy_bytes + b"\0" + REASONING_ENGINE_VERSION.encode("utf-8")
    ).hexdigest()
    reasoning_run_id = _stable_id("reason", run_id, policy.version, policy_hash)
    existing = db.get(EvidenceReasoningRun, reasoning_run_id)
    if existing is not None and existing.status == "complete":
        return get_reasoning_bundle(db, run_id, reasoning_run_id=reasoning_run_id)

    documents = list(db.scalars(select(SourceDocument).where(SourceDocument.run_id == run_id)))
    document_by_id = {item.document_id: item for item in documents}
    snapshots = list(
        db.scalars(
            select(SourceSnapshot).where(SourceSnapshot.document_id.in_(document_by_id))
        )
    ) if document_by_id else []
    snapshot_by_id = {item.snapshot_id: item for item in snapshots}
    evaluation_time = max(
        (item.fetched_at for item in snapshots),
        default=datetime.now(timezone.utc),
    )
    if evaluation_time.tzinfo is None:
        evaluation_time = evaluation_time.replace(tzinfo=timezone.utc)
    passages = list(
        db.scalars(
            select(EvidencePassage).where(EvidencePassage.snapshot_id.in_(snapshot_by_id))
        )
    ) if snapshot_by_id else []
    passage_by_id = {item.passage_id: item for item in passages}
    assertions = list(
        db.scalars(
            select(EvidenceAssertion).where(EvidenceAssertion.passage_id.in_(passage_by_id))
        )
    ) if passage_by_id else []
    assertion_by_id = {item.assertion_id: item for item in assertions}
    claims = list(db.scalars(select(ResearchClaim).where(ResearchClaim.run_id == run_id)))
    claim_by_id = {item.claim_id: item for item in claims}
    edges = list(
        db.scalars(select(ClaimEvidenceEdge).where(ClaimEvidenceEdge.claim_id.in_(claim_by_id)))
    ) if claim_by_id else []

    passage_hash_counts = Counter(item.content_hash for item in passages)
    duplicate_hashes = {key for key, count in passage_hash_counts.items() if count > 1}
    cluster_by_assertion: dict[str, str] = {}
    for assertion in assertions:
        passage = passage_by_id[assertion.passage_id]
        snapshot = snapshot_by_id[passage.snapshot_id]
        document = document_by_id[snapshot.document_id]
        cluster_by_assertion[assertion.assertion_id] = source_cluster_id(
            passage_hash=passage.content_hash,
            canonical_uri=document.canonical_uri,
            organization=document.organization,
            duplicate_passage_hashes=duplicate_hashes,
        )
    cluster_sizes = Counter(cluster_by_assertion.values())
    scored_by_claim: dict[str, list[ScoredRelation]] = defaultdict(list)
    score_rows: list[EvidenceReliabilityScore] = []

    try:
        reasoning_run = existing or EvidenceReasoningRun(
            reasoning_run_id=reasoning_run_id,
            run_id=run_id,
            policy_version=policy.version,
            policy_hash=policy_hash,
            status="building",
        )
        reasoning_run.status = "building"
        db.add(reasoning_run)
        db.flush()

        for edge in edges:
            claim = claim_by_id[edge.claim_id]
            assertion = assertion_by_id[edge.assertion_id]
            passage = passage_by_id[assertion.passage_id]
            snapshot = snapshot_by_id[passage.snapshot_id]
            document = document_by_id[snapshot.document_id]
            document_metadata = _json_object(document.metadata_json)
            document_metadata["provider"] = document.provider
            passage_metadata = _json_object(passage.metadata_json)
            locator = _json_object(passage.locator_json)
            cluster = cluster_by_assertion[assertion.assertion_id]
            breakdown = score_reliability(
                claim_text=claim.claim_text,
                assertion_text=assertion.object_text,
                source_type=document.source_type,
                canonical_uri=document.canonical_uri,
                organization=document.organization,
                source_metadata=document_metadata,
                passage_metadata=passage_metadata,
                locator=locator,
                trace_id=assertion.trace_id,
                snapshot_hash=snapshot.content_hash,
                fetched_at=snapshot.fetched_at,
                extraction_confidence=assertion.extraction_confidence,
                polarity=assertion.polarity,
                scalar_present=_scalar(assertion.value_json) is not None,
                source_cluster=cluster,
                cluster_size=cluster_sizes[cluster],
                policy=policy,
                now=evaluation_time,
            )
            relation = classify_relation(
                normalize_fact(
                    claim.claim_text,
                    value=_scalar(claim.value_json),
                    unit=claim.unit,
                    time_scope=claim.time_scope,
                ),
                normalize_fact(
                    assertion.object_text,
                    value=_scalar(assertion.value_json),
                    unit=assertion.unit,
                    time_scope=assertion.time_scope,
                    polarity=assertion.polarity,
                ),
                prior_relation=edge.relation,
            )
            edge.relation = relation.relation
            edge.score = breakdown.total_score
            edge.rationale = relation.rationale
            db.add(edge)
            score_rows.append(
                EvidenceReliabilityScore(
                    score_id=_stable_id("score", reasoning_run_id, edge.edge_id),
                    reasoning_run_id=reasoning_run_id,
                    edge_id=edge.edge_id,
                    claim_type=breakdown.claim_type,
                    source_class=breakdown.source_class,
                    source_cluster_id=breakdown.source_cluster_id,
                    authority=breakdown.authority,
                    traceability=breakdown.traceability,
                    freshness=breakdown.freshness,
                    relevance=breakdown.relevance,
                    independence=breakdown.independence,
                    extraction_completeness=breakdown.extraction_completeness,
                    total_score=breakdown.total_score,
                    rationale_json=_json_dump(
                        {
                            **breakdown.rationale,
                            "reasoning_engine_version": REASONING_ENGINE_VERSION,
                            "relation_rationale": relation.rationale,
                            "scope_difference": relation.scope_difference,
                        }
                    ),
                )
            )
            scored_by_claim[claim.claim_id].append(
                ScoredRelation(
                    relation=relation.relation,
                    score=breakdown.total_score,
                    source_cluster_id=breakdown.source_cluster_id,
                    source_class=breakdown.source_class,
                    time_scope=assertion.time_scope,
                    scope_difference=relation.scope_difference,
                    is_correction=bool(
                        passage_metadata.get("is_correction")
                        or passage_metadata.get("correction")
                        or passage_metadata.get("supersedes")
                    ),
                )
            )

        db.add_all(score_rows)
        db.flush()
        resolution_rows: list[ClaimResolution] = []
        for claim in claims:
            resolution = resolve_conflict(scored_by_claim.get(claim.claim_id, []), policy)
            claim_type = classify_claim(claim.claim_text, policy)
            profile = policy.claim_types[claim_type]
            winner_relation = (
                "supports"
                if resolution.support_quality >= resolution.refute_quality
                else "refutes"
            )
            winner_items = [
                item
                for item in scored_by_claim.get(claim.claim_id, [])
                if item.relation == winner_relation
            ]
            independent_winner_count = len({item.source_cluster_id for item in winner_items})
            strongest_winner_score = max((item.score for item in winner_items), default=0.0)
            minimum_sources = int(profile.get("minimum_independent_sources") or 1)
            minimum_reliability = float(profile.get("minimum_reliability") or 0.0)
            quality_gate_passed = (
                independent_winner_count >= minimum_sources
                and strongest_winner_score >= minimum_reliability
            )
            confidence = resolution.confidence
            if not quality_gate_passed:
                confidence = min(confidence, 0.69)
            rationale = {
                **resolution.rationale,
                "quality_gate": {
                    "passed": quality_gate_passed,
                    "winner_relation": winner_relation,
                    "independent_source_count": independent_winner_count,
                    "minimum_independent_sources": minimum_sources,
                    "strongest_source_score": strongest_winner_score,
                    "minimum_reliability": minimum_reliability,
                },
                "reasoning_engine_version": REASONING_ENGINE_VERSION,
            }
            resolution_rows.append(
                ClaimResolution(
                    resolution_id=_stable_id("resolution", reasoning_run_id, claim.claim_id),
                    reasoning_run_id=reasoning_run_id,
                    claim_id=claim.claim_id,
                    policy_version=policy.version,
                    status=resolution.status,
                    confidence=confidence,
                    support_quality=resolution.support_quality,
                    refute_quality=resolution.refute_quality,
                    independent_support_count=resolution.independent_support_count,
                    independent_refute_count=resolution.independent_refute_count,
                    rationale_json=_json_dump(rationale),
                )
            )
        db.add_all(resolution_rows)
        reasoning_run.status = "complete"
        reasoning_run.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_reasoning_bundle(db, run_id, reasoning_run_id=reasoning_run_id)


def get_reasoning_bundle(
    db: Session,
    run_id: str,
    *,
    reasoning_run_id: str | None = None,
) -> dict[str, Any]:
    if reasoning_run_id:
        reasoning_run = db.get(EvidenceReasoningRun, reasoning_run_id)
    else:
        reasoning_run = db.scalars(
            select(EvidenceReasoningRun)
            .where(EvidenceReasoningRun.run_id == run_id)
            .order_by(EvidenceReasoningRun.created_at.desc())
            .limit(1)
        ).first()
    if reasoning_run is None:
        return {"reasoning": None, "reliability_scores": [], "resolutions": []}
    scores = list(
        db.scalars(
            select(EvidenceReliabilityScore).where(
                EvidenceReliabilityScore.reasoning_run_id == reasoning_run.reasoning_run_id
            )
        )
    )
    resolutions = list(
        db.scalars(
            select(ClaimResolution).where(
                ClaimResolution.reasoning_run_id == reasoning_run.reasoning_run_id
            )
        )
    )
    score_payloads = [_score_dict(item) for item in scores]
    engine_versions = {
        item.get("rationale", {}).get("reasoning_engine_version")
        for item in score_payloads
        if item.get("rationale", {}).get("reasoning_engine_version")
    }
    return {
        "reasoning": {
            "reasoning_run_id": reasoning_run.reasoning_run_id,
            "policy_version": reasoning_run.policy_version,
            "policy_hash": reasoning_run.policy_hash,
            "engine_version": next(iter(engine_versions), "unknown"),
            "status": reasoning_run.status,
            "computed_at": reasoning_run.updated_at.isoformat(),
        },
        "reliability_scores": score_payloads,
        "resolutions": [_resolution_dict(item) for item in resolutions],
    }


def get_claim_evidence_audit(
    db: Session,
    claim_id: str,
    reasoning_run_id: str,
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            ClaimEvidenceEdge.edge_id,
            ClaimEvidenceEdge.assertion_id,
            ClaimEvidenceEdge.relation,
            EvidenceReliabilityScore.source_class,
            EvidenceReliabilityScore.source_cluster_id,
            EvidenceReliabilityScore.total_score,
            EvidenceReliabilityScore.rationale_json,
        )
        .join(
            EvidenceReliabilityScore,
            EvidenceReliabilityScore.edge_id == ClaimEvidenceEdge.edge_id,
        )
        .where(
            ClaimEvidenceEdge.claim_id == claim_id,
            EvidenceReliabilityScore.reasoning_run_id == reasoning_run_id,
        )
        .order_by(ClaimEvidenceEdge.edge_id)
    )
    return [
        {
            **dict(row._mapping),
            "rationale": _json_object(row._mapping["rationale_json"]),
        }
        for row in rows
    ]


def _score_dict(item: EvidenceReliabilityScore) -> dict[str, Any]:
    return {
        "score_id": item.score_id,
        "edge_id": item.edge_id,
        "claim_type": item.claim_type,
        "source_class": item.source_class,
        "source_cluster_id": item.source_cluster_id,
        "dimensions": {
            "authority": item.authority,
            "traceability": item.traceability,
            "freshness": item.freshness,
            "relevance": item.relevance,
            "independence": item.independence,
            "extraction_completeness": item.extraction_completeness,
        },
        "total_score": item.total_score,
        "rationale": _json_object(item.rationale_json),
    }


def _resolution_dict(item: ClaimResolution) -> dict[str, Any]:
    return {
        "resolution_id": item.resolution_id,
        "claim_id": item.claim_id,
        "policy_version": item.policy_version,
        "status": item.status,
        "confidence": item.confidence,
        "support_quality": item.support_quality,
        "refute_quality": item.refute_quality,
        "independent_support_count": item.independent_support_count,
        "independent_refute_count": item.independent_refute_count,
        "rationale": _json_object(item.rationale_json),
    }


def _scalar(value_json: str | None) -> float | None:
    value = _json_object(value_json).get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:48]}"
