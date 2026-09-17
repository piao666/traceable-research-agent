"""Deterministic claim-centric evidence quality metrics."""

from __future__ import annotations

from typing import Any


QUALITY_SCHEMA_VERSION = "evidence-quality-v3"


def calculate_evidence_quality(
    bundle: dict[str, Any],
    *,
    citation_accuracy: float = 0.0,
) -> dict[str, Any]:
    report_claims = [item for item in bundle.get("report_claims") or [] if isinstance(item, dict)]
    edges = [item for item in bundle.get("edges") or [] if isinstance(item, dict)]
    citations = [item for item in bundle.get("citations") or [] if isinstance(item, dict)]
    scores = [item for item in bundle.get("reliability_scores") or [] if isinstance(item, dict)]
    resolutions = [item for item in bundle.get("resolutions") or [] if isinstance(item, dict)]

    scores_by_edge = {str(item.get("edge_id") or ""): item for item in scores}
    cited_edges = {str(item.get("edge_id") or "") for item in citations}
    # A citation attached only to contextual metadata is not claim support.
    # Keep the metric name for API compatibility, but make its denominator
    # reflect evidence relations that can actually support or refute claims.
    eligible_edge_ids = {
        str(item.get("edge_id") or "")
        for item in edges
        if str(item.get("relation") or "").casefold() in {"supports", "refutes"}
    }
    claim_id_by_report = {
        str(item.get("report_claim_id") or ""): str(item.get("claim_id") or "")
        for item in report_claims
    }
    cited_claim_ids = {
        claim_id_by_report.get(str(item.get("report_claim_id") or ""), "")
        for item in citations
        if str(item.get("edge_id") or "") in eligible_edge_ids
    }
    cited_claim_ids.discard("")

    supporting_edges: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if str(edge.get("relation") or "") != "supports":
            continue
        supporting_edges.setdefault(str(edge.get("claim_id") or ""), []).append(edge)

    strong_claims = 0
    independent_claims = 0
    for claim_id in cited_claim_ids:
        claim_scores = [
            scores_by_edge.get(str(edge.get("edge_id") or ""), {})
            for edge in supporting_edges.get(claim_id, [])
        ]
        if any(float(item.get("total_score") or 0.0) >= 0.75 for item in claim_scores):
            strong_claims += 1
        clusters = {str(item.get("source_cluster_id") or "") for item in claim_scores}
        clusters.discard("")
        if len(clusters) >= 2:
            independent_claims += 1

    cited_scores = sorted(
        float(score.get("total_score") or 0.0)
        for edge_id, score in scores_by_edge.items()
        if edge_id in cited_edges
    )
    mean_reliability = sum(cited_scores) / len(cited_scores) if cited_scores else 0.0
    p25_reliability = cited_scores[max(0, int(len(cited_scores) * 0.25) - 1)] if cited_scores else 0.0
    clusters = {str(item.get("source_cluster_id") or "") for item in scores}
    clusters.discard("")
    if not clusters:
        # Scope identity is the V3 fallback when no claim reliability scores
        # were produced (for example, a completed run with no report claims).
        aliases = [
            alias
            for alias in (bundle.get("scope_identity") or {}).get("independence_aliases") or []
            if isinstance(alias, dict) and alias.get("representative_document_id")
        ]
        if len(aliases) > 1:
            clusters.update(str(alias["representative_document_id"]) for alias in aliases)
    unique_resources = {
        str(item.get("canonical_uri") or item.get("document_id") or "")
        for item in bundle.get("source_documents") or []
        if isinstance(item, dict) and (item.get("canonical_uri") or item.get("document_id"))
    }
    unresolved = sum(
        1
        for item in resolutions
        if str(item.get("resolution") or item.get("status") or "").casefold()
        in {"unresolved", "conflicted", "conflict"}
    )
    total_claims = len(report_claims)
    support_coverage = len(cited_claim_ids) / total_claims if total_claims else 0.0
    strong_coverage = strong_claims / total_claims if total_claims else 0.0
    independent_coverage = independent_claims / total_claims if total_claims else 0.0
    score = 10.0 * (
        0.30 * support_coverage
        + 0.20 * strong_coverage
        + 0.15 * independent_coverage
        + 0.25 * mean_reliability
        + 0.10 * max(0.0, min(1.0, citation_accuracy))
    )
    if unresolved:
        score *= max(0.5, 1.0 - 0.1 * unresolved)
    return {
        "quality_schema_version": QUALITY_SCHEMA_VERSION,
        "evidence_quality_score": round(score, 3),
        "claim_support_coverage": round(support_coverage, 6),
        "strong_claim_coverage": round(strong_coverage, 6),
        "independent_claim_coverage": round(independent_coverage, 6),
        "mean_cited_reliability": round(mean_reliability, 6),
        "p25_cited_reliability": round(p25_reliability, 6),
        "citation_accuracy": round(max(0.0, min(1.0, citation_accuracy)), 6),
        "unique_resource_count": len(unique_resources),
        "independent_source_count": len(clusters),
        "unresolved_conflict_count": unresolved,
    }
