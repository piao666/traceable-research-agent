"""Stable alias projection for raw cross-run provenance entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def source_identity_key(document: dict) -> str:
    """Return the fixed-priority identity key for a source document."""

    metadata = _mapping(document.get("metadata"))
    source_identity = _mapping(metadata.get("source_identity"))
    story_hash = _text(source_identity.get("canonical_story_hash"))
    if story_hash:
        return f"story:{story_hash}"

    independence_group = _text(source_identity.get("independence_group"))
    canonical_url = _text(
        source_identity.get("canonical_url")
        or metadata.get("canonical_url")
        or document.get("canonical_uri")
    )
    if independence_group and canonical_url:
        return f"independence:{independence_group}|url:{canonical_url}"

    canonical_uri = _text(document.get("canonical_uri"))
    if canonical_uri:
        return f"url:{canonical_uri}"

    return (
        f"origin:{_text(document.get('origin_run_id'))}"
        f"|document:{_text(document.get('document_id'))}"
    )


def passage_identity_key(
    passage: dict,
    document: dict,
) -> str:
    """Return a passage identity scoped by its fixed source identity."""

    return f"{source_identity_key(document)}|passage:{_text(passage.get('content_hash'))}"


def build_scope_identity_projection(
    entities: dict[str, list[dict[str, Any]]],
    run_rank: dict[str, int],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Build aliases and effective counts without removing raw provenance rows."""

    documents = entities.get("source_documents") or []
    snapshots = entities.get("source_snapshots") or []
    passages = entities.get("passages") or []
    assertions = entities.get("assertions") or []
    document_by_id = {
        _text(document.get("document_id")): document for document in documents
    }
    snapshot_by_id = {
        _text(snapshot.get("snapshot_id")): snapshot for snapshot in snapshots
    }
    assertions_by_passage: dict[str, list[dict[str, Any]]] = {}
    for assertion in assertions:
        assertions_by_passage.setdefault(
            _text(assertion.get("passage_id")), []
        ).append(assertion)
    passages_by_document: dict[str, list[dict[str, Any]]] = {}
    for passage in passages:
        snapshot = snapshot_by_id.get(_text(passage.get("snapshot_id")))
        if snapshot is None:
            continue
        passages_by_document.setdefault(
            _text(snapshot.get("document_id")), []
        ).append(passage)

    source_groups: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        source_groups.setdefault(source_identity_key(document), []).append(document)
    source_aliases = [
        _source_alias(
            identity_key,
            members,
            passages_by_document,
            snapshot_by_id,
            assertions_by_passage,
            run_rank,
        )
        for identity_key, members in source_groups.items()
    ]
    source_aliases.sort(key=lambda item: (item["identity_key"], item["representative_document_id"]))

    passage_groups: dict[str, list[dict[str, Any]]] = {}
    for passage in passages:
        snapshot = snapshot_by_id.get(_text(passage.get("snapshot_id")))
        document = (
            document_by_id.get(_text(snapshot.get("document_id")))
            if snapshot is not None
            else None
        )
        if document is None:
            # Broken lineage must not collapse unrelated raw rows.
            key = (
                f"unresolved:{_text(passage.get('origin_run_id'))}"
                f"|passage:{_text(passage.get('passage_id'))}"
            )
        else:
            key = passage_identity_key(passage, document)
        passage_groups.setdefault(key, []).append(passage)
    passage_aliases = [
        _passage_alias(
            identity_key,
            members,
            snapshot_by_id,
            document_by_id,
            assertions_by_passage,
            run_rank,
        )
        for identity_key, members in passage_groups.items()
    ]
    passage_aliases.sort(key=lambda item: (item["identity_key"], item["representative_passage_id"]))

    return (
        {
            "source_aliases": source_aliases,
            "passage_aliases": passage_aliases,
        },
        {
            "raw_source_count": len(documents),
            "effective_unique_source_count": len(source_aliases),
            "raw_passage_count": len(passages),
            "effective_unique_passage_count": len(passage_aliases),
        },
    )


def _source_alias(
    identity_key: str,
    members: list[dict[str, Any]],
    passages_by_document: dict[str, list[dict[str, Any]]],
    snapshot_by_id: dict[str, dict[str, Any]],
    assertions_by_passage: dict[str, list[dict[str, Any]]],
    run_rank: dict[str, int],
) -> dict[str, Any]:
    def quality(document: dict[str, Any]) -> tuple[Any, ...]:
        document_id = _text(document.get("document_id"))
        document_passages = passages_by_document.get(document_id) or []
        if document_passages:
            best_passage = min(
                document_passages,
                key=lambda passage: _representative_sort_key(
                    passage,
                    snapshot_by_id.get(_text(passage.get("snapshot_id"))),
                    document,
                    assertions_by_passage.get(_text(passage.get("passage_id"))) or [],
                    run_rank,
                    _text(passage.get("passage_id")),
                ),
            )
            return _representative_sort_key(
                best_passage,
                snapshot_by_id.get(_text(best_passage.get("snapshot_id"))),
                document,
                assertions_by_passage.get(_text(best_passage.get("passage_id"))) or [],
                run_rank,
                document_id,
            )
        return _representative_sort_key(
            {}, None, document, [], run_rank, document_id
        )

    representative = min(members, key=quality)
    ordered_members = sorted(
        members,
        key=lambda item: (
            run_rank.get(_text(item.get("origin_run_id")), 10**9),
            _text(item.get("document_id")),
        ),
    )
    return {
        "representative_document_id": _text(representative.get("document_id")),
        "member_document_ids": [_text(item.get("document_id")) for item in ordered_members],
        "origin_run_ids": _ordered_origins(ordered_members, run_rank),
        "identity_key": identity_key,
    }


def _passage_alias(
    identity_key: str,
    members: list[dict[str, Any]],
    snapshot_by_id: dict[str, dict[str, Any]],
    document_by_id: dict[str, dict[str, Any]],
    assertions_by_passage: dict[str, list[dict[str, Any]]],
    run_rank: dict[str, int],
) -> dict[str, Any]:
    def quality(passage: dict[str, Any]) -> tuple[Any, ...]:
        snapshot = snapshot_by_id.get(_text(passage.get("snapshot_id")))
        document = (
            document_by_id.get(_text(snapshot.get("document_id")))
            if snapshot is not None
            else None
        )
        return _representative_sort_key(
            passage,
            snapshot,
            document or {},
            assertions_by_passage.get(_text(passage.get("passage_id"))) or [],
            run_rank,
            _text(passage.get("passage_id")),
        )

    representative = min(members, key=quality)
    ordered_members = sorted(
        members,
        key=lambda item: (
            run_rank.get(_text(item.get("origin_run_id")), 10**9),
            _text(item.get("passage_id")),
        ),
    )
    return {
        "representative_passage_id": _text(representative.get("passage_id")),
        "member_passage_ids": [_text(item.get("passage_id")) for item in ordered_members],
        "origin_run_ids": _ordered_origins(ordered_members, run_rank),
        "identity_key": identity_key,
    }


def _representative_sort_key(
    passage: dict[str, Any],
    snapshot: dict[str, Any] | None,
    document: dict[str, Any],
    assertions: list[dict[str, Any]],
    run_rank: dict[str, int],
    entity_id: str,
) -> tuple[Any, ...]:
    passage_metadata = _mapping(passage.get("metadata"))
    snapshot_metadata = _mapping((snapshot or {}).get("metadata"))
    confidence_values = [
        _float(passage_metadata.get("extraction_confidence")),
        _float(snapshot_metadata.get("extraction_confidence")),
        *[_float(item.get("extraction_confidence")) for item in assertions],
    ]
    source_tier = _text(_mapping(document.get("metadata")).get("source_tier")).upper()
    tier_rank = {"T0": 3, "T1": 2, "T2": 1}.get(source_tier, 0)
    origin_run_id = _text(passage.get("origin_run_id") or document.get("origin_run_id"))
    return (
        -int(_text(passage.get("content_basis")).casefold() == "full_text"),
        -max(confidence_values, default=0.0),
        -tier_rank,
        -_timestamp((snapshot or {}).get("fetched_at")),
        run_rank.get(origin_run_id, 10**9),
        entity_id,
    )


def _ordered_origins(
    members: list[dict[str, Any]], run_rank: dict[str, int]
) -> list[str]:
    origins = {_text(item.get("origin_run_id")) for item in members}
    origins.discard("")
    return sorted(origins, key=lambda value: (run_rank.get(value, 10**9), value))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(_text(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


__all__ = [
    "build_scope_identity_projection",
    "passage_identity_key",
    "source_identity_key",
]
