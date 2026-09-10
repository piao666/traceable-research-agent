from app.research.scope import create_research_scope, list_scope_runs, resolve_research_scope
from app.trace import store

from .conftest import create_root


def test_scope_resolver_uses_columns_not_plan_json(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(
        db,
        "child",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    store.update_agent_run_plan(db, child.run_id, {"parent_run_id": "wrong", "steps": []})
    assert resolve_research_scope(db, child.run_id).scope_id == scope.scope_id
    assert [run.run_id for run in list_scope_runs(db, scope.scope_id)] == [root.run_id, child.run_id]


def test_default_task_listing_hides_internal_scope_runs(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    store.create_agent_run(
        db,
        "child",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    assert [run.run_id for run in store.list_agent_runs(db)] == [root.run_id]
    assert len(store.list_agent_runs(db, include_internal=True)) == 2
