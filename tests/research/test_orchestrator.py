import json
from unittest.mock import patch

from app.agent.outcome import load_observations
from app.agent.outcome import result_integrity
from app.agent.budget import BudgetExceeded
from app.eval.fake_react_llm import FakeReActLLMClient
from app.evidence.service import materialize_execution_provenance
from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.node_executor import ResearchNodeExecutor
from app.research.orchestrator import run_deep_research_v2
from app.research.scope import resolve_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root


def test_orchestrator_final_report_receives_parent_and_child_evidence(db, r12_settings):
    root = create_root(db)

    def fake_node_runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        suffix = "root" if run_id == root.run_id else "child"
        add_web_trace(session, run_id, f"Verified {suffix} evidence for final synthesis.", suffix)
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

    def branch_planner(_client, **kwargs):
        if kwargs["depth"] == 1:
            return {
                "branches": [
                    {
                        "topic": "child topic",
                        "query": "child query",
                        "research_goal": "verify child fact",
                        "node_type": "query",
                        "priority": 1,
                        "required": True,
                    }
                ],
                "is_comprehensive": False,
            }
        return {"branches": [], "is_comprehensive": True}

    captured = {}

    def report_generator(_run, _plan, _observations, _traces, **kwargs):
        bundle = kwargs["provenance_bundle"]
        captured["bundle"] = bundle
        child_citation = next(
            item for item in bundle["citations"] if item["origin_run_id"] != root.run_id
        )
        child_passage = next(
            item for item in bundle["passages"] if item["passage_id"] == child_citation["passage_id"]
        )
        return f"# Scope report\n\n{child_passage['text']} [{child_citation['citation_label']}]"

    with (
        patch("app.research.orchestrator.run_react_task", side_effect=fake_node_runner),
        patch("app.research.orchestrator.save_report", return_value="workspace/reports/r12.md"),
    ):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=branch_planner,
            node_executor=ResearchNodeExecutor(runner=fake_node_runner),
            report_generator=report_generator,
        )

    assert result["status"] == "completed"
    assert result["execution_mode"] == "deep_research_v2"
    assert result["research_node_count"] == 2
    origin_run_ids = {item["origin_run_id"] for item in captured["bundle"]["passages"]}
    assert root.run_id in origin_run_ids
    assert len(origin_run_ids) == 2
    scope = resolve_research_scope(db, root.run_id)
    assert scope.status == "completed"
    assert result_integrity(store.get_agent_run(db, root.run_id))["requires_review"] is False
    assert get_scope_provenance_bundle(db, scope)["integrity"]["child_citation_count"] >= 1


def test_report_budget_failure_marks_root_and_scope_failed(db, r12_settings):
    root = create_root(db)

    def fake_root_runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        add_web_trace(session, run_id, "Verified evidence before report failure.", "root")
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

    def fail_report(*_args, **_kwargs):
        raise BudgetExceeded("tokens")

    with patch("app.research.orchestrator.run_react_task", side_effect=fake_root_runner):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=lambda *_args, **_kwargs: {
                "branches": [],
                "is_comprehensive": True,
            },
            report_generator=fail_report,
        )

    scope = resolve_research_scope(db, root.run_id)
    assert result["status"] == "failed"
    assert scope.status == "failed"
