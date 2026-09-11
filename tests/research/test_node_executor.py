import json

import pytest

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


def test_node_executor_reuses_completed_run_without_calling_runner(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(
        db,
        "existing",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_status(db, child.run_id, "completed", None)
    node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=child.run_id,
        node_type="web_research", topic="existing", query="existing",
        research_goal="existing", depth=1, priority=1, status="completed",
    )
    calls = []

    result = ResearchNodeExecutor(
        runner=lambda *_args: calls.append(True)
    ).execute(db, scope, node, r12_settings)

    assert result == {
        "node_id": node.node_id,
        "run_id": child.run_id,
        "status": "completed",
    }
    assert calls == []
    assert len([
        run for run in store.list_agent_runs(db, include_internal=True)
        if run.parent_run_id
    ]) == 1


def test_node_executor_resumes_running_run_without_creating_replacement(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(
        db,
        "existing",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_status(db, child.run_id, "running", None)
    node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=child.run_id,
        node_type="web_research", topic="existing", query="existing",
        research_goal="existing", depth=1, priority=1, status="running",
    )
    calls = []

    def runner(session, run_id, _settings, _client):
        calls.append(run_id)
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    result = ResearchNodeExecutor(runner=runner).execute(
        db, scope, node, r12_settings
    )

    assert result["run_id"] == child.run_id
    assert result["status"] == "completed"
    assert calls == [child.run_id]
    assert len([
        run for run in store.list_agent_runs(db, include_internal=True)
        if run.parent_run_id
    ]) == 1


@pytest.mark.parametrize(
    "status", ["waiting_human", "waiting_human_plan", "failed", "cancelled"]
)
def test_node_executor_does_not_replace_non_resumable_run(
    db, r12_settings, status
):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(
        db,
        "existing",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_status(db, child.run_id, status, None)
    node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=child.run_id,
        node_type="web_research", topic="existing", query="existing",
        research_goal="existing", depth=1, priority=1, status=status,
    )
    calls = []

    result = ResearchNodeExecutor(
        runner=lambda *_args: calls.append(True)
    ).execute(db, scope, node, r12_settings)

    assert result["run_id"] == child.run_id
    assert result["status"] == status
    assert calls == []
    assert len([
        run for run in store.list_agent_runs(db, include_internal=True)
        if run.parent_run_id
    ]) == 1


def test_create_research_node_reuses_normalized_sibling_query(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    parent = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0,
    )
    first = create_research_node(
        db, scope.scope_id, parent_node_id=parent.node_id, run_id=None,
        node_type="web_research", topic="first", query="ＦＯＯ   Bar",
        research_goal="first", depth=1, priority=1,
    )
    duplicate = create_research_node(
        db, scope.scope_id, parent_node_id=parent.node_id, run_id=None,
        node_type="verification", topic="second", query=" foo\tbar ",
        research_goal="second", depth=1, priority=2,
    )

    assert duplicate.node_id == first.node_id
