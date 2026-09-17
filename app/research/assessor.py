"""Read-only, deterministic requirement assessor for the PEAR shadow phase.

The assessor deliberately does not call tools, mutate a Run, or decide whether
to replan.  It converts the current evidence/Scope projection into explicit
requirement states and gaps so the future PEAR controller can consume a
replayable snapshot without treating quality scores as completion gates.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.evidence.policy import evidence_role_supports_claim
from app.research.contracts import normalize_requirements
from app.research.models import CoverageSnapshot, EvidenceGapRecord


ASSESSOR_VERSION = "pear-assessor-v1"


def _source_role(source: dict[str, Any]) -> str:
    metadata = source.get("metadata")
    if isinstance(metadata, dict) and metadata.get("evidence_role"):
        return str(metadata["evidence_role"])
    return str(source.get("evidence_role") or "unknown")


def _source_independence_key(source: dict[str, Any]) -> str:
    identity = source.get("source_identity")
    if isinstance(identity, dict):
        value = identity.get("independence_group") or identity.get("resource_identity")
        if value:
            return str(value)
    return str(source.get("independence_group") or urlsplit(str(source.get("url") or "")).netloc).strip().casefold()


def _claim_text(requirement: dict[str, Any]) -> str:
    return " ".join(
        str(requirement.get(key) or "")
        for key in ("predicate", "entity", "dimension", "time_scope")
    ).strip()


def _links_for(contract: dict[str, Any], requirement_id: str) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in contract.get("requirement_claim_links") or []
        if isinstance(item, dict) and str(item.get("requirement_id") or "") == requirement_id
    ]


def _scope_groups(scope_evidence: dict[str, Any] | None, group_ids: set[str]) -> list[dict[str, Any]]:
    if not group_ids:
        return []
    return [
        dict(group)
        for group in (scope_evidence or {}).get("claim_groups") or []
        if isinstance(group, dict) and str(group.get("group_id") or "") in group_ids
    ]


def _assess_linked_requirement(
    requirement: dict[str, Any],
    links: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    scope_evidence: dict[str, Any] | None,
) -> tuple[str, list[str], dict[str, Any]]:
    """Assess only explicit mappings; absent mappings cannot pass by inference."""

    claim_text = _claim_text(requirement)
    source_ids = {
        str(item.get("source_id"))
        for item in links
        if item.get("source_id")
    }
    group_ids = {
        str(item.get("scope_group_id"))
        for item in links
        if item.get("scope_group_id")
    }
    mapped_sources = [
        source for source in sources
        if source.get("source_id") in source_ids
        or (group_ids and str(source.get("scope_group_id") or "") in group_ids)
    ]
    groups = _scope_groups(scope_evidence, group_ids)
    if not links:
        return "uncovered", ["missing_mapping"], {"mapped_claims": 0, "mapped_groups": 0}

    acceptable_basis = set(requirement.get("acceptable_content_basis") or ("full_text",))
    eligible = []
    rejected_roles = 0
    for source in mapped_sources:
        role = _source_role(source)
        if not evidence_role_supports_claim(role, claim_text):
            rejected_roles += 1
            continue
        if source.get("fetch_status") != "fetched":
            continue
        if str(source.get("content_basis") or "snippet_only").casefold() not in acceptable_basis:
            continue
        eligible.append(source)

    independent = {_source_independence_key(source) for source in eligible}
    independent.discard("")
    min_sources = int(requirement.get("min_independent_sources") or 0)
    reliability = [
        float(source["reliability_score"])
        for source in eligible
        if source.get("reliability_score") is not None
    ]
    min_reliability = float(requirement.get("min_reliability") or 0.0)
    reliability_ok = not min_reliability or any(score >= min_reliability for score in reliability)
    unresolved = any(
        str(group.get("status") or "").casefold() in {"unresolved", "requires_human"}
        for group in groups
    )
    gaps: list[str] = []
    if not eligible:
        gaps.append("unknown_evidence_role" if rejected_roles else "missing_evidence")
    if len(independent) < min_sources:
        gaps.append("missing_independence")
    if not reliability_ok:
        gaps.append("missing_freshness")
    if unresolved:
        gaps.append("conflict")
    if unresolved:
        status = "conflicted"
    elif not eligible:
        status = "partial"
    elif gaps:
        status = "partial"
    else:
        status = "satisfied"
    return status, list(dict.fromkeys(gaps)), {
        "mapped_claims": sum(bool(item.get("claim_occurrence_id")) for item in links),
        "mapped_groups": len(group_ids),
        "eligible_sources": len(eligible),
        "independent_sources": len(independent),
        "independence_satisfied": len(independent) >= min_sources,
        "reliability_satisfied": reliability_ok,
    }


def assess_requirements(
    contract: dict[str, Any] | None,
    source_context: dict[str, Any] | None = None,
    *,
    traces=None,
    scope_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a replayable requirement assessment without external side effects."""

    contract = dict(contract or {})
    normalized = [item.model_dump(mode="json") for item in normalize_requirements(contract)]
    sources = [dict(item) for item in (source_context or {}).get("sources") or [] if isinstance(item, dict)]

    # Preserve the existing comparison matrix as a diagnostic input, while
    # converting its legacy ``covered`` state to the canonical PEAR state.
    comparison_rows: dict[str, dict[str, Any]] = {}
    if contract.get("goal_kind") == "comparison" and normalized:
        from app.research.coverage import assess_comparison_coverage

        comparison = assess_comparison_coverage(contract, source_context, traces=traces)
        comparison_rows = {
            str(row.get("requirement_id")): dict(row)
            for row in comparison.get("requirements") or []
            if isinstance(row, dict)
        }

    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for requirement in normalized:
        requirement_id = str(requirement["requirement_id"])
        links = _links_for(contract, requirement_id)
        if requirement_id in comparison_rows:
            legacy = comparison_rows[requirement_id]
            status = "satisfied" if legacy.get("status") == "covered" else str(legacy.get("status") or "uncovered")
            missing = [] if status == "satisfied" else ["missing_evidence"]
            details = {
                "mapped_claims": 0,
                "mapped_groups": 0,
                "eligible_sources": len(legacy.get("source_ids") or []),
                "independent_sources": int(legacy.get("independent_hosts") or 0),
                "independence_satisfied": bool(legacy.get("independence_satisfied")),
                "reliability_satisfied": bool(legacy.get("reliability_satisfied")),
                "legacy_coverage_status": legacy.get("status"),
            }
        else:
            status, missing, details = _assess_linked_requirement(
                requirement, links, sources, scope_evidence
            )
        row = {**requirement, **details, "status": status}
        rows.append(row)
        if missing:
            gaps.append({
                "gap_id": f"gap-{requirement_id}",
                "requirement_id": requirement_id,
                "type": missing[0],
                "missing_dimensions": missing,
                "status": "open",
            })

    required_rows = [row for row in rows if row.get("required", True)]
    complete = bool(required_rows) and all(row["status"] == "satisfied" for row in required_rows)
    if not required_rows and normalized:
        complete = False
    return {
        "assessor_version": ASSESSOR_VERSION,
        "applicable": bool(normalized),
        "complete": complete,
        "status": "satisfied" if complete else "incomplete",
        "requirements": rows,
        "gaps": gaps,
        "external_calls": 0,
        "mutated_run": False,
    }


def persist_shadow_assessment(
    db: Session,
    *,
    root_run_id: str,
    result: dict[str, Any],
    scope_id: str | None = None,
    plan_revision_id: str | None = None,
) -> CoverageSnapshot:
    """Persist an idempotent assessment snapshot without touching Run status/evidence."""

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "requirements": result.get("requirements") or [],
                "gaps": result.get("gaps") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    snapshot_id = "cov-" + hashlib.sha256(
        f"{root_run_id}|{ASSESSOR_VERSION}|{fingerprint}".encode("utf-8")
    ).hexdigest()[:56]
    snapshot = db.get(CoverageSnapshot, snapshot_id)
    if snapshot is None:
        snapshot = CoverageSnapshot(
            snapshot_id=snapshot_id,
            root_run_id=root_run_id,
            scope_id=scope_id,
            plan_revision_id=plan_revision_id,
            assessor_version=ASSESSOR_VERSION,
            status=str(result.get("status") or "incomplete"),
            requirements_json=json.dumps(result.get("requirements") or [], ensure_ascii=False, sort_keys=True),
            gaps_json=json.dumps(result.get("gaps") or [], ensure_ascii=False, sort_keys=True),
            evidence_fingerprint=fingerprint,
        )
        db.add(snapshot)
    else:
        snapshot.scope_id = scope_id
        snapshot.plan_revision_id = plan_revision_id
        snapshot.status = str(result.get("status") or "incomplete")
        snapshot.requirements_json = json.dumps(result.get("requirements") or [], ensure_ascii=False, sort_keys=True)
        snapshot.gaps_json = json.dumps(result.get("gaps") or [], ensure_ascii=False, sort_keys=True)
        snapshot.evidence_fingerprint = fingerprint
        db.execute(delete(EvidenceGapRecord).where(EvidenceGapRecord.snapshot_id == snapshot_id))

    for gap in result.get("gaps") or []:
        if not isinstance(gap, dict):
            continue
        db.add(
            EvidenceGapRecord(
                gap_id=f"{snapshot_id}:{gap.get('requirement_id')}",
                snapshot_id=snapshot_id,
                requirement_id=str(gap.get("requirement_id") or "unknown"),
                gap_type=str(gap.get("type") or "missing_evidence"),
                missing_dimensions_json=json.dumps(gap.get("missing_dimensions") or [], ensure_ascii=False),
                related_claims_json=json.dumps(gap.get("related_claims") or [], ensure_ascii=False),
                related_groups_json=json.dumps(gap.get("related_groups") or [], ensure_ascii=False),
                suggested_action=str(gap.get("suggested_action") or "Fetch and verify an eligible source."),
                status=str(gap.get("status") or "open"),
            )
        )
    db.flush()
    return snapshot
