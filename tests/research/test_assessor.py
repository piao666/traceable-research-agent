from app.research.assessor import assess_requirements, persist_shadow_assessment
from app.research.models import EvidenceGapRecord

from .conftest import create_root


def _contract(**extra):
    payload = {
        "requirements": [
            {
                "requirement_id": "r1",
                "question_id": "q1",
                "kind": "fact",
                "predicate": "reduced latency",
                "required": True,
                "min_independent_sources": 1,
                "acceptable_content_basis": ["full_text"],
            }
        ]
    }
    payload.update(extra)
    return payload


def test_shadow_assessor_never_passes_unmapped_requirement() -> None:
    result = assess_requirements(_contract(), {"sources": []})
    assert result["external_calls"] == 0
    assert result["mutated_run"] is False
    assert result["complete"] is False
    assert result["requirements"][0]["status"] == "uncovered"
    assert result["gaps"][0]["type"] == "missing_mapping"


def test_metadata_source_is_context_only_for_substantive_requirement() -> None:
    result = assess_requirements(
        _contract(
            requirement_claim_links=[
                {"requirement_id": "r1", "claim_occurrence_id": "claim-1", "source_id": "s1"}
            ]
        ),
        {
            "sources": [
                {
                    "source_id": "s1",
                    "fetch_status": "fetched",
                    "content_basis": "full_text",
                    "evidence_role": "discovery_index",
                    "url": "https://example.com/paper",
                }
            ]
        },
    )
    assert result["complete"] is False
    assert result["requirements"][0]["status"] == "partial"
    assert result["gaps"][0]["type"] == "unknown_evidence_role"


def test_explicit_content_source_can_satisfy_mapped_requirement() -> None:
    result = assess_requirements(
        _contract(
            requirement_claim_links=[
                {"requirement_id": "r1", "claim_occurrence_id": "claim-1", "source_id": "s1"}
            ]
        ),
        {
            "sources": [
                {
                    "source_id": "s1",
                    "fetch_status": "fetched",
                    "content_basis": "full_text",
                    "evidence_role": "primary_content",
                    "independence_group": "publisher-a",
                    "reliability_score": 0.9,
                    "url": "https://example.com/paper",
                }
            ]
        },
    )
    assert result["complete"] is True
    assert result["requirements"][0]["status"] == "satisfied"


def test_scope_conflict_is_a_distinct_requirement_state() -> None:
    result = assess_requirements(
        _contract(
            requirement_claim_links=[
                {"requirement_id": "r1", "scope_group_id": "group-1", "source_id": "s1"}
            ]
        ),
        {
            "sources": [
                {
                    "source_id": "s1",
                    "fetch_status": "fetched",
                    "content_basis": "full_text",
                    "evidence_role": "primary_content",
                    "independence_group": "publisher-a",
                    "url": "https://example.com/paper",
                }
            ]
        },
        scope_evidence={"claim_groups": [{"group_id": "group-1", "status": "requires_human"}]},
    )
    assert result["complete"] is False
    assert result["requirements"][0]["status"] == "conflicted"
    assert result["gaps"][0]["type"] == "conflict"


def test_shadow_assessment_persistence_is_idempotent_and_does_not_change_run(db) -> None:
    root = create_root(db)
    before = (root.status, root.error_message)
    result = assess_requirements(_contract(), {"sources": []})
    first = persist_shadow_assessment(db, root_run_id=root.run_id, result=result)
    second = persist_shadow_assessment(db, root_run_id=root.run_id, result=result)
    db.refresh(root)
    assert first.snapshot_id == second.snapshot_id
    assert db.query(EvidenceGapRecord).filter_by(snapshot_id=first.snapshot_id).count() == 1
    assert (root.status, root.error_message) == before
