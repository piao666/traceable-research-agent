"""P0 contracts for the Quick/Deep PEAR refactor."""

from __future__ import annotations

from app.agent.dispatcher import _is_quick_plan
from app.agent.planner import plan_task
from app.api.tasks import _clear_retry_derived_state
from app.config import Settings
from app.evidence.quality import calculate_evidence_quality
from app.research.coverage import assess_comparison_coverage
from app.research.contracts import normalize_requirements
from app.research.node_executor import ResearchNodeExecutor
from app.research.scope import create_research_node, create_research_scope
from app.trace import store

from .conftest import create_root


def test_quick_mode_is_a_hard_planning_boundary(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent.planner.settings",
        Settings(deep_research_enabled=True, react_enabled=True),
    )
    plan = plan_task(
        "比较多个框架的风险并给出建议",
        allowed_tools=["tavily_search", "report_writer"],
        scenario_template="standard",
        research_mode="quick",
    )
    assert plan["research_mode"] == "quick"
    assert plan["execution_mode"] == "planned"
    assert plan["requested_execution_mode"] == "planned"
    assert _is_quick_plan(plan) is True


def test_legacy_standard_template_does_not_change_auto_mode() -> None:
    plan = plan_task(
        "给出一个事实摘要",
        allowed_tools=["tavily_search", "report_writer"],
        scenario_template="standard",
    )
    assert plan["research_mode"] == "auto"
    assert plan.get("quick_mode") is not True


def test_explicit_deep_selects_scope_controller(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent.planner.settings",
        Settings(deep_research_enabled=True, react_enabled=True),
    )
    plan = plan_task(
        "梳理多个来源并进行深入研究",
        allowed_tools=["tavily_search", "report_writer"],
        research_mode="deep",
    )
    assert plan["research_mode"] == "deep"
    assert plan["execution_mode"] == "react"
    assert plan["requested_execution_mode"] == "react"


def test_coverage_does_not_trust_fetched_flag_without_fetch_trace() -> None:
    result = assess_comparison_coverage(
        {
            "goal_kind": "comparison",
            "requirements": [
                {"requirement_id": "r1", "entity": "Alpha", "dimension": "架构"}
            ],
        },
        {
            "sources": [
                {
                    "source_id": "s1",
                    "url": "https://example.com/alpha",
                    "title": "Alpha architecture",
                    "fetch_status": "fetched",
                }
            ]
        },
        traces=[],
    )
    assert result["complete"] is False
    assert result["requirements"][0]["status"] != "covered"


def test_legacy_requirements_are_normalized_to_typed_contracts() -> None:
    requirements = normalize_requirements(
        {
            "requirements": [
                {
                    "requirement_id": "cmp-1",
                    "entity": "Alpha",
                    "dimension": "架构",
                    "mandatory": False,
                }
            ]
        }
    )
    assert requirements[0].kind == "comparison"
    assert requirements[0].question_id == "q-1"
    assert requirements[0].required is False


def test_contextualizes_citation_is_not_claim_support() -> None:
    result = calculate_evidence_quality(
        {
            "report_claims": [{"report_claim_id": "rc1", "claim_id": "c1"}],
            "citations": [{"report_claim_id": "rc1", "edge_id": "e1"}],
            "edges": [
                {"edge_id": "e1", "claim_id": "c1", "relation": "contextualizes"}
            ],
        }
    )
    assert result["claim_support_coverage"] == 0.0


def test_new_waiting_child_preserves_waiting_node_state(db, r12_settings) -> None:
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    node = create_research_node(
        db,
        scope.scope_id,
        parent_node_id=None,
        run_id=None,
        node_type="verification",
        topic="approval",
        query="approval",
        research_goal="approval",
        depth=1,
        priority=1,
    )

    def runner(session, run_id, _settings, _client):
        store.update_agent_run_status(session, run_id, "waiting_human", None)
        return {"run_id": run_id, "status": "waiting_human"}

    result = ResearchNodeExecutor(runner=runner).execute(db, scope, node, r12_settings)
    assert result["status"] == "waiting_human"
    assert node.status == "waiting_human"


def test_retry_cleanup_removes_new_controller_and_coverage_state() -> None:
    plan = {
        "task": "retry",
        "research_mode": "deep",
        "research_scope_id": "scope-old",
        "research_node_id": "node-old",
        "root_run_id": "run-old",
        "run_role": "root",
        "engine_version": "v2",
        "defer_to_research_scope": True,
        "report_integrity": {"status": "passed"},
        "report_diagnostics": {"repair_attempted": True},
        "coverage_snapshot": {"complete": True},
        "controller_runtime": {"phase": "finalizing"},
        "adaptive_gate_pending": True,
    }
    _clear_retry_derived_state(plan)
    assert plan == {"task": "retry", "research_mode": "deep"}
