from copy import deepcopy

from app.evidence.scope_identity import (
    build_scope_identity_projection,
    passage_identity_key,
    source_identity_key,
)


def _document(
    document_id: str,
    run_id: str,
    uri: str,
    *,
    story_hash: str = "",
    source_tier: str = "T2",
) -> dict:
    source_identity = {}
    if story_hash:
        source_identity["canonical_story_hash"] = story_hash
    return {
        "document_id": document_id,
        "origin_run_id": run_id,
        "canonical_uri": uri,
        "metadata": {
            "source_identity": source_identity,
            "source_tier": source_tier,
        },
    }


def _snapshot(
    snapshot_id: str,
    document_id: str,
    run_id: str,
    *,
    fetched_at: str,
    confidence: float,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "document_id": document_id,
        "origin_run_id": run_id,
        "fetched_at": fetched_at,
        "metadata": {"extraction_confidence": confidence},
    }


def _passage(
    passage_id: str,
    snapshot_id: str,
    run_id: str,
    content_hash: str,
    *,
    content_basis: str = "full_text",
) -> dict:
    return {
        "passage_id": passage_id,
        "snapshot_id": snapshot_id,
        "origin_run_id": run_id,
        "content_hash": content_hash,
        "content_basis": content_basis,
        "metadata": {},
    }


def _entities(documents: list[dict], snapshots: list[dict], passages: list[dict]) -> dict:
    return {
        "source_documents": documents,
        "source_snapshots": snapshots,
        "passages": passages,
        "assertions": [],
    }


def test_source_identity_uses_the_fixed_priority_order():
    document = _document(
        "doc-a", "root", "https://example.com/canonical", story_hash="story-a"
    )
    document["metadata"]["canonical_url"] = "https://example.com/metadata"
    document["metadata"]["source_identity"]["independence_group"] = "group-a"
    assert source_identity_key(document) == "story:story-a"

    del document["metadata"]["source_identity"]["canonical_story_hash"]
    assert source_identity_key(document) == (
        "independence:group-a|url:https://example.com/metadata"
    )
    del document["metadata"]["source_identity"]["independence_group"]
    assert source_identity_key(document) == "url:https://example.com/canonical"

    document["canonical_uri"] = ""
    document["metadata"].pop("canonical_url")
    assert source_identity_key(document) == "origin:root|document:doc-a"


def test_root_and_child_duplicate_rows_become_aliases_without_losing_raw_provenance():
    documents = [
        _document("doc-root", "root", "https://example.com/same"),
        _document("doc-child", "child", "https://example.com/same"),
    ]
    snapshots = [
        _snapshot("snap-root", "doc-root", "root", fetched_at="2026-09-01T00:00:00+00:00", confidence=0.9),
        _snapshot("snap-child", "doc-child", "child", fetched_at="2026-09-02T00:00:00+00:00", confidence=0.9),
    ]
    passages = [
        _passage("pass-root", "snap-root", "root", "same-hash"),
        _passage("pass-child", "snap-child", "child", "same-hash"),
    ]
    entities = _entities(documents, snapshots, passages)
    raw_before = deepcopy(entities)

    identity, metrics = build_scope_identity_projection(entities, {"root": 0, "child": 1})

    assert entities == raw_before
    assert metrics == {
        "raw_source_count": 2,
        "effective_unique_source_count": 1,
        "raw_passage_count": 2,
        "effective_unique_passage_count": 1,
    }
    assert identity["source_aliases"][0]["member_document_ids"] == ["doc-root", "doc-child"]
    assert identity["source_aliases"][0]["origin_run_ids"] == ["root", "child"]
    assert identity["passage_aliases"][0]["member_passage_ids"] == ["pass-root", "pass-child"]
    assert identity["passage_aliases"][0]["origin_run_ids"] == ["root", "child"]


def test_canonical_story_hash_aliases_different_urls():
    documents = [
        _document("doc-a", "root", "https://publisher-a.example/story", story_hash="story-one"),
        _document("doc-b", "child", "https://publisher-b.example/copy", story_hash="story-one"),
    ]
    snapshots = [
        _snapshot("snap-a", "doc-a", "root", fetched_at="2026-09-01T00:00:00+00:00", confidence=0.8),
        _snapshot("snap-b", "doc-b", "child", fetched_at="2026-09-01T00:00:00+00:00", confidence=0.8),
    ]
    passages = [
        _passage("pass-a", "snap-a", "root", "same-passage"),
        _passage("pass-b", "snap-b", "child", "same-passage"),
    ]

    identity, metrics = build_scope_identity_projection(
        _entities(documents, snapshots, passages), {"root": 0, "child": 1}
    )

    assert metrics["effective_unique_source_count"] == 1
    assert metrics["effective_unique_passage_count"] == 1
    assert identity["source_aliases"][0]["identity_key"] == "story:story-one"


def test_same_url_with_different_passage_hashes_remains_two_effective_passages():
    document = _document("doc-root", "root", "https://example.com/source")
    snapshot = _snapshot(
        "snap-root",
        "doc-root",
        "root",
        fetched_at="2026-09-01T00:00:00+00:00",
        confidence=0.8,
    )
    passages = [
        _passage("pass-a", "snap-root", "root", "hash-a"),
        _passage("pass-b", "snap-root", "root", "hash-b"),
    ]

    identity, metrics = build_scope_identity_projection(
        _entities([document], [snapshot], passages), {"root": 0}
    )

    assert metrics["effective_unique_source_count"] == 1
    assert metrics["effective_unique_passage_count"] == 2
    assert len(identity["passage_aliases"]) == 2
    assert passage_identity_key(passages[0], document) != passage_identity_key(passages[1], document)


def test_full_text_is_the_representative_before_confidence_tier_recency_and_run_rank():
    documents = [
        _document("doc-root", "root", "https://example.com/same", source_tier="T0"),
        _document("doc-child", "child", "https://example.com/same", source_tier="T2"),
    ]
    snapshots = [
        _snapshot("snap-root", "doc-root", "root", fetched_at="2026-09-10T00:00:00+00:00", confidence=1.0),
        _snapshot("snap-child", "doc-child", "child", fetched_at="2026-09-01T00:00:00+00:00", confidence=0.1),
    ]
    passages = [
        _passage("pass-root", "snap-root", "root", "same-hash", content_basis="snippet_only"),
        _passage("pass-child", "snap-child", "child", "same-hash", content_basis="full_text"),
    ]

    identity, _ = build_scope_identity_projection(
        _entities(documents, snapshots, passages), {"root": 0, "child": 1}
    )

    assert identity["source_aliases"][0]["representative_document_id"] == "doc-child"
    assert identity["passage_aliases"][0]["representative_passage_id"] == "pass-child"
