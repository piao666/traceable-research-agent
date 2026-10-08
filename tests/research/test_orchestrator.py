import json
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agent.outcome import load_observations
from app.agent.outcome import result_integrity
from app.agent.budget import BudgetExceeded
from app.agent.reporter import build_bounded_provenance_context, save_report as real_save_report
from tests.support.fake_react_llm import FakeReActLLMClient
from app.evidence.service import materialize_execution_provenance
from app.evidence.citation_validator import extract_final_answer_section
from app.evidence.reference_verifier import (
    ReferenceVerificationDetail,
    ReferenceVerificationReport,
)
from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.node_executor import ResearchNodeExecutor
from app.research.node_executor import _node_task_contract
from app.research.models import CoverageSnapshot, ResearchPlanRevision
from app.research.orchestrator import _branch_has_budget, _explicit_scope_covered, run_deep_research_v2
from app.agent.react_executor import (
    _first_branch_web_discovery_only,
    _react_step_allowance,
    _tool_call_limit,
)
from app.agent.source_intake import prepare_tool_arguments
from app.config import Settings
from app.research.scope import (
    create_research_node,
    create_research_scope,
    list_scope_nodes,
    resolve_research_scope,
)
from app.trace import store

from .conftest import add_web_trace, create_root


def test_pear_discovery_and_child_have_bounded_shared_budget_allowances():
    settings = Settings(deep_research_enabled=True, react_max_steps=12)
    root_plan = {"engine_version": "v2", "research_controller": "pear", "run_role": "root"}
    child_plan = {**root_plan, "run_role": "branch"}
    assert _react_step_allowance(root_plan, settings) <= 4
    assert _react_step_allowance(child_plan, settings) <= 7
    assert _tool_call_limit(root_plan, settings, "web_fetcher") == 1
    assert _tool_call_limit(root_plan, settings, "tavily_search") <= 2
    urls = [f"https://docs.example/page-{index}" for index in range(5)]
    assert len(prepare_tool_arguments("web_fetcher", {"urls": urls}, root_plan, settings)["urls"]) == 2
    assert len(prepare_tool_arguments("web_fetcher", {"urls": urls}, child_plan, settings)["urls"]) == 5

    class Runtime:
        def snapshot(self):
            return {"accounted_tokens": 48000, "llm_calls": 8}

        def can_deepen(self, **kwargs):
            self.request = kwargs
            return True

    runtime = Runtime()
    assert _branch_has_budget(runtime, settings)
    assert runtime.request == {"required_llm_calls": 14, "required_tokens": 84000}


def test_external_web_child_first_discovers_urls_when_tavily_is_available():
    plan = {
        "engine_version": "v2", "research_controller": "pear", "run_role": "branch",
        "task_contract": {
            "original_task": "研究 Jev 对 Agent 的影响",
            "evidence_scope_requirements": [{"source_scope": "external_web"}],
        },
    }
    tools = ["semantic_scholar_search", "web_fetcher", "tavily_search"]
    assert _first_branch_web_discovery_only(plan, {}, tools)
    assert not _first_branch_web_discovery_only(plan, {"observation_history": [{}]}, tools)
    assert not _first_branch_web_discovery_only(plan, {}, tools[:-1])
    plan["task_contract"]["original_task"] = "Read https://example.com/jev"
    assert not _first_branch_web_discovery_only(plan, {}, tools)
    plan["task_contract"]["original_task"] = "研究 Jev 对 Agent 的影响"
    plan["task_contract"]["evidence_scope_requirements"] = [{"source_scope": "local_workspace"}]
    assert not _first_branch_web_discovery_only(plan, {}, tools)


def test_explicit_scope_coverage_requires_eligible_body_from_every_required_node():
    scope = SimpleNamespace(scope_id="scope")
    nodes = [
        SimpleNamespace(run_id=run_id, status="completed", metadata_json='{"required": true}')
        for run_id in ("root", "muse", "jev")
    ]
    nodes.append(SimpleNamespace(run_id=None, status="pending", metadata_json='{"required": false}'))
    bundle = {"passages": [
        {"passage_id": f"p{index}", "origin_run_id": run_id}
        for index, run_id in enumerate(("root", "muse", "jev"))
    ]}
    contract = {"evidence_scope_requirements": [{"requirement_id": "muse"}, {"requirement_id": "jev"}]}
    assessment = SimpleNamespace(passed=True, eligible_passage_ids=["p0", "p1", "p2"])
    with (
        patch("app.research.orchestrator.list_scope_nodes", return_value=nodes),
        patch("app.research.orchestrator.get_scope_provenance_bundle", return_value=bundle),
        patch("app.agent.evidence_requirements.assess_required_evidence", return_value=assessment),
    ):
        assert _explicit_scope_covered(None, scope, {"task_contract": contract})
        assessment.eligible_passage_ids.remove("p2")
        assert not _explicit_scope_covered(None, scope, {"task_contract": contract})
        assessment.eligible_passage_ids.append("p2")
        nodes[2].status = "failed"
        assert not _explicit_scope_covered(None, scope, {"task_contract": contract})


def test_completed_required_branches_defer_redundant_optional_work(db, r12_settings):
    root = create_root(db)
    executed = []

    def runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        executed.append(run_id)
        add_web_trace(session, run_id, f"Traceable evidence for {run.task} from a real fetched page.", run_id)
        traces = store.list_tool_traces(session, run_id)
        materialize_execution_provenance(
            session, run, json.loads(run.plan_json or "{}"),
            load_observations(traces), traces, settings,
        )
        if run_id != root.run_id:
            store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "running" if run_id == root.run_id else "completed"}

    def planner(_client, **kwargs):
        if kwargs["depth"] > 1:
            return {"branches": [], "is_comprehensive": True}
        return {"branches": [
            {"topic": topic, "query": topic, "research_goal": topic,
             "node_type": "query", "priority": index, "required": index < 2}
            for index, topic in enumerate(("Muse Agent impact", "Jev Agent impact", "optional comparison"))
        ], "is_comprehensive": False}

    def covered(session, scope, _plan):
        required = [node for node in list_scope_nodes(session, scope.scope_id)
                    if json.loads(node.metadata_json or "{}").get("required", True)]
        return len(required) == 3 and all(node.status == "completed" for node in required)

    with (
        patch("app.research.orchestrator.run_react_task", side_effect=runner),
        patch("app.research.orchestrator._explicit_scope_covered", side_effect=covered),
    ):
        run_deep_research_v2(
            db, root.run_id, r12_settings, FakeReActLLMClient([]),
            branch_planner=planner,
            node_executor=ResearchNodeExecutor(runner=runner),
            report_generator=lambda *_args, **_kwargs: "# Audit only",
        )
    scope = resolve_research_scope(db, root.run_id)
    nodes = list_scope_nodes(db, scope.scope_id)
    assert len(executed) == 3  # root plus the two required children
    assert nodes[3].status == "pending"
    assert nodes[3].run_id is None
    assert json.loads(nodes[3].metadata_json)["deferred_reason"] == "explicit_scope_covered"


def test_unexecuted_planned_branch_cannot_pass_scope_gate(db, r12_settings):
    root = create_root(db)

    def root_runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        add_web_trace(session, run_id, "Compare traceable research systems: root evidence.", "root")
        traces = store.list_tool_traces(session, run_id)
        materialize_execution_provenance(
            session, run, json.loads(run.plan_json or "{}"),
            load_observations(traces), traces, settings,
        )
        return {"run_id": run_id, "status": "running"}

    def planner(_client, **_kwargs):
        return {"branches": [{
            "topic": "missing", "query": "missing detail", "research_goal": "verify",
            "node_type": "query", "priority": 1, "required": True,
        }], "is_comprehensive": False}

    with (
        patch("app.research.orchestrator.run_react_task", side_effect=root_runner),
        patch("app.research.orchestrator._branch_has_budget", return_value=False),
    ):
        run_deep_research_v2(
            db, root.run_id, r12_settings, FakeReActLLMClient([]),
            branch_planner=planner,
        )
    scope = resolve_research_scope(db, root.run_id)
    nodes = list_scope_nodes(db, scope.scope_id)
    assert len(nodes) == 2
    assert nodes[1].status == "pending"
    plan = json.loads(store.get_fresh_agent_run(db, root.run_id).plan_json)
    assert plan["research_outcome"]["status"] == "failed"
    assert "required_research_branch_incomplete" in plan["research_outcome"]["errors"]


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
        add_web_trace(session, run_id, f"Compare traceable research systems: verified {suffix} evidence for final synthesis.", suffix)
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

    def save_report(_run_id, markdown):
        captured["markdown"] = markdown
        return real_save_report(_run_id, markdown)

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
        patch("app.research.orchestrator.save_report", side_effect=save_report),
        patch(
            "app.research.orchestrator.extract_cited_academic_references",
            return_value=[{"title": "Unresolved cited work"}],
        ),
        patch(
            "app.research.orchestrator.ReferenceVerifier.verify",
            return_value=ReferenceVerificationReport(
                total=1,
                unresolved=1,
                network_failures=1,
                indexes_available=["crossref"],
                details=[
                    ReferenceVerificationDetail(
                        ref_label="REF-001",
                        identifier_type="title_author",
                        identifier_value="Unresolved cited work",
                        status="unresolved",
                        indexes_checked=["crossref"],
                        failure_reason="network_unavailable",
                    )
                ],
            ),
        ),
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
    assert "## 12. 文献存在性校验" in captured["markdown"]
    assert "网络失败: 1" in captured["markdown"]
    assert "## 12. 文献存在性校验" not in extract_final_answer_section(
        captured["markdown"]
    )
    assert result_integrity(completed_root)["requires_review"] is False
    assert get_scope_provenance_bundle(db, scope)["integrity"]["child_citation_count"] >= 1

    # A completed V2 run is replay-safe: no external runner/planner/report call
    # and no additional durable plan or assessment rows are created.
    revision_count = db.scalar(select(func.count()).select_from(ResearchPlanRevision))
    snapshot_count = db.scalar(select(func.count()).select_from(CoverageSnapshot))
    with (
        patch("app.research.orchestrator.run_react_task", side_effect=AssertionError("replayed")),
        patch("app.research.orchestrator.save_report", side_effect=AssertionError("replayed")),
        patch("app.research.orchestrator.plan_research_branches", side_effect=AssertionError("replayed")),
    ):
        replay = run_deep_research_v2(db, root.run_id, r12_settings, actor_client)
    assert replay["status"] == "completed"
    assert db.scalar(select(func.count()).select_from(ResearchPlanRevision)) == revision_count
    assert db.scalar(select(func.count()).select_from(CoverageSnapshot)) == snapshot_count


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


def test_branch_planner_failure_persists_provider_diagnostics(db, r12_settings):
    root = create_root(db)

    def fake_root_runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        add_web_trace(session, run_id, "Evidence collected before planner failure.", "root")
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

    with patch("app.research.orchestrator.run_react_task", side_effect=fake_root_runner):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=lambda *_args, **_kwargs: {
                "branches": [],
                "is_comprehensive": False,
                "planner_failed": True,
                "error_type": "structured_output_truncated",
                "error_message": "Branch planner response reached the provider output limit.",
                "provider": "fixture",
                "model": "reasoning-model",
                "finish_reason": "length",
                "prompt_tokens": 1102,
                "completion_tokens": 1200,
                "content_length": 693,
            },
        )

    trace = next(
        item for item in store.list_tool_traces(db, root.run_id)
        if item.tool_name == "research_branch_planner"
    )
    diagnostics = json.loads(trace.output_json)
    assert result["status"] == "failed"
    assert diagnostics["error_type"] == "structured_output_truncated"
    assert diagnostics["finish_reason"] == "length"
    assert diagnostics["provider"] == "fixture"
    assert trace.token_in == 1102
    assert trace.token_out == 1200


def test_empty_noncomprehensive_deep_scope_is_incomplete_not_execution_failure(db, r12_settings):
    root = create_root(db)

    def no_evidence_runner(session, run_id, _settings, _client):
        store.mark_agent_run_running_unless_cancelled(session, run_id)
        return {"run_id": run_id, "status": "running"}

    with patch("app.research.orchestrator.run_react_task", side_effect=no_evidence_runner):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=lambda *_args, **_kwargs: {
                "branches": [], "is_comprehensive": False,
            },
        )

    plan = json.loads(store.get_agent_run(db, root.run_id).plan_json)
    assert result["status"] == "incomplete"
    assert plan["research_outcome"]["error_code"] == "no_usable_evidence"
    assert "no_usable_evidence" in plan["terminal_decision"]["blockers"]


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


@pytest.mark.parametrize("persisted_status", ["pending", "running"])
def test_orchestrator_resume_executes_existing_unfinished_child(
    db,
    r12_settings,
    persisted_status,
):
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
    add_web_trace(db, root.run_id, "Compare traceable research systems: verified root evidence for resumed research.", "resume-root")
    root_traces = store.list_tool_traces(db, root.run_id)
    materialize_execution_provenance(
        db,
        root,
        json.loads(root.plan_json or "{}"),
        load_observations(root_traces),
        root_traces,
        r12_settings,
    )
    child_run = store.create_agent_run(
        db,
        "resumable child query",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_plan(
        db,
        child_run.run_id,
        {
            "version": "research-node-v2",
            "task": "resumable child query",
            "execution_mode": "react",
            "allowed_tools": ["web_fetcher"],
            "research_scope_id": scope.scope_id,
            "root_run_id": root.run_id,
            "defer_to_research_scope": True,
            "steps": [],
        },
    )
    if persisted_status == "running":
        store.mark_agent_run_running_unless_cancelled(db, child_run.run_id)
    child_node = create_research_node(
        db,
        scope.scope_id,
        parent_node_id=root_node.node_id,
        run_id=child_run.run_id,
        node_type="web_research",
        topic="resumable child",
        query="resumable child query",
        research_goal="resume existing child",
        depth=1,
        priority=1,
        status=persisted_status,
        metadata={"required": True},
    )
    resumed_run_ids = []

    def resume_runner(session, run_id, settings, _client):
        resumed_run_ids.append(run_id)
        assert run_id == child_run.run_id
        running = store.get_fresh_agent_run(session, run_id)
        add_web_trace(session, run_id, "Compare traceable research systems: verified evidence from the resumed child.", "resume-child")
        traces = store.list_tool_traces(session, run_id)
        materialize_execution_provenance(
            session,
            running,
            json.loads(running.plan_json or "{}"),
            load_observations(traces),
            traces,
            settings,
        )
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    def report_generator(_run, _plan, _observations, _traces, **kwargs):
        bundle = kwargs["provenance_bundle"]
        citation = next(
            item for item in bundle["citations"] if item["origin_run_id"] == child_run.run_id
        )
        passage = next(
            item for item in bundle["passages"] if item["passage_id"] == citation["passage_id"]
        )
        return "# Resume report\n\n## 3. 最终回答\n\n" + (
            f"{passage['text']} [{citation['citation_label']}]"
        )

    with patch("app.research.orchestrator.save_report", side_effect=real_save_report):
        result = run_deep_research_v2(
            db,
            root.run_id,
            r12_settings,
            FakeReActLLMClient([]),
            branch_planner=lambda *_args, **_kwargs: {
                "branches": [],
                "is_comprehensive": True,
            },
            node_executor=ResearchNodeExecutor(runner=resume_runner),
            report_generator=report_generator,
        )

    assert result["status"] == "completed"
    assert resumed_run_ids == [child_run.run_id]
    assert child_node.status == "completed"
    assert [node.run_id for node in list_scope_nodes(db, scope.scope_id)].count(child_run.run_id) == 1


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
def test_research_child_inherits_constraints_but_only_its_entity_requirement():
    from types import SimpleNamespace

    root_contract = {
        "original_task": "Analyze Muse and Jev impact on Agents",
        "evidence_requirement": "substantive",
        "source_constraints": {"official_only": True},
        "evidence_scope_requirements": [
            {"requirement_id": "muse", "entity": "Muse", "dimension": "Agent impact"},
            {"requirement_id": "jev", "entity": "Jev", "dimension": "Agent impact"},
        ],
    }
    node = SimpleNamespace(query="Muse software agent capabilities", research_goal="Verify Muse impact on Agents")
    child = _node_task_contract(root_contract, node)
    assert child["source_constraints"] == {"official_only": True}
    assert child["original_task"] == node.research_goal
    assert [item["requirement_id"] for item in child["evidence_scope_requirements"]] == ["muse"]
    assert len(root_contract["evidence_scope_requirements"]) == 2


def test_persisted_research_child_contract_is_derived_from_root_lineage(db):
    root = create_root(db, "Analyze Muse and Jev impact on Agents")
    root_plan = json.loads(root.plan_json)
    root_plan["task_contract"] = {
        "original_task": root.task, "evidence_requirement": "substantive",
        "source_constraints": {"official_only": True},
        "evidence_scope_requirements": [
            {"requirement_id": "muse", "entity": "Muse", "dimension": "Agent impact"},
            {"requirement_id": "jev", "entity": "Jev", "dimension": "Agent impact"},
        ],
    }
    store.replace_agent_run_plan(db, root.run_id, root_plan)
    scope = create_research_scope(db, root.run_id, root_plan["task_contract"])
    node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=None,
        node_type="web_research", topic="Muse", query="Muse software Agent capabilities",
        research_goal="Verify Muse impact on Agents", depth=1, priority=1,
    )
    child = store.create_agent_run(
        db, node.query, "summary", "real", parent_run_id=root.run_id,
        root_run_id=root.run_id, research_scope_id=scope.scope_id,
    )
    node.run_id = child.run_id
    db.commit()
    store.update_agent_run_plan(db, child.run_id, {
        "version": "research-node-v2", "task_contract": {"evidence_requirement": "unspecified"},
        "steps": [],
    })
    contract = json.loads(store.get_fresh_agent_run(db, child.run_id).plan_json)["task_contract"]
    assert contract["evidence_requirement"] == "substantive"
    assert contract["source_constraints"]["official_only"] is True
    assert [item["requirement_id"] for item in contract["evidence_scope_requirements"]] == ["muse"]
