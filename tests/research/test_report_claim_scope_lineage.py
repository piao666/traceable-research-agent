from sqlalchemy import delete, func, select, text

from app.evidence.citation_validator import (
    CitationValidationDetail,
    CitationValidationReport,
    get_report_occurrence_bundle,
    materialize_final_report_occurrences,
)
from app.evidence.models import (
    ReportClaimOccurrence,
    ReportClaimScopeGroupLink,
    ResearchClaim,
    ScopeClaimGroup,
    ScopeClaimMember,
    ScopeReasoningRun,
)
from app.evidence.scope_reasoning import scope_claim_group_key
from app.research.orchestrator import _validation_occurrence_preview
from app.research.scope import create_research_scope

from .conftest import create_root


def _scope_fixture(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    reasoning = ScopeReasoningRun(
        reasoning_run_id="reasoning-lineage", scope_id=scope.scope_id,
        policy_version="test", policy_hash="policy-hash",
        evidence_fingerprint="evidence-hash", engine_version="scope-reasoning-v1",
        status="complete",
    )
    specs = [
        ("claim-market", "group-market", "Market size in 2025 is 100 USD."),
        ("claim-growth", "group-growth", "Annual revenue growth was 12 percent."),
    ]
    db.add(reasoning)
    groups, claims = [], []
    for claim_id, group_id, claim_text in specs:
        db.add(ResearchClaim(
            claim_id=claim_id, run_id=root.run_id, claim_text=claim_text,
            normalized_subject=None, normalized_predicate="states",
            normalized_object=claim_text, value_json=None, unit=None, time_scope=None,
            qualifier_json="{}", status="supported", extractor_version="test",
        ))
        group = ScopeClaimGroup(
            group_id=group_id, reasoning_run_id=reasoning.reasoning_run_id,
            normalized_key=scope_claim_group_key({"claim_text": claim_text}),
            representative_claim_text=claim_text, unit=None, time_scope=None,
        )
        db.add(group)
        db.add(ScopeClaimMember(
            member_id=f"member-{claim_id}", group_id=group_id,
            origin_run_id=root.run_id, claim_id=claim_id,
        ))
        claims.append({"claim_id": claim_id, "claim_text": claim_text})
        groups.append({
            "group_id": group_id, "normalized_key": group.normalized_key,
            "members": [{"claim_id": claim_id}],
        })
    db.commit()
    return root, scope, claims, groups


def _validation(final_answer: str, *labels: str) -> CitationValidationReport:
    details = []
    for label in labels:
        marker_start = final_answer.index(label)
        details.append(CitationValidationDetail(
            citation_label=label, verdict="supported", sentence=final_answer,
            passage_text="Verified passage.", keyword_overlap=0.8,
            marker_start=marker_start, marker_end=marker_start + len(label),
            sentence_start=0, sentence_end=len(final_answer),
        ))
    return CitationValidationReport(
        occurrence_total=len(labels), unique_citation_count=len(set(labels)),
        supported_occurrences=len(labels), details=details,
    )


def test_citation_lineage_survives_materialization_and_database_reload(db):
    root, scope, claims, groups = _scope_fixture(db)
    final_answer = "The 2025 market was worth $100. [CIT-001-01]"
    validation = _validation(final_answer, "CIT-001-01")
    scope_bundle = {
        "claims": claims,
        "report_claims": [{"report_claim_id": "report-market", "claim_id": "claim-market"}],
        "citations": [{"citation_label": "CIT-001-01", "report_claim_id": "report-market"}],
        "scope_claim_groups": groups,
    }
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    bundle = materialize_final_report_occurrences(
        db, root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/lineage.md",
        validation_report=validation, occurrence_preview=preview,
    )
    reloaded = get_report_occurrence_bundle(db, bundle["report_revision"]["report_revision_id"])
    assert preview["claim_occurrences"][0]["scope_group_ids"] == ["group-market"]
    assert reloaded["claim_occurrences"][0]["scope_group_ids"] == ["group-market"]
    assert reloaded["claim_occurrences"][0]["scope_group_mapping_sources"] == ["citation_lineage"]


def test_uncited_text_fallback_lineage_is_persisted(db):
    root, scope, _claims, groups = _scope_fixture(db)
    final_answer = "Market size in 2025 is 100 USD."
    validation = _validation(final_answer)
    scope_bundle = {"claims": [], "report_claims": [], "citations": [], "scope_claim_groups": groups}
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    bundle = materialize_final_report_occurrences(
        db, root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/text-fallback.md",
        validation_report=validation, occurrence_preview=preview,
    )
    claim = bundle["claim_occurrences"][0]
    assert claim["scope_group_ids"] == ["group-market"]
    assert claim["scope_group_mapping_sources"] == ["text_fallback"]
    assert claim["citation_count"] == 0


def test_exact_uncited_research_claim_uses_member_lineage_before_text_fallback(db):
    root, scope, claims, groups = _scope_fixture(db)
    final_answer = "Market size in 2025 is 100 USD."
    validation = _validation(final_answer)
    scope_bundle = {
        "claims": claims,
        "report_claims": [],
        "citations": [],
        "scope_claim_groups": groups,
    }
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    bundle = materialize_final_report_occurrences(
        db, root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/member-lineage.md",
        validation_report=validation, occurrence_preview=preview,
    )
    claim = bundle["claim_occurrences"][0]
    assert claim["scope_group_ids"] == ["group-market"]
    assert claim["scope_group_mapping_sources"] == ["claim_member_lineage"]


def test_one_final_claim_persists_all_citation_scope_groups_idempotently(db):
    root, scope, claims, groups = _scope_fixture(db)
    final_answer = "The market was valued at $100 and revenue grew 12%. [CIT-001-01] [CIT-002-01]"
    validation = _validation(final_answer, "CIT-001-01", "CIT-002-01")
    scope_bundle = {
        "claims": claims,
        "report_claims": [
            {"report_claim_id": "report-market", "claim_id": "claim-market"},
            {"report_claim_id": "report-growth", "claim_id": "claim-growth"},
        ],
        "citations": [
            {"citation_label": "CIT-001-01", "report_claim_id": "report-market"},
            {"citation_label": "CIT-002-01", "report_claim_id": "report-growth"},
        ],
        "scope_claim_groups": groups,
    }
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    kwargs = dict(
        root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/multi-lineage.md",
        validation_report=validation, occurrence_preview=preview,
    )
    first = materialize_final_report_occurrences(db, **kwargs)
    second = materialize_final_report_occurrences(db, **kwargs)
    expected = ["group-growth", "group-market"]
    assert first["claim_occurrences"][0]["scope_group_ids"] == expected
    assert second["claim_occurrences"][0]["scope_group_ids"] == expected
    assert db.scalar(select(func.count()).select_from(ReportClaimScopeGroupLink)) == 2


def test_existing_complete_revision_backfills_missing_scope_lineage(db):
    root, scope, _claims, groups = _scope_fixture(db)
    final_answer = "Market size in 2025 is 100 USD."
    validation = _validation(final_answer)
    scope_bundle = {"claims": [], "report_claims": [], "citations": [], "scope_claim_groups": groups}
    kwargs = dict(
        root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/backfill.md",
        validation_report=validation,
    )
    first = materialize_final_report_occurrences(db, **kwargs)
    assert first["claim_occurrences"][0]["scope_group_ids"] == []
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    second = materialize_final_report_occurrences(db, occurrence_preview=preview, **kwargs)
    assert second["claim_occurrences"][0]["scope_group_ids"] == ["group-market"]


def test_deleting_report_claim_occurrence_cascades_scope_links(db):
    db.execute(text("PRAGMA foreign_keys=ON"))
    root, scope, _claims, groups = _scope_fixture(db)
    final_answer = "Market size in 2025 is 100 USD."
    validation = _validation(final_answer)
    scope_bundle = {"claims": [], "report_claims": [], "citations": [], "scope_claim_groups": groups}
    preview = _validation_occurrence_preview(validation, final_answer, scope_bundle)
    materialize_final_report_occurrences(
        db, root_run_id=root.run_id, scope_id=scope.scope_id,
        markdown=f"# Report\n\n## 3. 最终回答\n\n{final_answer}\n",
        provenance_bundle=scope_bundle, report_path="workspace/reports/cascade.md",
        validation_report=validation, occurrence_preview=preview,
    )
    claim_id = db.scalar(select(ReportClaimOccurrence.claim_occurrence_id))
    db.execute(delete(ReportClaimOccurrence).where(ReportClaimOccurrence.claim_occurrence_id == claim_id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(ReportClaimScopeGroupLink)) == 0
