import json

from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.scope import create_research_node, create_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root, materialize_run


def _scope_with_parent_and_child(db, settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    root_node = create_research_node(
        db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="discovery", topic="root", query="root", research_goal="root",
        depth=0, priority=0, status="completed",
    )
    child = store.create_agent_run(
        db, "child", "summary", "real", allowed_tools=["web_fetcher"],
        parent_run_id=root.run_id, root_run_id=root.run_id, run_role="research_branch",
        research_scope_id=scope.scope_id, engine_version="v2",
    )
    store.update_agent_run_plan(db, child.run_id, json.loads(root.plan_json))
    create_research_node(
        db, scope.scope_id, parent_node_id=root_node.node_id, run_id=child.run_id,
        node_type="query", topic="child", query="child", research_goal="child",
        depth=1, priority=1, status="completed",
    )
    root_trace = add_web_trace(db, root.run_id, "Parent evidence establishes fact Alpha.", "parent")
    child_trace = add_web_trace(db, child.run_id, "Child evidence establishes fact Beta.", "child")
    materialize_run(db, root, settings)
    materialize_run(db, child, settings)
    return root, child, scope, root_trace, child_trace


def test_scope_bundle_contains_parent_and_child_without_copying_rows(db, r12_settings):
    root, child, scope, _, _ = _scope_with_parent_and_child(db, r12_settings)
    bundle = get_scope_provenance_bundle(db, scope)
    assert bundle["run_id"] == root.run_id
    assert bundle["schema_version"] == "research-scope-evidence-v2"
    assert bundle["extractor_version"] == "multi-run"
    assert {item["origin_run_id"] for item in bundle["passages"]} == {root.run_id, child.run_id}
    assert {item["origin_run_id"] for item in bundle["source_documents"]} == {
        root.run_id,
        child.run_id,
    }
    assert bundle["integrity"]["all_citations_resolve"] is True
    assert bundle["integrity"]["all_traceability_resolves"] is True
    assert all(item["origin_trace_id"] for item in bundle["passages"])


def test_scope_citation_labels_are_stable_and_child_traceable(db, r12_settings):
    root, child, scope, _, child_trace = _scope_with_parent_and_child(db, r12_settings)
    first = get_scope_provenance_bundle(db, scope)
    second = get_scope_provenance_bundle(db, scope)
    assert [item["citation_label"] for item in first["citations"]] == [
        item["citation_label"] for item in second["citations"]
    ]
    child_citations = [item for item in first["citations"] if item["origin_run_id"] == child.run_id]
    assert child_citations
    assert child_citations[0]["origin_trace_id"] == child_trace.trace_id
    assert first["integrity"]["child_citation_count"] >= 1
