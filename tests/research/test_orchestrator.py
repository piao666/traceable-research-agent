import json
from unittest.mock import patch

from app.agent.outcome import load_observations
from app.agent.outcome import result_integrity
from app.agent.budget import BudgetExceeded
from app.agent.reporter import build_bounded_provenance_context
from app.eval.fake_react_llm import FakeReActLLMClient
from app.evidence.service import materialize_execution_provenance
from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.node_executor import ResearchNodeExecutor
from app.research.orchestrator import run_deep_research_v2
from app.research.scope import (
    create_research_node,
    create_research_scope,
    resolve_research_scope,
)
from app.trace import store

from .conftest import add_web_trace, create_root


def test_orchestrator_final_report_receives_parent_and_child_evidence(db, r12_settings):
    root = create_root(db)
    actor_client = FakeReActLLMClient([])
    actor_client.describe = lambda: {
        "provider": "fixture",
        "model": "actor-A",
        "available": True,
    }
    synthesizer_client = FakeReActLLMClient([])
    synthesizer_client.describe = lambda: {
        "provider": "fixture",
        "model": "synthesizer-B",
        "available": True,
    }

    def fake_node_runner(session, run_id, settings, _client):
        assert _client.describe()["model"] == "actor-A"
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
        if run.run_role == "root":
            return {"run_id": run_id, "status": "running"}
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    def branch_planner(_client, **kwargs):
        assert _client.describe()["model"] == "actor-A"
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
        assert store.get_fresh_agent_run(db, root.run_id).status == "running"
        captured["report_model"] = kwargs["llm_client"].describe()["model"]
        bundle = kwargs["provenance_bundle"]
        captured["bundle"] = bundle
        child_citation = next(
            item for item in bundle["citations"] if item["origin_run_id"] != root.run_id
        )
        child_passage = next(
            item for item in bundle["passages"] if item["passage_id"] == child_citation["passage_id"]
        )
        return (
            "# Scope report\n\n## 3. 最终回答\n\n"
            f"{child_passage['text']} [{child_citation['citation_label']}]"
        )

    with (
        patch("app.research.orchestrator.run_react_task", side_effect=fake_node_runner),
        patch("app.research.orchestrator.save_report", return_value="workspace/reports/r12.md"),
    ):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings.model_copy(update={"report_generation_mode": "llm"}),
            actor_client,
            report_llm_client=synthesizer_client,
            branch_planner=branch_planner,
            node_executor=ResearchNodeExecutor(runner=fake_node_runner),
            report_generator=report_generator,
        )

    assert result["status"] == "completed"
    assert result["execution_mode"] == "deep_research_v2"
    assert result["research_node_count"] == 2
    assert captured["report_model"] == "synthesizer-B"
    origin_run_ids = {item["origin_run_id"] for item in captured["bundle"]["passages"]}
    assert root.run_id in origin_run_ids
    assert len(origin_run_ids) == 2
    scope = resolve_research_scope(db, root.run_id)
    assert scope.status == "completed"
    completed_root = store.get_agent_run(db, root.run_id)
    completed_plan = json.loads(completed_root.plan_json)
    assert completed_plan["research_outcome"]["status"] == "passed"
    assert completed_plan["report_integrity"]["status"] == "passed"
    assert result_integrity(completed_root)["requires_review"] is False
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
        return {"run_id": run_id, "status": "running"}

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


def test_orchestrator_resume_skips_completed_branch_planning(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    root_node = create_research_node(
        db,
        scope.scope_id,
        parent_node_id=None,
        run_id=root.run_id,
        node_type="discovery",
        topic="root",
        query="root query",
        research_goal="root",
        depth=0,
        priority=0,
        status="completed",
        metadata={"required": True, "branch_planning_status": "completed"},
    )
    child_run = store.create_agent_run(
        db,
        "child query",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_status(db, child_run.run_id, "completed", None)
    create_research_node(
        db,
        scope.scope_id,
        parent_node_id=root_node.node_id,
        run_id=child_run.run_id,
        node_type="web_research",
        topic="child",
        query="child query",
        research_goal="child",
        depth=1,
        priority=1,
        status="completed",
    )
    planner_tasks = []

    def planner(_client, **kwargs):
        planner_tasks.append(kwargs["task"])
        assert set(kwargs["prior_queries"]) == {"root query", "child query"}
        return {"branches": [], "is_comprehensive": False, "finalization_limited": True}

    with patch("app.research.orchestrator.run_react_task") as root_runner:
        run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=planner,
        )

    root_runner.assert_not_called()
    assert planner_tasks == ["child query"]


def test_bounded_scope_context_round_robins_sparse_child_evidence():
    bundle = {
        "schema_version": "research-scope-evidence-v2",
        "claims": [],
        "report_claims": [],
        "passages": [],
        "citations": [],
        "scope_claim_groups": [],
        "scope_resolutions": [],
    }

    def add_claim(index, node_id, run_id, text, score):
        claim_id = f"claim-{index}"
        report_claim_id = f"report-{index}"
        passage_id = f"passage-{index}"
        group_id = f"group-{index}"
        bundle["claims"].append({
            "claim_id": claim_id,
            "claim_text": text,
            "origin_run_id": run_id,
            "research_node_id": node_id,
        })
        bundle["report_claims"].append({
            "report_claim_id": report_claim_id,
            "claim_id": claim_id,
            "claim_text": text,
        })
        bundle["passages"].append({
            "passage_id": passage_id,
            "text": text + " evidence " + ("x" * 700),
            "origin_run_id": run_id,
            "research_node_id": node_id,
            "origin_trace_id": f"trace-{index}",
        })
        bundle["citations"].append({
            "report_claim_id": report_claim_id,
            "passage_id": passage_id,
            "citation_label": f"CIT-{index:03d}-01",
            "origin_run_id": run_id,
        })
        bundle["scope_claim_groups"].append({
            "group_id": group_id,
            "representative_claim_text": text,
            "members": [{"claim_id": claim_id, "origin_run_id": run_id}],
        })
        bundle["scope_resolutions"].append({
            "group_id": group_id,
            "status": "resolved",
            "confidence": score,
            "rationale": {"relations": [{"passage_id": passage_id, "score": score}]},
        })

    for index in range(1, 7):
        add_claim(index, "root-node", "root-run", f"root claim {index}", 0.9)
    for index in range(7, 13):
        add_claim(index, "child-a-node", "child-a-run", f"child A claim {index}", 0.8)
    add_claim(13, "child-b-node", "child-b-run", "sparse high value child B", 0.99)

    payload = json.loads(build_bounded_provenance_context(bundle, token_budget=900))

    assert any(
        "child-b-node" in claim["research_node_ids"]
        and claim["citations"][0]["passage_id"] == "passage-13"
        for claim in payload["claims"]
    )
