"""Logical cross-run Evidence aggregation for a Research Scope.

Rows remain owned by their originating AgentRun. This module only builds a
read-only projection and never copies child evidence into the root run.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.evidence.scope_identity import build_scope_identity_projection
from app.evidence.service import get_provenance_bundle
from app.research.models import ResearchScope
from app.research.scope import list_scope_nodes, list_scope_runs, list_scope_traces


ENTITY_KEYS = (
    "source_documents",
    "source_snapshots",
    "passages",
    "assertions",
    "claims",
    "edges",
    "report_claims",
    "citations",
    "reliability_scores",
    "resolutions",
)


def get_scope_provenance_bundle(db: Session, scope: ResearchScope | str) -> dict[str, Any]:
    """Aggregate every run's current Evidence revision without changing ownership."""

    scope_obj = db.get(ResearchScope, scope) if isinstance(scope, str) else scope
    if scope_obj is None:
        raise ValueError("Research scope not found")
    runs = list_scope_runs(db, scope_obj.scope_id)
    run_rank = {run.run_id: index for index, run in enumerate(runs)}
    node_by_run = {
        node.run_id: node.node_id
        for node in list_scope_nodes(db, scope_obj.scope_id)
        if node.run_id
    }
    combined: dict[str, list[dict[str, Any]]] = {key: [] for key in ENTITY_KEYS}
    revisions: list[dict[str, Any]] = []

    for run in runs:
        try:
            bundle = get_provenance_bundle(db, run.run_id)
        except ValueError:
            continue
        tagged = _tag_bundle(bundle, run.run_id, node_by_run.get(run.run_id))
        for key in ENTITY_KEYS:
            combined[key].extend(tagged.get(key) or [])
        revisions.append(
            {
                "run_id": run.run_id,
                "schema_version": bundle.get("schema_version"),
                "extractor_version": bundle.get("extractor_version"),
                "status": bundle.get("status"),
            }
        )

    _sort_entities(combined, run_rank)
    _assign_scope_citation_labels(combined)
    scope_identity, metrics = build_scope_identity_projection(combined, run_rank)
    trace_ids = {trace.trace_id for trace in list_scope_traces(db, scope_obj.scope_id)}
    integrity = _scope_integrity(combined, scope_obj.root_run_id, trace_ids)
    projection_status = (
        "empty"
        if not integrity["has_evidence"]
        else "complete"
        if revisions and integrity["all_traceability_resolves"]
        else "partial"
    )
    from app.evidence.scope_reasoning import get_scope_reasoning_bundle

    scope_reasoning = get_scope_reasoning_bundle(db, scope_obj.scope_id)
    return {
        # Keep the single-run ProvenanceBundle top-level contract so existing
        # report/citation consumers can read a scope projection unchanged.
        "run_id": scope_obj.root_run_id,
        "schema_version": "research-scope-evidence-v2",
        "extractor_version": "multi-run",
        "scope_id": scope_obj.scope_id,
        "root_run_id": scope_obj.root_run_id,
        "engine_version": scope_obj.engine_version,
        "status": projection_status,
        "runs": [
            {
                "run_id": run.run_id,
                "parent_run_id": run.parent_run_id,
                "root_run_id": run.root_run_id,
                "run_role": run.run_role,
                "research_node_id": node_by_run.get(run.run_id),
                "status": run.status,
            }
            for run in runs
        ],
        "revisions": revisions,
        **combined,
        "scope_identity": scope_identity,
        "metrics": metrics,
        **scope_reasoning,
        "integrity": integrity,
    }


def get_scope_reasoning_bundle(db: Session, scope: ResearchScope | str) -> dict[str, Any]:
    from app.evidence.scope_reasoning import (
        get_scope_reasoning_bundle as get_persisted_scope_reasoning,
    )

    scope_obj = db.get(ResearchScope, scope) if isinstance(scope, str) else scope
    if scope_obj is None:
        raise ValueError("Research scope not found")
    return get_persisted_scope_reasoning(db, scope_obj.scope_id)


def _tag_bundle(
    bundle: dict[str, Any], run_id: str, node_id: str | None
) -> dict[str, list[dict[str, Any]]]:
    tagged = {key: [deepcopy(item) for item in (bundle.get(key) or [])] for key in ENTITY_KEYS}
    snapshots_by_document: dict[str, list[dict[str, Any]]] = {}
    for snapshot in tagged["source_snapshots"]:
        snapshots_by_document.setdefault(str(snapshot.get("document_id")), []).append(snapshot)
    assertion_by_id = {item.get("assertion_id"): item for item in tagged["assertions"]}
    passage_by_id = {item.get("passage_id"): item for item in tagged["passages"]}
    edges_by_claim: dict[str, list[dict[str, Any]]] = {}
    for edge in tagged["edges"]:
        edges_by_claim.setdefault(str(edge.get("claim_id")), []).append(edge)
    claim_by_id = {item.get("claim_id"): item for item in tagged["claims"]}
    report_claim_by_id = {
        item.get("report_claim_id"): item for item in tagged["report_claims"]
    }

    def traces_for(item: dict[str, Any], key: str) -> list[str]:
        if key == "source_documents":
            values = [x.get("trace_id") for x in snapshots_by_document.get(str(item.get("document_id")), [])]
        elif key in {"source_snapshots", "passages", "assertions"}:
            values = [item.get("trace_id")]
        elif key == "edges":
            values = [assertion_by_id.get(item.get("assertion_id"), {}).get("trace_id")]
        elif key in {"claims", "resolutions"}:
            claim_id = item.get("claim_id")
            values = [
                assertion_by_id.get(edge.get("assertion_id"), {}).get("trace_id")
                for edge in edges_by_claim.get(str(claim_id), [])
            ]
        elif key == "report_claims":
            claim = claim_by_id.get(item.get("claim_id"), {})
            values = traces_for(claim, "claims")
        elif key == "citations":
            values = [passage_by_id.get(item.get("passage_id"), {}).get("trace_id")]
        elif key == "reliability_scores":
            edge = next((x for x in tagged["edges"] if x.get("edge_id") == item.get("edge_id")), {})
            values = traces_for(edge, "edges")
        else:
            values = []
        return list(dict.fromkeys(str(value) for value in values if value))

    for key, items in tagged.items():
        for item in items:
            trace_ids = traces_for(item, key)
            item["origin_run_id"] = run_id
            item["origin_trace_id"] = trace_ids[0] if trace_ids else None
            item["origin_trace_ids"] = trace_ids
            item["research_node_id"] = node_id
            if key == "citations":
                report_claim = report_claim_by_id.get(item.get("report_claim_id"), {})
                item["report_claim_origin_run_id"] = report_claim.get("origin_run_id", run_id)
    return tagged


def _sort_entities(
    combined: dict[str, list[dict[str, Any]]], run_rank: dict[str, int]
) -> None:
    id_keys = {
        "source_documents": "document_id",
        "source_snapshots": "snapshot_id",
        "passages": "passage_id",
        "assertions": "assertion_id",
        "claims": "claim_id",
        "edges": "edge_id",
        "report_claims": "report_claim_id",
        "citations": "citation_id",
        "reliability_scores": "score_id",
        "resolutions": "resolution_id",
    }
    for key, items in combined.items():
        identifier = id_keys[key]
        items.sort(
            key=lambda item: (
                run_rank.get(str(item.get("origin_run_id")), 10**9),
                int(item.get("ordinal") or 0),
                str(item.get(identifier) or ""),
            )
        )


def _assign_scope_citation_labels(combined: dict[str, list[dict[str, Any]]]) -> None:
    claim_order = {
        claim.get("report_claim_id"): index
        for index, claim in enumerate(combined["report_claims"], 1)
    }
    per_claim: dict[str, int] = {}
    for citation in combined["citations"]:
        report_claim_id = str(citation.get("report_claim_id") or "")
        per_claim[report_claim_id] = per_claim.get(report_claim_id, 0) + 1
        citation["origin_citation_label"] = citation.get("citation_label")
        citation["citation_label"] = (
            f"CIT-{claim_order.get(report_claim_id, 0):03d}-{per_claim[report_claim_id]:02d}"
        )


def _scope_integrity(
    combined: dict[str, list[dict[str, Any]]],
    root_run_id: str,
    trace_ids: set[str],
) -> dict[str, Any]:
    snapshot_ids = {item.get("snapshot_id") for item in combined["source_snapshots"]}
    passage_ids = {item.get("passage_id") for item in combined["passages"]}
    assertion_ids = {item.get("assertion_id") for item in combined["assertions"]}
    claim_ids = {item.get("claim_id") for item in combined["claims"]}
    report_claim_ids = {item.get("report_claim_id") for item in combined["report_claims"]}
    citations = combined["citations"]
    integrity = {
        "has_evidence": bool(combined["passages"]),
        "citation_count": len(citations),
        "report_claim_count": len(combined["report_claims"]),
        "child_citation_count": sum(
            1 for item in citations if item.get("origin_run_id") != root_run_id
        ),
        "all_passages_resolve": all(
            item.get("snapshot_id") in snapshot_ids
            and item.get("origin_trace_id") in trace_ids
            for item in combined["passages"]
        ),
        "all_assertions_resolve": all(
            item.get("passage_id") in passage_ids
            and item.get("origin_trace_id") in trace_ids
            for item in combined["assertions"]
        ),
        "all_edges_resolve": all(
            item.get("claim_id") in claim_ids and item.get("assertion_id") in assertion_ids
            for item in combined["edges"]
        ),
        "all_citations_resolve": all(
            item.get("passage_id") in passage_ids
            and item.get("report_claim_id") in report_claim_ids
            and item.get("origin_trace_id") in trace_ids
            for item in citations
        ),
    }
    integrity["all_traceability_resolves"] = all(
        integrity[key]
        for key in (
            "all_passages_resolve",
            "all_assertions_resolve",
            "all_edges_resolve",
            "all_citations_resolve",
        )
    )
    return integrity
