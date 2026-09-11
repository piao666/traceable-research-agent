import json
from unittest.mock import patch

from app.agent.outcome import report_block_reason, result_integrity, trusted_run_ids
from app.agent.outcome import load_observations
from app.agent.reporter import generate_markdown_report
from app.evidence.service import materialize_execution_provenance
from app.eval.fake_react_llm import FakeReActLLMClient
from app.reporting.integrity import (
    REPORT_INTEGRITY_VERSION,
    append_report_integrity_warnings,
    assess_report_integrity,
)
from app.research.orchestrator import run_deep_research_v2
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
