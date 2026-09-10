import json

from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.outcome import assess_scope_outcome
from app.research.scope import create_research_node, create_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root, materialize_run


def test_scope_outcome_requires_real_scope_evidence(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    bundle = get_scope_provenance_bundle(db, scope)
    outcome = assess_scope_outcome(db, scope, bundle, {})
    assert outcome["status"] == "failed"
    assert outcome["error_code"] == "no_usable_evidence"


def test_scope_outcome_passes_completed_evidence_node(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    add_web_trace(db, root.run_id, "Verified evidence for the requested research task.", "root")
    materialize_run(db, root, r12_settings)
    outcome = assess_scope_outcome(db, scope, get_scope_provenance_bundle(db, scope), {})
    assert outcome["status"] == "passed"


def test_scope_outcome_rejects_required_node_that_admits_goal_failure(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    plan = json.loads(root.plan_json)
    plan["react_state"] = {
        "finish_reason": "max_steps_reached",
        "finish_summary": "The task could not be completed.",
        "goal_status": "not_met",
    }
    store.replace_agent_run_plan(db, root.run_id, plan)
    add_web_trace(db, root.run_id, "Some evidence exists but the required goal is unmet.", "limited")
    materialize_run(db, root, r12_settings)
    outcome = assess_scope_outcome(db, scope, get_scope_provenance_bundle(db, scope), {})
    assert outcome["status"] == "failed"
    assert "required_research_branch_goal_not_met" in outcome["errors"]
