from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.agent.reporter import _render_reasoning_markdown
from app.evidence.models import (
    EvidenceAssertion,
    EvidencePassage,
    ResearchClaim,
    ScopeClaimGroup,
    ScopeReasoningRun,
    SourceDocument,
    SourceSnapshot,
)
from app.evidence.scope_reasoning import (
    materialize_scope_reasoning,
    scope_claim_group_key,
    scope_source_cluster_id,
)
from app.evidence.scope_service import get_scope_provenance_bundle
from app.research.scope import create_research_node, create_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root, materialize_run


POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "source_policy.v2.json"


def _scope_runs(db, settings, count: int = 2):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    root_node = create_research_node(
        db,
        scope.scope_id,
        parent_node_id=None,
        run_id=root.run_id,
        node_type="discovery",
        topic="root",
        query="root",
        research_goal="root",
        depth=0,
        priority=0,
        status="completed",
    )
    runs = [root]
    for index in range(1, count):
        child = store.create_agent_run(
            db,
            f"child-{index}",
            "summary",
            "real",
            allowed_tools=["web_fetcher"],
            parent_run_id=root.run_id,
            root_run_id=root.run_id,
            run_role="research_branch",
            research_scope_id=scope.scope_id,
            engine_version="v2",
        )
        store.update_agent_run_plan(db, child.run_id, json.loads(root.plan_json))
        create_research_node(
            db,
            scope.scope_id,
            parent_node_id=root_node.node_id,
            run_id=child.run_id,
            node_type="query",
            topic=f"child-{index}",
            query=f"child-{index}",
            research_goal=f"child-{index}",
            depth=1,
            priority=index,
            status="completed",
        )
        runs.append(store.get_agent_run(db, child.run_id))
    for index, run in enumerate(runs):
        add_web_trace(
            db,
            run.run_id,
            f"Market size in 2025 is {100 + index * 30} USD.",
            f"source-{index}",
        )
        materialize_run(db, run, settings)
    return scope, runs


def _set_fact(
    db,
    run_id: str,
    *,
    value: float,
    time_scope: str = "2025",
    independence_group: str | None = None,
    passage_hash: str | None = None,
) -> None:
    claim = db.scalars(select(ResearchClaim).where(ResearchClaim.run_id == run_id)).first()
    assertion = db.scalars(
        select(EvidenceAssertion)
        .join(EvidencePassage, EvidenceAssertion.passage_id == EvidencePassage.passage_id)
        .join(SourceSnapshot, EvidencePassage.snapshot_id == SourceSnapshot.snapshot_id)
        .join(SourceDocument, SourceSnapshot.document_id == SourceDocument.document_id)
        .where(SourceDocument.run_id == run_id)
    ).first()
    passage = db.get(EvidencePassage, assertion.passage_id)
    snapshot = db.get(SourceSnapshot, passage.snapshot_id)
    document = db.get(SourceDocument, snapshot.document_id)
    text = f"Market size in {time_scope} is {value:g} USD."
    claim.claim_text = text
    claim.value_json = json.dumps({"value": value})
    claim.unit = "USD"
    claim.time_scope = time_scope
    assertion.object_text = text
    assertion.value_json = json.dumps({"value": value})
    assertion.unit = "USD"
    assertion.time_scope = time_scope
    assertion.polarity = "positive"
    if passage_hash is not None:
        passage.content_hash = passage_hash
    metadata = json.loads(document.metadata_json or "{}")
    source_identity = dict(metadata.get("source_identity") or {})
    source_identity.pop("canonical_story_hash", None)
    if independence_group is not None:
        source_identity["independence_group"] = independence_group
    else:
        source_identity.pop("independence_group", None)
    metadata["source_identity"] = source_identity
    document.metadata_json = json.dumps(metadata)
    db.commit()


def test_numeric_values_do_not_split_the_scope_claim_group():
    first = {"claim_text": "Market size in 2025 is 100 USD", "unit": "USD", "value": {"value": 100}}
    second = {"claim_text": "Market size in 2025 is 130 USD", "unit": "USD", "value": {"value": 130}}
    assert scope_claim_group_key(first) == scope_claim_group_key(second)
    assert scope_claim_group_key(
        {"claim_text": "市场规模为100亿美元"}
    ) == scope_claim_group_key({"claim_text": "市场规模为130亿美元"})
    assert scope_claim_group_key(
        {"claim_text": "市场规模在2025年为100亿美元"}
    ) == scope_claim_group_key({"claim_text": "市场规模在2024年为130亿美元"})


def test_root_100_and_child_130_form_one_cross_run_conflict_group(db, r12_settings):
    scope, runs = _scope_runs(db, r12_settings)
    _set_fact(db, runs[0].run_id, value=100, independence_group="root-source")
    _set_fact(db, runs[1].run_id, value=130, independence_group="child-source")

    result = materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)

    assert result["reasoning"]["engine_version"] == "scope-reasoning-v1"
    assert result["reasoning"]["status"] == "complete"
    assert len(result["scope_claim_groups"]) == 1
    assert len(result["scope_claim_groups"][0]["members"]) == 2
    relations = result["scope_resolutions"][0]["rationale"]["relations"]
    assert {item["relation"] for item in relations} == {"supports", "refutes"}


def test_three_syndicated_domains_count_as_one_independent_support(db, r12_settings):
    scope, runs = _scope_runs(db, r12_settings, count=3)
    for run in runs:
        _set_fact(
            db,
            run.run_id,
            value=100,
            independence_group="wire-story-one",
            passage_hash="same-passage",
        )

    result = materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)
    resolution = result["scope_resolutions"][0]

    assert resolution["independent_support_count"] == 1
    assert {
        item["source_cluster_id"]
        for item in resolution["rationale"]["relations"]
    } == {
        scope_source_cluster_id(
            {
                "metadata": {
                    "source_identity": {"independence_group": "wire-story-one"}
                }
            },
            {"content_hash": "same-passage"},
        )
    }


def test_different_time_scopes_are_scope_differences_not_plain_conflicts(db, r12_settings):
    scope, runs = _scope_runs(db, r12_settings)
    _set_fact(db, runs[0].run_id, value=100, time_scope="2025", independence_group="a")
    _set_fact(db, runs[1].run_id, value=130, time_scope="2024", independence_group="b")

    result = materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)
    resolution = result["scope_resolutions"][0]

    assert resolution["status"] == "resolved_by_scope"
    assert "time" in resolution["rationale"]["scope_differences"]
    assert any(
        item["scope_difference"] == "time"
        for item in resolution["rationale"]["relations"]
    )


def test_same_evidence_fingerprint_is_idempotent_and_raw_evidence_remains(db, r12_settings):
    scope, runs = _scope_runs(db, r12_settings)
    for run in runs:
        _set_fact(
            db,
            run.run_id,
            value=100,
            independence_group="same-source",
            passage_hash="same-passage",
        )
    raw_before = get_scope_provenance_bundle(db, scope)["passages"]

    first = materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)
    second = materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)

    assert first["reasoning"]["reasoning_run_id"] == second["reasoning"]["reasoning_run_id"]
    assert db.scalar(select(func.count()).select_from(ScopeReasoningRun)) == 1
    assert db.scalar(select(func.count()).select_from(ScopeClaimGroup)) == 1
    assert len(get_scope_provenance_bundle(db, scope)["passages"]) == len(raw_before) == 2
    assert second["scope_resolutions"][0]["independent_support_count"] == 1


def test_scope_reporter_renders_cross_run_conflict(db, r12_settings):
    scope, runs = _scope_runs(db, r12_settings)
    _set_fact(db, runs[0].run_id, value=100, independence_group="root-source")
    _set_fact(db, runs[1].run_id, value=130, independence_group="child-source")
    materialize_scope_reasoning(db, scope.scope_id, POLICY_PATH)

    lines = _render_reasoning_markdown(get_scope_provenance_bundle(db, scope))
    rendered = "\n".join(lines)

    assert "跨 Run 冲突状态" in rendered
    assert runs[0].run_id in rendered
    assert runs[1].run_id in rendered
