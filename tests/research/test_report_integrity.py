import json
from unittest.mock import patch

from app.agent.outcome import report_block_reason, result_integrity, trusted_run_ids
from app.agent.outcome import load_observations
from app.agent.reporter import generate_markdown_report
from app.evidence.citation_validator import (
    CitationValidationDetail,
    CitationValidationReport,
)
from app.evidence.service import materialize_execution_provenance
from app.evidence.reference_verifier import ReferenceVerificationReport
from app.eval.fake_react_llm import FakeReActLLMClient
from app.reporting.integrity import (
    REPORT_INTEGRITY_VERSION,
    append_report_integrity_warnings,
    assess_report_integrity,
)
from app.research.orchestrator import (
    _requires_strict_reference_gate,
    _validation_occurrence_preview,
    run_deep_research_v2,
)
from app.research.scope import resolve_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root


def _occurrences(*verdicts: str, unresolved_at: int | None = None) -> dict:
    return {
        "citation_occurrences": [
            {
                "citation_occurrence_id": f"occ-{index}",
                "passage_id": None if index == unresolved_at else f"pass-{index}",
                "verdict": verdict,
            }
            for index, verdict in enumerate(verdicts)
        ]
    }


def test_zero_citations_fails_report_gate():
    result = assess_report_integrity(_occurrences())
    assert result.version == REPORT_INTEGRITY_VERSION
    assert result.status == "failed"
    assert result.error_code == "no_citation_occurrences"


def test_explicit_empty_claim_universe_fails_with_claim_error():
    result = assess_report_integrity(
        {"claim_occurrences": [], "citation_occurrences": []}
    )

    assert result.status == "failed"
    assert result.error_code == "no_final_claim_occurrences"
    assert result.claim_total == 0


def test_claim_coverage_metrics_include_uncited_claims():
    result = assess_report_integrity(
        {
            "claim_occurrences": [
                {"claim_text": "Supported claim.", "citation_count": 1},
                {"claim_text": "A further observation.", "citation_count": 0},
            ],
            "citation_occurrences": [
                {"passage_id": "pass-1", "verdict": "supported"}
            ],
        }
    )

    assert result.status == "passed"
    assert result.claim_total == 2
    assert result.claim_with_citation == 1
    assert result.claim_without_citation == 1
    assert result.claim_citation_coverage_rate == 0.5
    assert "require review" in result.warnings[-1]


def test_uncited_numeric_claim_fails_without_weakening_citation_gate():
    result = assess_report_integrity(
        {
            "claim_occurrences": [
                {"claim_text": "Market size reached 100 USD.", "citation_count": 0}
            ],
            "citation_occurrences": [],
        }
    )

    assert result.status == "failed"
    assert result.error_code == "uncited_deterministic_claim"


def test_five_percent_unsupported_passes_report_gate():
    result = assess_report_integrity(
        _occurrences(*(["supported"] * 19), "unsupported")
    )
    assert result.status == "passed"
    assert result.support_rate == 0.95


def test_twenty_percent_unsupported_fails_report_gate():
    result = assess_report_integrity(
        _occurrences(*(["supported"] * 8), "unsupported", "unsupported")
    )
    assert result.status == "failed"
    assert result.error_code == "unsupported_citation_rate_exceeded"


def test_unresolved_citation_target_fails_report_gate():
    result = assess_report_integrity(
        _occurrences("supported", "supported", unresolved_at=1)
    )
    assert result.status == "failed"
    assert result.error_code == "unresolved_citation_target"


def test_low_strict_support_rate_is_warning_only():
    result = assess_report_integrity(
        _occurrences(
            *(["supported"] * 5),
            *(["weakly_supported"] * 5),
        )
    )
    assert result.status == "passed"
    assert result.support_rate == 1.0
    assert result.strict_support_rate == 0.5
    assert result.warnings
    markdown = append_report_integrity_warnings("# Report", result)
    assert "## 13. 报告完整性警告" in markdown
    assert result.warnings[0] in markdown


def test_systematic_review_inconsistent_cited_work_fails():
    references = ReferenceVerificationReport(
        total=1,
        inconsistent=1,
    )
    result = assess_report_integrity(
        _occurrences("supported"),
        reference_report=references,
        enforce_reference_consistency=True,
    )
    assert result.status == "failed"
    assert result.error_code == "reference_metadata_inconsistent"


def test_normal_web_reference_problems_are_warning_only():
    references = ReferenceVerificationReport(
        total=2,
        inconsistent=1,
        unresolved=1,
        network_failures=1,
    )
    result = assess_report_integrity(
        _occurrences("supported"),
        reference_report=references,
        enforce_reference_consistency=False,
    )
    assert result.status == "passed"
    assert len(result.warnings) == 2


def test_deterministic_claim_mapped_to_open_scope_conflict_fails():
    from app.evidence.scope_reasoning import scope_claim_group_key

    claim_text = "Market size in 2025 is 100 USD."
    for conflict_status in ("unresolved", "requires_human"):
        scope_bundle = {
            "scope_claim_groups": [
                {
                    "group_id": "group-1",
                    "normalized_key": scope_claim_group_key({"claim_text": claim_text}),
                }
            ],
            "scope_resolutions": [
                {"group_id": "group-1", "status": conflict_status}
            ],
        }
        occurrences = _occurrences("supported")
        occurrences["claim_occurrences"] = [
            {"claim_text": claim_text, "citation_count": 0}
        ]

        result = assess_report_integrity(occurrences, scope_bundle=scope_bundle)

        assert result.status == "failed"
        assert result.error_code == "unresolved_scope_claim_asserted"
        assert "deterministic final claim" in result.warnings[-1]


def test_uncertain_claim_mapped_to_unresolved_scope_group_is_not_asserted():
    from app.evidence.scope_reasoning import scope_claim_group_key

    qualified_claim = "Market size in 2025 may be 100 USD."
    scope_bundle = {
        "scope_claim_groups": [
            {
                "group_id": "group-1",
                "normalized_key": scope_claim_group_key({"claim_text": qualified_claim}),
            }
        ],
        "scope_resolutions": [
            {"group_id": "group-1", "status": "unresolved"}
        ],
    }
    occurrences = _occurrences("supported")
    occurrences["claim_occurrences"] = [
        {"claim_text": qualified_claim, "citation_count": 0}
    ]

    result = assess_report_integrity(occurrences, scope_bundle=scope_bundle)

    assert result.status == "passed"


def test_paraphrased_claim_maps_to_conflict_by_citation_lineage():
    final_answer = "The 2025 market was worth $100. [CIT-001-01]"
    validation = CitationValidationReport(
        occurrence_total=1,
        unique_citation_count=1,
        supported_occurrences=1,
        details=[
            CitationValidationDetail(
                citation_label="CIT-001-01",
                verdict="supported",
                sentence=final_answer,
                passage_text="Market size in 2025 is 100 USD.",
                keyword_overlap=0.5,
                marker_start=final_answer.index("CIT-001-01"),
                marker_end=final_answer.index("CIT-001-01") + len("CIT-001-01"),
                sentence_start=0,
                sentence_end=len(final_answer),
            )
        ],
    )
    scope_bundle = {
        "report_claims": [{"report_claim_id": "report-1", "claim_id": "claim-1"}],
        "citations": [
            {"citation_label": "CIT-001-01", "report_claim_id": "report-1"}
        ],
        "scope_claim_groups": [
            {
                "group_id": "group-1",
                "normalized_key": "different-from-final-paraphrase",
                "members": [{"claim_id": "claim-1"}],
            }
        ],
        "scope_resolutions": [{"group_id": "group-1", "status": "unresolved"}],
    }

    preview = _validation_occurrence_preview(
        validation,
        final_answer,
        scope_bundle,
    )
    result = assess_report_integrity(preview, scope_bundle=scope_bundle)

    assert preview["claim_occurrences"][0]["scope_group_ids"] == ["group-1"]
    assert preview["claim_occurrences"][0]["mapping_source"] == "citation_lineage"
    assert result.status == "failed"
    assert result.error_code == "unresolved_scope_claim_asserted"


def test_strict_reference_gate_is_limited_to_academic_reviews():
    assert _requires_strict_reference_gate({"skill_name": "systematic_review"})
    assert _requires_strict_reference_gate(
        {"retrieval_profile": "academic_literature"}
    )
    assert not _requires_strict_reference_gate({"retrieval_profile": "generic"})


def test_validator_exception_fails_deep_v2_and_keeps_audit_report(
    db,
    r12_settings,
):
    root = create_root(db)

    def fake_root_runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        add_web_trace(session, run_id, "Verified evidence for report integrity.", "root")
        traces = store.list_tool_traces(session, run_id)
        materialize_execution_provenance(
            session,
            run,
            json.loads(run.plan_json or "{}"),
            load_observations(traces),
            traces,
            settings,
        )
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    def report_generator(_run, _plan, _observations, _traces, **kwargs):
        bundle = kwargs["provenance_bundle"]
        citation = bundle["citations"][0]
        passage = next(
            item
            for item in bundle["passages"]
            if item["passage_id"] == citation["passage_id"]
        )
        return (
            "# Scope report\n\n## 3. 最终回答\n\n"
            f"{passage['text']} [{citation['citation_label']}]"
        )

    with (
        patch("app.research.orchestrator.run_react_task", side_effect=fake_root_runner),
        patch(
            "app.research.orchestrator.validate_scope_citations",
            side_effect=RuntimeError("validator unavailable"),
        ),
        patch(
            "app.research.orchestrator.save_report",
            return_value="workspace/reports/audit.md",
        ),
    ):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=lambda *_args, **_kwargs: {
                "branches": [],
                "is_comprehensive": True,
            },
            report_generator=report_generator,
        )

    run = store.get_agent_run(db, root.run_id)
    plan = json.loads(run.plan_json)
    assert result["status"] == "failed"
    assert run.report_path == "workspace/reports/audit.md"
    assert plan["research_outcome"]["status"] == "passed"
    assert plan["report_integrity"]["status"] == "failed"
    assert plan["report_integrity"]["error_code"] == "citation_validation_failed"
    assert resolve_research_scope(db, root.run_id).status == "failed"
    assert report_block_reason(run)


def test_legacy_planned_validator_exception_remains_warning_only(db):
    run = create_root(db)
    plan = {"execution_mode": "planned", "steps": []}
    provenance = {
        "passages": [{"passage_id": "p1", "text": "Verified evidence."}],
        "citations": [{"citation_label": "CIT-001-01", "passage_id": "p1"}],
    }
    with patch(
        "app.evidence.citation_validator.validate_citations",
        side_effect=RuntimeError("validator unavailable"),
    ) as validator:
        markdown = generate_markdown_report(
            run,
            plan,
            [],
            [],
            provenance_bundle=provenance,
        )
    assert validator.called
    assert "## 3. 最终回答" in markdown


def test_deep_v2_trust_and_citation_metrics_require_both_gates(db):
    run = create_root(db)
    plan = json.loads(run.plan_json)
    plan.update(
        {
            "execution_mode": "deep_research_v2",
            "research_outcome": {
                "version": "research-scope-outcome-v2",
                "status": "passed",
                "effective_evidence_count": 2,
            },
            "report_integrity": {
                "version": REPORT_INTEGRITY_VERSION,
                "status": "failed",
                "error_code": "unsupported_citation_rate_exceeded",
                "warnings": [],
                "metrics": {},
            },
        }
    )
    store.replace_agent_run_plan(db, run.run_id, plan)
    store.update_agent_run_citation_validation(
        db,
        run.run_id,
        total=10,
        supported=8,
        weakly_supported=0,
        unsupported=2,
        accuracy=0.8,
    )
    run = store.update_agent_run_status(db, run.run_id, "completed", None)

    assert result_integrity(run)["citation_evaluated"] is False
    assert run.run_id not in set(db.scalars(trusted_run_ids()))
    assert report_block_reason(run)

    plan["report_integrity"] = {
        "version": REPORT_INTEGRITY_VERSION,
        "status": "passed",
        "error_code": None,
        "warnings": ["Strictly supported final citation occurrences are below 60%."],
        "metrics": {
            "occurrence_total": 10,
            "supported": 5,
            "weakly_supported": 5,
            "unsupported": 0,
            "support_rate": 1.0,
            "strict_support_rate": 0.5,
        },
    }
    store.replace_agent_run_plan(db, run.run_id, plan)
    run = store.get_agent_run(db, run.run_id)
    integrity = result_integrity(run)
    assert integrity["citation_evaluated"] is True
    assert integrity["quality_warnings"] == plan["report_integrity"]["warnings"]
    assert run.run_id in set(db.scalars(trusted_run_ids()))
    assert report_block_reason(run) is None
