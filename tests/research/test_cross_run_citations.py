from app.evidence.citation_validator import validate_scope_citations
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
