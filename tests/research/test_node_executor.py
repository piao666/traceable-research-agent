import json

from app.agent.budget import budget_snapshot, ensure_budget
from app.research.node_executor import ResearchNodeExecutor
from app.research.scope import create_research_node, create_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root


def test_node_executor_reuses_root_budget_and_sets_lineage(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    root_node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    node = create_research_node(
        db, scope.scope_id, parent_node_id=root_node.node_id, run_id=None,
        node_type="verification", topic="verify", query="verify", research_goal="verify",
        depth=1, priority=1,
    )
    ensure_budget(db, root.run_id, r12_settings)

    def runner(session, run_id, settings, _client):
        add_web_trace(session, run_id, "Child verification evidence.", "node")
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    result = ResearchNodeExecutor(runner=runner).execute(db, scope, node, r12_settings)
    child = store.get_agent_run(db, result["run_id"])
    plan = json.loads(child.plan_json)
    assert child.parent_run_id == root.run_id
    assert child.root_run_id == root.run_id
    assert child.run_role == "verification_branch"
    assert child.research_scope_id == scope.scope_id
    assert plan["defer_to_research_scope"] is True
    assert budget_snapshot(db, child.run_id)["root_run_id"] == root.run_id
    assert node.status == "completed"
