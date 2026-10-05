"""Independent acceptance of report/evidence/terminal agreement."""
import json
from pathlib import Path

import pytest

from app.agent.executor import _persist_final_report_gate
from app.agent.outcome import (
    assess_research_outcome, finalize_terminal_decision, load_observations,
    report_block_reason,
)
from app.agent.reporter import save_report
from app.agent.report_exporter import resolve_report_path
from app.evidence.citation_validator import validate_scope_citations
from app.evidence.service import materialize_execution_provenance
from app.trace import store
from app.tools.base import ToolResult
from .conftest import add_web_trace, create_root


@pytest.fixture
def supported_run(db, r12_settings):
    run = create_root(db, "Explain the documented retry behavior")
    run = store.mark_agent_run_running_unless_cancelled(db, run.run_id)
    add_web_trace(db, run.run_id, "The client retries transient server errors with a bounded retry policy.", "retry")
    plan = json.loads(run.plan_json)
    traces = store.list_tool_traces(db, run.run_id)
    observations = load_observations(traces)
    evidence = materialize_execution_provenance(db, run, plan, observations, traces, r12_settings)
    citation = evidence["citations"][0]
    passage = next(p for p in evidence["passages"] if p["passage_id"] == citation["passage_id"])
    markdown = f"# Research\n\n## 3. 最终回答\n\n{passage['text']} [{citation['citation_label']}]\n"
    report_path = save_report(run.run_id, markdown)
    run = store.update_agent_run_report(db, run.run_id, report_path)
    validation = validate_scope_citations(markdown, evidence, min_supported_overlap=0.15, min_weak_overlap=0.05)
    store.update_agent_run_citation_validation(db, run.run_id, total=validation.total,
        supported=validation.supported, weakly_supported=validation.weakly_supported,
        unsupported=validation.unsupported, accuracy=validation.accuracy)
    plan["research_outcome"] = assess_research_outcome(run, plan, observations, traces, r12_settings)
    _persist_final_report_gate(db, run, plan, markdown, evidence, report_path, [validation])
    try:
        yield run, plan, traces
    finally:
        Path(resolve_report_path(report_path)).unlink(missing_ok=True)


def test_supported_content_can_complete_with_persisted_report_revision(db, supported_run):
    run, plan, traces = supported_run
    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    assert decision["status"] == "completed", decision
    assert decision["report_revision_id"]
    assert decision["report_sha256"]
    assert store.get_fresh_agent_run(db, run.run_id).status == "completed"


def test_unadopted_report_draft_cannot_complete_even_when_final_citations_pass(db, supported_run):
    run, plan, traces = supported_run
    plan["report_draft_result"] = {
        "integrity": "incomplete", "adopted": False,
        "diagnostics": [{"code": "repeated_report_draft"}],
    }
    store.replace_agent_run_plan(db, run.run_id, plan)
    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    assert decision["status"] == "incomplete", decision
    assert "report_revision_incomplete" in decision["blockers"]


def test_missing_required_report_diagnostic_cannot_complete(db, supported_run):
    run, plan, traces = supported_run
    plan.pop("report_integrity", None)
    store.replace_agent_run_plan(db, run.run_id, plan)
    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    assert decision["status"] != "completed", decision


def test_required_independence_without_coverage_assessment_cannot_complete(db, supported_run):
    run, plan, traces = supported_run
    plan["task_contract"]["requirements"] = [{
        "requirement_id": "retry-independent", "kind": "fact", "required": True,
        "predicate": "retry behavior", "min_independent_sources": 2,
    }]
    plan.pop("coverage_matrix", None)
    store.replace_agent_run_plan(db, run.run_id, plan)
    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    assert decision["status"] != "completed", decision


def test_report_changed_after_verdict_is_not_served_as_verified(db, supported_run):
    run, plan, traces = supported_run
    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    assert decision["status"] == "completed", decision
    Path(resolve_report_path(run.report_path)).write_text(
        "## 3. 最终回答\n\nAn unrelated unsupported conclusion.\n", encoding="utf-8")
    assert report_block_reason(store.get_fresh_agent_run(db, run.run_id))


def test_revision_identity_mismatch_cannot_leave_run_completed(db, supported_run):
    run, plan, traces = supported_run
    plan["report_generation"]["report_revision_id"] = "report_revision_tampered"
    store.replace_agent_run_plan(db, run.run_id, plan)

    decision = finalize_terminal_decision(db, run, plan, traces=traces)
    refreshed = store.get_fresh_agent_run(db, run.run_id)

    assert decision["status"] == "incomplete"
    assert decision["error_code"] == "report_revision_identity_mismatch"
    assert refreshed.status == decision["status"]
    assert json.loads(refreshed.plan_json)["terminal_decision"]["status"] == refreshed.status
    assert report_block_reason(refreshed)


def test_legacy_failed_outcome_cannot_be_exposed_by_status_alone(db):
    run = create_root(db)
    plan = json.loads(run.plan_json)
    plan["research_outcome"] = {"status": "failed", "error_code": "goal_not_met"}
    store.replace_agent_run_plan(db, run.run_id, plan)
    run = store.update_agent_run_status(db, run.run_id, "completed")
    assert report_block_reason(run)


@pytest.mark.parametrize("error_code", [
    "required_fetch_failed",
])
def test_evidence_quality_gate_is_incomplete_not_hard_failure(db, error_code):
    run = create_root(db, "Evidence quality gate fixture")
    run = store.mark_agent_run_running_unless_cancelled(db, run.run_id)
    plan = json.loads(run.plan_json or "{}")
    plan["research_outcome"] = {
        "version": "research-integrity-v2",
        "status": "failed",
        "error_code": error_code,
        "message": f"Research completion blocked: {error_code}.",
    }
    store.replace_agent_run_plan(db, run.run_id, plan)
    decision = finalize_terminal_decision(db, run, plan, traces=[])
    assert decision["status"] == "incomplete", decision
    assert error_code in decision["blockers"]


def test_quick_required_web_fetch_failure_pipeline_is_incomplete(db, r12_settings, monkeypatch):
    """A real planned pipeline must preserve evidence incompleteness as retryable."""
    from app.agent.executor import run_plan
    from app.tools.defaults import register_default_tools

    # This module's lightweight fixtures do not run application startup; make
    # the preflight capability check exercise the same registry as production.
    register_default_tools()

    run = store.create_agent_run(
        db,
        "Quick substantive evidence fixture",
        "summary",
        "real",
        allowed_tools=["tavily_search", "web_fetcher", "report_writer"],
    )
    plan = json.loads(run.plan_json or "{}")
    plan.update({
        "research_mode": "quick",
        "quick_output_mode": "substantive",
        "execution_mode": "planned",
        "allowed_tools": ["tavily_search", "web_fetcher", "report_writer"],
        "steps": [
            {"step_no": 1, "tool_name": "tavily_search", "required": True,
             "arguments": {"query": "fixture", "max_results": 1}},
            {"step_no": 2, "tool_name": "web_fetcher", "required": True,
             "arguments": {"urls": ["https://example.com/fixture"]}},
            {"step_no": 3, "tool_name": "report_writer", "required": False,
             "arguments": {}},
        ],
    })
    store.replace_agent_run_plan(db, run.run_id, plan)

    def fake_operation(tool_name, *_args, **_kwargs):
        if tool_name == "tavily_search":
            return ToolResult(success=True, output={"results": [{
                "url": "https://example.com/fixture",
                "content": "Search snippet that identifies the source for the required full-text fetch.",
            }]})
        return ToolResult(
            success=False,
            output={"pages": [], "fetched_count": 0, "failed_count": 1},
            error_message="required fetch blocked by SSRF policy",
            metadata={"error_type": "ssrf_blocked"},
        )

    monkeypatch.setattr("app.agent.executor.is_executable_tool", lambda _name: True)
    monkeypatch.setattr("app.agent.executor.execute_governed_operation", fake_operation)
    settings = r12_settings.model_copy(update={"tavily_api_key": "fixture"})
    result = run_plan(db, run.run_id, settings_obj=settings)
    refreshed = store.get_fresh_agent_run(db, run.run_id)
    assert result["status"] == "incomplete", result
    assert refreshed.status == "incomplete"
    assert refreshed.report_path is None
    outcome = json.loads(refreshed.plan_json)["research_outcome"]
    assert outcome["error_code"] == "required_fetch_failed"


def test_terminal_replay_is_idempotent_and_new_requirements_reopen_decision(db, supported_run):
    run, plan, traces = supported_run
    first = finalize_terminal_decision(db, run, plan)
    count = len(store.list_tool_traces(db, run.run_id))
    assert first["status"] == "completed"
    assert finalize_terminal_decision(db, run, {}) == first
    assert len(store.list_tool_traces(db, run.run_id)) == count
    persisted = json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)
    persisted["task_contract"]["requirements"] = [{
        "requirement_id": "independent", "kind": "fact", "required": True,
        "predicate": "retry behavior", "min_independent_sources": 2,
    }]
    store.replace_agent_run_plan(db, run.run_id, persisted)
    second = finalize_terminal_decision(db, run, plan)
    assert second["status"] == "incomplete"
    # The report evidence snapshot stays bound to the adopted revision; the
    # separate terminal-input digest records that a new requirement reopened
    # the decision.
    assert second["evidence_snapshot_id"] == first["evidence_snapshot_id"]
    assert second["decision_input_hash"] != first["decision_input_hash"]


@pytest.mark.parametrize("status", ["cancelled", "waiting_human", "waiting_human_plan"])
def test_finalizer_preserves_cancellation_and_human_boundary(db, supported_run, status):
    run, plan, traces = supported_run
    store.update_agent_run_status(db, run.run_id, status)
    assert finalize_terminal_decision(db, run, plan)["status"] == status
    assert store.get_fresh_agent_run(db, run.run_id).status == status
