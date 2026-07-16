"""Materialize and query the Claim-level Evidence Pipeline V2 graph."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.evidence import ClaimEvidenceMap, EvidenceBundle, EvidenceItem, build_evidence_bundle
from app.config import Settings
from app.evidence import EVIDENCE_SCHEMA_VERSION
from app.evidence.artifact_store import ArtifactStore
from app.evidence.models import (
    Citation,
    ClaimEvidenceEdge,
    EvidenceAssertion,
    EvidencePassage,
    EvidencePipelineRun,
    ReportClaim,
    ResearchClaim,
    SourceDocument,
    SourceSnapshot,
)
from app.evidence.normalizers import (
    canonical_source_uri,
    passage_locator,
    source_organization,
    source_provider,
)
from app.trace.models import AgentRun, ToolTrace


def materialize_execution_provenance(
    db: Session,
    run: AgentRun,
    plan: dict[str, Any],
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
    settings: Settings,
) -> dict[str, Any] | None:
    if settings.evidence_pipeline_version != "v2":
        return None
    bundle = build_evidence_bundle(run, plan, observations, traces)
    return materialize_provenance_bundle(
        db,
        run,
        bundle,
        traces,
        ArtifactStore(Path(settings.evidence_artifact_root)),
        extractor_version=settings.evidence_extractor_version,
        passage_max_chars=settings.evidence_passage_max_chars,
    )


def materialize_provenance_bundle(
    db: Session,
    run: AgentRun,
    bundle: EvidenceBundle,
    traces: list[ToolTrace],
    artifact_store: ArtifactStore,
    *,
    extractor_version: str,
    passage_max_chars: int = 4000,
) -> dict[str, Any]:
    existing = db.get(EvidencePipelineRun, run.run_id)
    if existing is not None and existing.status == "complete":
        return get_provenance_bundle(db, run.run_id)

    trace_by_id = {trace.trace_id: trace for trace in traces}
    assertion_by_evidence_id: dict[str, EvidenceAssertion] = {}
    passage_by_evidence_id: dict[str, EvidencePassage] = {}
    documents_by_id: dict[str, SourceDocument] = {}
    snapshots_by_id: dict[str, SourceSnapshot] = {}
    pending_passages: list[EvidencePassage] = []
    pending_assertions: list[EvidenceAssertion] = []
    pending_claims: list[ResearchClaim] = []
    pending_report_claims: list[ReportClaim] = []
    pending_edges: list[ClaimEvidenceEdge] = []
    pending_citations: list[Citation] = []

    try:
        pipeline = existing or EvidencePipelineRun(
            run_id=run.run_id,
            schema_version=EVIDENCE_SCHEMA_VERSION,
            extractor_version=extractor_version,
            status="building",
        )
        pipeline.schema_version = EVIDENCE_SCHEMA_VERSION
        pipeline.extractor_version = extractor_version
        pipeline.status = "building"
        db.add(pipeline)

        for ordinal, item in enumerate(bundle.evidence_items, 1):
            trace = trace_by_id.get(item.trace_id or "")
            document, snapshot, passage, assertion = _materialize_item(
                run,
                item,
                ordinal,
                trace,
                artifact_store,
                extractor_version,
                passage_max_chars,
            )
            documents_by_id.setdefault(document.document_id, document)
            snapshots_by_id.setdefault(snapshot.snapshot_id, snapshot)
            pending_passages.append(passage)
            pending_assertions.append(assertion)
            passage_by_evidence_id[item.evidence_id] = passage
            assertion_by_evidence_id[item.evidence_id] = assertion

        db.add_all(documents_by_id.values())
        db.flush()
        db.add_all(snapshots_by_id.values())
        db.flush()
        db.add_all(pending_passages)
        db.flush()
        db.add_all(pending_assertions)
        db.flush()

        all_claims = [*bundle.claims, *bundle.unsupported_claims]
        for ordinal, claim_map in enumerate(all_claims, 1):
            claim = _research_claim(run.run_id, claim_map, ordinal, extractor_version)
            report_claim = ReportClaim(
                report_claim_id=_stable_id("rpt", run.run_id, claim.claim_id),
                run_id=run.run_id,
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                section="Evidence-backed claims",
                ordinal=ordinal,
                origin="plan_claim",
            )
            pending_claims.append(claim)
            pending_report_claims.append(report_claim)
            citation_ordinal = 0
            for evidence_id in claim_map.evidence_ids:
                assertion = assertion_by_evidence_id.get(evidence_id)
                passage = passage_by_evidence_id.get(evidence_id)
                if assertion is None or passage is None:
                    continue
                relation = "supports" if claim_map.support_level not in {"unsupported", "none"} else "contextualizes"
                edge = ClaimEvidenceEdge(
                    edge_id=_stable_id("edge", claim.claim_id, assertion.assertion_id),
                    claim_id=claim.claim_id,
                    assertion_id=assertion.assertion_id,
                    relation=relation,
                    score=None,
                    rationale="V1 claim-to-evidence mapping; reliability scoring is deferred to P2.",
                )
                pending_edges.append(edge)
                citation_ordinal += 1
                pending_citations.append(
                    Citation(
                        citation_id=_stable_id("cit", report_claim.report_claim_id, passage.passage_id),
                        report_claim_id=report_claim.report_claim_id,
                        passage_id=passage.passage_id,
                        edge_id=edge.edge_id,
                        citation_label=f"CIT-{ordinal:03d}-{citation_ordinal:02d}",
                    )
                )

        db.add_all(pending_claims)
        db.flush()
        db.add_all(pending_report_claims)
        db.add_all(pending_edges)
        db.flush()
        db.add_all(pending_citations)
        pipeline.status = "complete"
        pipeline.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_provenance_bundle(db, run.run_id)


def get_provenance_bundle(db: Session, run_id: str) -> dict[str, Any]:
    pipeline = db.get(EvidencePipelineRun, run_id)
    if pipeline is None:
        raise ValueError("Evidence Pipeline V2 has not been materialized for this run")

    documents = list(db.scalars(select(SourceDocument).where(SourceDocument.run_id == run_id)))
    document_ids = [item.document_id for item in documents]
    snapshots = list(
        db.scalars(select(SourceSnapshot).where(SourceSnapshot.document_id.in_(document_ids)))
    ) if document_ids else []
    snapshot_ids = [item.snapshot_id for item in snapshots]
    passages = list(
        db.scalars(select(EvidencePassage).where(EvidencePassage.snapshot_id.in_(snapshot_ids)))
    ) if snapshot_ids else []
    passage_ids = [item.passage_id for item in passages]
    assertions = list(
        db.scalars(select(EvidenceAssertion).where(EvidenceAssertion.passage_id.in_(passage_ids)))
    ) if passage_ids else []
    claims = list(db.scalars(select(ResearchClaim).where(ResearchClaim.run_id == run_id)))
    claim_ids = [item.claim_id for item in claims]
    edges = list(
        db.scalars(select(ClaimEvidenceEdge).where(ClaimEvidenceEdge.claim_id.in_(claim_ids)))
    ) if claim_ids else []
    report_claims = list(db.scalars(select(ReportClaim).where(ReportClaim.run_id == run_id)))
    report_claim_ids = [item.report_claim_id for item in report_claims]
    citations = list(
        db.scalars(select(Citation).where(Citation.report_claim_id.in_(report_claim_ids)))
    ) if report_claim_ids else []

    citation_coverage = (
        sum(1 for claim in report_claims if any(c.report_claim_id == claim.report_claim_id for c in citations))
        / len(report_claims)
        if report_claims
        else 1.0
    )
    snapshot_ids_set = set(snapshot_ids)
    passage_ids_set = set(passage_ids)
    assertion_ids_set = {item.assertion_id for item in assertions}
    claim_ids_set = set(claim_ids)
    report_claim_ids_set = set(report_claim_ids)
    return {
        "run_id": run_id,
        "schema_version": pipeline.schema_version,
        "extractor_version": pipeline.extractor_version,
        "status": pipeline.status,
        "source_documents": [_document_dict(item) for item in documents],
        "source_snapshots": [_snapshot_dict(item) for item in snapshots],
        "passages": [_passage_dict(item) for item in passages],
        "assertions": [_assertion_dict(item) for item in assertions],
        "claims": [_claim_dict(item) for item in claims],
        "edges": [_edge_dict(item) for item in edges],
        "report_claims": [_report_claim_dict(item) for item in report_claims],
        "citations": [_citation_dict(item) for item in citations],
        "integrity": {
            "citation_count": len(citations),
            "report_claim_count": len(report_claims),
            "citation_coverage": round(citation_coverage, 6),
            "all_passages_resolve": all(
                passage.snapshot_id in snapshot_ids_set and bool(passage.trace_id)
                for passage in passages
            ),
            "all_assertions_resolve": all(
                assertion.passage_id in passage_ids_set and bool(assertion.trace_id)
                for assertion in assertions
            ),
            "all_edges_resolve": all(
                edge.claim_id in claim_ids_set and edge.assertion_id in assertion_ids_set
                for edge in edges
            ),
            "all_citations_resolve": all(
                citation.passage_id in passage_ids_set
                and citation.report_claim_id in report_claim_ids_set
                for citation in citations
            ),
        },
    }


def _materialize_item(
    run: AgentRun,
    item: EvidenceItem,
    ordinal: int,
    trace: ToolTrace | None,
    artifact_store: ArtifactStore,
    extractor_version: str,
    passage_max_chars: int,
) -> tuple[SourceDocument, SourceSnapshot, EvidencePassage, EvidenceAssertion]:
    canonical_uri = canonical_source_uri(item)
    document_id = _stable_id("doc", run.run_id, canonical_uri, item.title)
    snapshot_content = trace.output_json if trace and trace.output_json else item.snippet
    artifact = artifact_store.put_text(snapshot_content or "")
    snapshot_id = _stable_id("snap", document_id, artifact.content_hash)
    passage_text = item.snippet[:passage_max_chars]
    passage_hash = hashlib.sha256(passage_text.encode("utf-8")).hexdigest()
    passage_id = _stable_id("pass", snapshot_id, item.evidence_id, passage_hash)
    assertion_id = _stable_id("assert", passage_id, passage_hash)
    trace_input = _json_object(trace.input_json if trace else None)
    scalar = _extract_scalar(passage_text)

    document = SourceDocument(
        document_id=document_id,
        run_id=run.run_id,
        source_type=item.source_type,
        canonical_uri=canonical_uri,
        title=item.title,
        provider=source_provider(item),
        organization=source_organization(item, canonical_uri),
        metadata_json=_json_dump(
            {
                "v1_evidence_id": item.evidence_id,
                "tool_name": item.tool_name,
                "is_mock": item.is_mock,
                "is_fallback": item.is_fallback,
            }
        ),
    )
    snapshot = SourceSnapshot(
        snapshot_id=snapshot_id,
        document_id=document_id,
        trace_id=item.trace_id,
        content_hash=artifact.content_hash,
        artifact_path=artifact.artifact_path,
        content_type="application/json" if trace and trace.output_json else "text/plain",
        fetched_at=(trace.finished_at or trace.created_at) if trace else datetime.now(timezone.utc),
        extractor_version=extractor_version,
        metadata_json=_json_dump(
            {
                "size_bytes": artifact.size_bytes,
                "compressed_size_bytes": artifact.compressed_size_bytes,
            }
        ),
    )
    passage = EvidencePassage(
        passage_id=passage_id,
        snapshot_id=snapshot_id,
        trace_id=item.trace_id,
        ordinal=ordinal,
        content_hash=passage_hash,
        text=passage_text,
        locator_json=_json_dump(passage_locator(item, trace_input)),
        metadata_json=_json_dump(item.metadata),
    )
    assertion = EvidenceAssertion(
        assertion_id=assertion_id,
        passage_id=passage_id,
        trace_id=item.trace_id,
        subject=item.title,
        predicate="states",
        object_text=passage_text,
        value_json=_json_dump({"value": scalar[0]}) if scalar[0] is not None else None,
        unit=scalar[1],
        time_scope=_extract_time_scope(passage_text),
        qualifier_json=_json_dump({"source_type": item.source_type}),
        polarity=_polarity(passage_text, item.unsupported_reason),
        extraction_confidence=_confidence(item.confidence),
        extractor_version=extractor_version,
    )
    return document, snapshot, passage, assertion


def _research_claim(
    run_id: str,
    claim_map: ClaimEvidenceMap,
    ordinal: int,
    extractor_version: str,
) -> ResearchClaim:
    subject, predicate, object_text = _claim_parts(claim_map.claim)
    scalar = _extract_scalar(claim_map.claim)
    return ResearchClaim(
        claim_id=_stable_id("claim", run_id, str(ordinal), claim_map.claim),
        run_id=run_id,
        claim_text=claim_map.claim,
        normalized_subject=subject,
        normalized_predicate=predicate,
        normalized_object=object_text,
        value_json=_json_dump({"value": scalar[0]}) if scalar[0] is not None else None,
        unit=scalar[1],
        time_scope=_extract_time_scope(claim_map.claim),
        qualifier_json=_json_dump({"v1_claim_id": claim_map.claim_id, "notes": claim_map.notes}),
        status=claim_map.support_level,
        extractor_version=extractor_version,
    )


def _claim_parts(text: str) -> tuple[str | None, str, str]:
    normalized = " ".join(text.split())
    for separator in (":", "：", " - "):
        if separator in normalized:
            subject, object_text = normalized.split(separator, 1)
            return subject[:200] or None, "states", object_text[:1000]
    return None, "states", normalized[:1000]


def _extract_scalar(text: str) -> tuple[float | None, str | None]:
    match = re.search(
        r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*(%|percent|USD|CNY|RMB|亿元|万元|元)?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    return float(match.group(1)), match.group(2) or None


def _extract_time_scope(text: str) -> str | None:
    match = re.search(r"\b20\d{2}(?:[-/]\d{1,2}|\s*Q[1-4])?\b", text, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _polarity(text: str, unsupported_reason: str | None) -> str:
    if unsupported_reason:
        return "unknown"
    lowered = text.lower()
    return "negative" if any(term in lowered for term in (" not ", "no ", "decline", "decrease", "下降", "减少")) else "positive"


def _confidence(value: str) -> float:
    return {"high": 0.9, "medium": 0.6, "low": 0.35, "unsupported": 0.0}.get(value, 0.5)


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:48]}"


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_load(value: str | None) -> Any:
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _document_dict(item: SourceDocument) -> dict[str, Any]:
    return {
        "document_id": item.document_id,
        "source_type": item.source_type,
        "canonical_uri": item.canonical_uri,
        "title": item.title,
        "provider": item.provider,
        "organization": item.organization,
        "metadata": _json_load(item.metadata_json),
    }


def _snapshot_dict(item: SourceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": item.snapshot_id,
        "document_id": item.document_id,
        "trace_id": item.trace_id,
        "content_hash": item.content_hash,
        "artifact_path": item.artifact_path,
        "content_type": item.content_type,
        "fetched_at": item.fetched_at.isoformat(),
        "extractor_version": item.extractor_version,
        "metadata": _json_load(item.metadata_json),
    }


def _passage_dict(item: EvidencePassage) -> dict[str, Any]:
    return {
        "passage_id": item.passage_id,
        "snapshot_id": item.snapshot_id,
        "trace_id": item.trace_id,
        "ordinal": item.ordinal,
        "content_hash": item.content_hash,
        "text": item.text,
        "locator": _json_load(item.locator_json),
        "metadata": _json_load(item.metadata_json),
    }


def _assertion_dict(item: EvidenceAssertion) -> dict[str, Any]:
    return {
        "assertion_id": item.assertion_id,
        "passage_id": item.passage_id,
        "trace_id": item.trace_id,
        "subject": item.subject,
        "predicate": item.predicate,
        "object_text": item.object_text,
        "value": _json_load(item.value_json) if item.value_json else None,
        "unit": item.unit,
        "time_scope": item.time_scope,
        "qualifiers": _json_load(item.qualifier_json),
        "polarity": item.polarity,
        "extraction_confidence": item.extraction_confidence,
        "extractor_version": item.extractor_version,
    }


def _claim_dict(item: ResearchClaim) -> dict[str, Any]:
    return {
        "claim_id": item.claim_id,
        "claim_text": item.claim_text,
        "subject": item.normalized_subject,
        "predicate": item.normalized_predicate,
        "object": item.normalized_object,
        "value": _json_load(item.value_json) if item.value_json else None,
        "unit": item.unit,
        "time_scope": item.time_scope,
        "qualifiers": _json_load(item.qualifier_json),
        "status": item.status,
        "extractor_version": item.extractor_version,
    }


def _edge_dict(item: ClaimEvidenceEdge) -> dict[str, Any]:
    return {
        "edge_id": item.edge_id,
        "claim_id": item.claim_id,
        "assertion_id": item.assertion_id,
        "relation": item.relation,
        "score": item.score,
        "rationale": item.rationale,
    }


def _report_claim_dict(item: ReportClaim) -> dict[str, Any]:
    return {
        "report_claim_id": item.report_claim_id,
        "claim_id": item.claim_id,
        "claim_text": item.claim_text,
        "section": item.section,
        "ordinal": item.ordinal,
        "origin": item.origin,
    }


def _citation_dict(item: Citation) -> dict[str, Any]:
    return {
        "citation_id": item.citation_id,
        "report_claim_id": item.report_claim_id,
        "passage_id": item.passage_id,
        "edge_id": item.edge_id,
        "citation_label": item.citation_label,
    }
