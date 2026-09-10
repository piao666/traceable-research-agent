import asyncio

from app.api.tasks import (
    get_task_research_scope,
    get_task_research_tree,
    get_task_scope_evidence,
)
from app.research.scope import create_research_node, create_research_scope

from .conftest import add_web_trace, create_root, materialize_run


def test_scope_read_apis_share_same_scope_identity(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    add_web_trace(db, root.run_id, "API-visible source evidence.", "api")
    materialize_run(db, root, r12_settings)
    scope_response = asyncio.run(get_task_research_scope(root.run_id, db))
    tree_response = asyncio.run(get_task_research_tree(root.run_id, db))
    evidence_response = asyncio.run(get_task_scope_evidence(root.run_id, db))
    assert scope_response.scope_id == scope.scope_id
    assert tree_response.scope.scope_id == scope.scope_id
    assert evidence_response.scope_id == scope.scope_id
    assert evidence_response.passages
