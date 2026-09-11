import asyncio
import json

from app.api.tasks import (
    _export_run_evidence,
    get_task_research_scope,
    get_task_research_tree,
    get_task_result_context,
    get_task_result_evidence,
    get_task_result_trace,
    get_task_scope_evidence,
)
from app.agent.evidence_exporter import resolve_export_path
from app.research.scope import create_research_node, create_research_scope

from .conftest import add_web_trace, create_root, materialize_run
from .test_scope_evidence import _scope_with_parent_and_child


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


def test_result_apis_resolve_an_ordinary_run_without_scope(db, r12_settings):
    root = create_root(db)
    trace = add_web_trace(db, root.run_id, "Ordinary result evidence.", "ordinary")
    materialize_run(db, root, r12_settings)

    context = asyncio.run(get_task_result_context(root.run_id, db))
    evidence = asyncio.run(get_task_result_evidence(root.run_id, db))
    traces = asyncio.run(get_task_result_trace(root.run_id, db))

    assert context.is_scope is False
    assert context.root_run_id == root.run_id
    assert context.member_run_ids == [root.run_id]
    assert evidence.run_id == root.run_id
    assert evidence.passages
    assert traces[0].origin_run_id == root.run_id
    assert traces[0].research_node_id is None
    assert traces[0].trace_id == trace.trace_id


def test_result_apis_and_export_include_child_origins(db, r12_settings):
    root, child, scope, _, child_trace = _scope_with_parent_and_child(db, r12_settings)

    context = asyncio.run(get_task_result_context(child.run_id, db))
    evidence = asyncio.run(get_task_result_evidence(root.run_id, db))
    traces = asyncio.run(get_task_result_trace(root.run_id, db))

    assert context.requested_run_id == child.run_id
    assert context.root_run_id == root.run_id
    assert context.scope_id == scope.scope_id
    assert set(context.member_run_ids) == {root.run_id, child.run_id}
    assert {item["origin_run_id"] for item in evidence.passages} == {
        root.run_id,
        child.run_id,
    }
    child_response = next(item for item in traces if item.trace_id == child_trace.trace_id)
    assert child_response.origin_run_id == child.run_id
    assert child_response.research_node_id

    exported = _export_run_evidence(db, root.run_id, "json")
    export_path = resolve_export_path(exported.export_path)
    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        assert any(
            item.get("origin_run_id") == child.run_id
            for item in payload["passages"]
        )
    finally:
        export_path.unlink(missing_ok=True)
