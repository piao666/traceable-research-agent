from app.research.models import ResearchNode, ResearchScope
from app.research.scope import create_research_node, create_research_scope

from .conftest import create_root


def test_scope_and_root_node_are_persisted_with_explicit_lineage(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {"goal_kind": "research"})
    node = create_research_node(
        db,
        scope.scope_id,
        parent_node_id=None,
        run_id=root.run_id,
        node_type="discovery",
        topic=root.task,
        query=root.task,
        research_goal=root.task,
        depth=0,
        priority=0,
    )
    db.expire_all()
    persisted_root = db.get(type(root), root.run_id)
    assert db.get(ResearchScope, scope.scope_id) is not None
    assert db.get(ResearchNode, node.node_id) is not None
    assert persisted_root.root_run_id == root.run_id
    assert persisted_root.parent_run_id is None
    assert persisted_root.run_role == "root"
    assert persisted_root.research_scope_id == scope.scope_id
    assert persisted_root.engine_version == "v2"
