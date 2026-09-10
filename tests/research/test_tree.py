from app.research.scope import create_research_node, create_research_scope
from app.research.tree import get_research_tree

from .conftest import create_root


def test_tree_projection_nests_children(db):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    parent = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0,
    )
    child = create_research_node(
        db, scope.scope_id, parent_node_id=parent.node_id, run_id=None,
        node_type="query", topic="child", query="child", research_goal="child",
        depth=1, priority=1,
    )
    tree = get_research_tree(db, scope)
    assert tree["roots"][0]["node_id"] == parent.node_id
    assert tree["roots"][0]["children"][0]["node_id"] == child.node_id
