from sqlalchemy import func, select

from app.evidence.citation_validator import materialize_final_report_occurrences
from app.evidence.citation_validator import validate_scope_citations
from app.evidence.models import CitationOccurrence, ReportClaimOccurrence, ReportRevision
from app.evidence.scope_service import get_scope_provenance_bundle

from .test_scope_evidence import _scope_with_parent_and_child


def test_final_text_can_cite_child_passage_and_resolve_child_trace(db, r12_settings):
    _, child, scope, _, child_trace = _scope_with_parent_and_child(db, r12_settings)
    bundle = get_scope_provenance_bundle(db, scope)
    citation = next(item for item in bundle["citations"] if item["origin_run_id"] == child.run_id)
    passage = next(item for item in bundle["passages"] if item["passage_id"] == citation["passage_id"])
    report = f"{passage['text']} [{citation['citation_label']}]"
    validation = validate_scope_citations(
        report,
        bundle,
        min_supported_overlap=0.0,
        min_entity_co_occurrence=0,
    )
    assert validation.total == 1
    assert validation.unsupported == 0
    assert citation["origin_trace_id"] == child_trace.trace_id


def test_final_report_occurrences_keep_child_lineage_and_are_idempotent(db, r12_settings):
    root, child, scope, _, child_trace = _scope_with_parent_and_child(db, r12_settings)
    bundle = get_scope_provenance_bundle(db, scope)
    citation = next(item for item in bundle["citations"] if item["origin_run_id"] == child.run_id)
    passage = next(item for item in bundle["passages"] if item["passage_id"] == citation["passage_id"])
    label = citation["citation_label"]
    markdown = (
        "# Scope report\n\n"
        "## 3. 最终回答\n\n"
        f"{passage['text']} [{label}].\n\n"
        f"Unrelated weather statement [{label}].\n\n"
        "## 9. 引用索引\n\n"
        f"[{label}] must not become a final claim occurrence."
    )

    first = materialize_final_report_occurrences(
        db,
        root_run_id=root.run_id,
        scope_id=scope.scope_id,
        markdown=markdown,
        provenance_bundle=bundle,
        report_path="workspace/reports/scope.md",
    )
    second = materialize_final_report_occurrences(
        db,
        root_run_id=root.run_id,
        scope_id=scope.scope_id,
        markdown=markdown,
        provenance_bundle=bundle,
        report_path="workspace/reports/scope.md",
    )

    assert first == second
    assert len(first["claim_occurrences"]) == 2
    assert len(first["citation_occurrences"]) == 2
    assert all(
        len(item["claim_occurrence_id"]) <= 64
        for item in first["claim_occurrences"]
    )
    assert all(
        len(item["citation_occurrence_id"]) <= 64
        for item in first["citation_occurrences"]
    )
    assert first["claim_occurrences"][0]["claim_text"] == passage["text"]
    assert {item["verdict"] for item in first["citation_occurrences"]} == {
        "supported",
        "unsupported",
    }
    assert {item["origin_run_id"] for item in first["citation_occurrences"]} == {
        child.run_id
    }
    assert {item["origin_trace_id"] for item in first["citation_occurrences"]} == {
        child_trace.trace_id
    }
    assert db.scalar(select(func.count()).select_from(ReportRevision)) == 1
    assert db.scalar(select(func.count()).select_from(ReportClaimOccurrence)) == 2
    assert db.scalar(select(func.count()).select_from(CitationOccurrence)) == 2
