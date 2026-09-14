from sqlalchemy import func, select

from app.evidence.citation_validator import materialize_final_report_occurrences
from app.evidence.models import CitationOccurrence, ReportClaimOccurrence
from app.evidence.scope_service import get_scope_provenance_bundle
from app.reporting.claim_occurrence import segment_final_answer_claims

from .conftest import create_root
from .test_scope_evidence import _scope_with_parent_and_child


def test_uncited_factual_sentence_materializes_claim_without_citation(db):
    root = create_root(db)
    markdown = "# Report\n\n## 3. 最终回答\n\n市场规模为 100 亿美元。\n\n## 4. 审计\n"

    bundle = materialize_final_report_occurrences(
        db,
        root_run_id=root.run_id,
        markdown=markdown,
        provenance_bundle={},
        report_path="workspace/reports/uncited.md",
    )

    assert len(bundle["claim_occurrences"]) == 1
    assert bundle["claim_occurrences"][0]["claim_text"] == "市场规模为 100 亿美元。"
    assert bundle["claim_occurrences"][0]["citation_count"] == 0
    assert bundle["citation_occurrences"] == []
    assert db.scalar(select(func.count()).select_from(ReportClaimOccurrence)) == 1
    assert db.scalar(select(func.count()).select_from(CitationOccurrence)) == 0


def test_cited_and_uncited_sentences_both_materialize(db, r12_settings):
    root, _, scope, _, _ = _scope_with_parent_and_child(db, r12_settings)
    provenance = get_scope_provenance_bundle(db, scope)
    citation = provenance["citations"][0]
    passage = next(
        item
        for item in provenance["passages"]
        if item["passage_id"] == citation["passage_id"]
    )
    markdown = (
        "# Report\n\n## 3. 最终回答\n\n"
        f"{passage['text']} [{citation['citation_label']}]\n"
        "市场规模为 100 亿美元。\n\n"
        "## 4. 审计\n"
    )

    bundle = materialize_final_report_occurrences(
        db,
        root_run_id=root.run_id,
        scope_id=scope.scope_id,
        markdown=markdown,
        provenance_bundle=provenance,
        report_path="workspace/reports/mixed.md",
    )

    assert len(bundle["claim_occurrences"]) == 2
    assert len(bundle["citation_occurrences"]) == 1
    assert [item["citation_count"] for item in bundle["claim_occurrences"]] == [1, 0]


def test_segmentation_excludes_headings_links_and_code_blocks():
    final_answer = (
        "### 主要来源\n"
        "* [Source](https://example.com/a)\n"
        "```python\n"
        "value = 100\n"
        "```\n"
        "- 第一项事实为 10。第二项事实为 20！\n"
        "No punctuation English factual line\n"
    )

    candidates = [
        span
        for span in segment_final_answer_claims(final_answer)
        if span.is_claim_candidate
    ]

    assert [span.claim_text for span in candidates] == [
        "第一项事实为 10。",
        "第二项事实为 20！",
        "No punctuation English factual line",
    ]
    assert all(
        final_answer[span.sentence_start : span.sentence_end] == span.raw_text
        for span in candidates
    )


def test_standalone_citation_attaches_to_previous_claim(db):
    root = create_root(db)
    markdown = (
        "# Report\n\n## 3. 最终回答\n\n"
        "Market size reached $100.\n\n[CIT-001-01]\n\n## 4. 审计\n"
    )
    provenance = {
        "passages": [{"passage_id": "pass-1", "text": "Market size reached $100."}],
        "citations": [{"citation_label": "CIT-001-01", "passage_id": "pass-1"}],
    }
    bundle = materialize_final_report_occurrences(
        db, root_run_id=root.run_id, markdown=markdown,
        provenance_bundle=provenance, report_path="workspace/reports/standalone.md",
    )
    assert len(bundle["claim_occurrences"]) == 1
    assert len(bundle["citation_occurrences"]) == 1
    assert bundle["citation_occurrences"][0]["claim_occurrence_id"] == bundle["claim_occurrences"][0]["claim_occurrence_id"]
    assert len(bundle["claim_occurrences"][0]["citations"]) == 1


def test_standalone_citation_attaches_only_to_nearest_previous_claim(db):
    root = create_root(db)
    markdown = (
        "# Report\n\n## 3. 最终回答\n\n"
        "The first measured value was 10.\n"
        "The second measured value was 20.\n\n"
        "[CIT-001-01]\n\n## 4. 审计\n"
    )
    provenance = {
        "passages": [{"passage_id": "pass-1", "text": "The second measured value was 20."}],
        "citations": [{"citation_label": "CIT-001-01", "passage_id": "pass-1"}],
    }
    bundle = materialize_final_report_occurrences(
        db, root_run_id=root.run_id, markdown=markdown,
        provenance_bundle=provenance, report_path="workspace/reports/standalone-nearest.md",
    )
    assert len(bundle["claim_occurrences"]) == 2
    assert [item["citation_count"] for item in bundle["claim_occurrences"]] == [0, 1]
    assert bundle["citation_occurrences"][0]["claim_occurrence_id"] == bundle["claim_occurrences"][1]["claim_occurrence_id"]
