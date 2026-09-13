from app.evidence.scope_identity import build_scope_identity_projection
from app.retrieval.source_identity import source_lineage


def _document(document_id: str, run_id: str, url: str, content: str, **metadata):
    lineage = source_lineage(url, content, metadata)
    return {
        "document_id": document_id,
        "origin_run_id": run_id,
        "canonical_uri": url,
        "metadata": {"source_identity": lineage.to_dict()},
    }


def _projection(documents):
    snapshots = [
        {
            "snapshot_id": f"snapshot-{index}",
            "document_id": document["document_id"],
            "origin_run_id": document["origin_run_id"],
            "fetched_at": "2026-09-13T00:00:00+00:00",
            "metadata": {"extraction_confidence": 0.8},
        }
        for index, document in enumerate(documents)
    ]
    passages = [
        {
            "passage_id": f"passage-{index}",
            "snapshot_id": snapshot["snapshot_id"],
            "origin_run_id": snapshot["origin_run_id"],
            "content_hash": f"snapshot-content-{index}",
            "content_basis": "full_text",
            "metadata": {},
        }
        for index, snapshot in enumerate(snapshots)
    ]
    return build_scope_identity_projection(
        {
            "source_documents": documents,
            "source_snapshots": snapshots,
            "passages": passages,
            "assertions": [],
        },
        {document["origin_run_id"]: index for index, document in enumerate(documents)},
    )


def test_same_canonical_resource_http_and_browser_use_one_source():
    documents = [
        _document(
            "http-doc",
            "http-run",
            "https://example.com/article/123",
            "HTTP extraction contains the complete article and navigation.",
            title="Stable article",
            fetch_backend="http",
        ),
        _document(
            "browser-doc",
            "browser-run",
            "https://example.com/article/123",
            "Browser extraction contains the article without navigation.",
            title="Stable article",
            fetch_backend="browser",
        ),
    ]

    identity, metrics = _projection(documents)

    assert metrics["effective_unique_source_count"] == 1
    assert len(identity["source_aliases"][0]["member_document_ids"]) == 2
    assert (
        documents[0]["metadata"]["source_identity"]["canonical_story_hash"]
        != documents[1]["metadata"]["source_identity"]["canonical_story_hash"]
    )


def test_same_canonical_resource_http_and_remote_use_one_source():
    documents = [
        _document(
            "http-doc",
            "http-run",
            "https://example.com/article/123",
            "Complete source with an additional final paragraph.",
            title="Stable article",
        ),
        _document(
            "remote-doc",
            "remote-run",
            "https://example.com/article/123",
            "Complete source.",
            title="Stable article",
        ),
    ]

    _, metrics = _projection(documents)

    assert metrics["effective_unique_source_count"] == 1
    assert metrics["effective_unique_passage_count"] == 2


def test_same_hostname_different_paths_are_distinct_resources():
    documents = [
        _document("doc-a", "run-a", "https://example.com/a", "Article A."),
        _document("doc-b", "run-b", "https://example.com/b", "Article B."),
    ]

    _, metrics = _projection(documents)

    assert metrics["effective_unique_source_count"] == 2


def test_reuters_light_rewrite_uses_one_controlled_syndication_group():
    documents = [
        _document(
            "doc-a",
            "run-a",
            "https://publisher-a.example/story",
            "Reporting by Reuters. The company announced revenue growth today.",
            title="Company announces annual revenue growth",
        ),
        _document(
            "doc-b",
            "run-b",
            "https://publisher-b.example/reprint",
            "Reuters reporting said the company posted higher annual revenue.",
            title="Company announces annual revenue growth",
        ),
    ]

    _, metrics = _projection(documents)

    assert metrics["effective_unique_source_count"] == 1


def test_unrelated_similar_title_pages_do_not_fuzzy_collapse():
    documents = [
        _document(
            "doc-a",
            "run-a",
            "https://example.com/a",
            "Independent analysis of market A.",
            title="Annual market update",
        ),
        _document(
            "doc-b",
            "run-b",
            "https://example.com/b",
            "Independent analysis of market B.",
            title="Annual market update",
        ),
    ]

    _, metrics = _projection(documents)

    assert metrics["effective_unique_source_count"] == 2
