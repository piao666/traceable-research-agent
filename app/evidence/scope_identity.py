"""Stable alias projection for raw cross-run provenance entities."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from app.retrieval.source_identity import (
    SYNDICATION_BODY_CHAR_LIMIT,
    syndication_near_duplicate,
)


_MAX_SYNDICATION_CANDIDATES_PER_WIRE = 256


def source_resource_key(document: dict) -> str:
    """Return the stable alias key for one original Resource."""

    metadata = _mapping(document.get("metadata"))
    source_identity = _mapping(metadata.get("source_identity"))
    resource_kind = _text(source_identity.get("resource_identity_kind")).casefold()
    resource_identity = _text(source_identity.get("resource_identity"))
    if resource_identity:
        return f"resource:{resource_kind or 'unknown'}:{resource_identity}"
    canonical_uri = _text(
        document.get("canonical_uri")
        or source_identity.get("canonical_url")
        or metadata.get("canonical_url")
    )
    if canonical_uri:
        return f"resource:canonical_url:{canonical_uri}"
    return (
        f"origin:{_text(document.get('origin_run_id'))}"
        f"|document:{_text(document.get('document_id'))}"
    )


def source_independence_key(
    document: dict,
    passage: dict | None = None,
    *,
    resolved_group: str | None = None,
) -> str:
    """Return the central Resource/Story independence key.

    Explicit syndication candidates may share one story-level independence
    key. All other newly materialized web sources prefer their stable Resource
    identity, so backend-specific Snapshot text never changes independence.
    """

    if resolved_group:
        return resolved_group
    metadata = _mapping(document.get("metadata"))
    source_identity = _mapping(metadata.get("source_identity"))
    original = _text(
        source_identity.get("original_publisher")
        or source_identity.get("syndication_source")
    )
    independence_group = _text(source_identity.get("independence_group"))
    if original and independence_group:
        return f"syndication:{original.casefold()}|group:{independence_group}"
    if independence_group:
        return f"independence:{independence_group}"
    resource_key = source_resource_key(document)
    if not resource_key.startswith("origin:"):
        return resource_key

    story_hash = _text(source_identity.get("canonical_story_hash"))
    if story_hash:
        return f"story:{story_hash}"

    canonical_uri = _text(
        document.get("canonical_uri")
        or source_identity.get("canonical_url")
        or metadata.get("canonical_url")
    )
    if canonical_uri:
        return f"url:{canonical_uri}"

    passage_hash = _text((passage or {}).get("content_hash"))
    if passage_hash:
        return f"passage:{passage_hash}"

    return (
        f"origin:{_text(document.get('origin_run_id'))}"
        f"|document:{_text(document.get('document_id'))}"
    )


def source_identity_key(document: dict) -> str:
    """Compatibility name for the stable Resource alias key."""

    return source_resource_key(document)


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
        source_groups.setdefault(source_resource_key(document), []).append(document)
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
    independence_aliases = _build_independence_aliases(
        source_aliases,
        document_by_id,
        passages_by_document,
        run_rank,
    )

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
            "independence_aliases": independence_aliases,
            "passage_aliases": passage_aliases,
        },
        {
            "raw_source_count": len(documents),
            "unique_resource_count": len(source_aliases),
            "independent_source_count": len(independence_aliases),
            "effective_unique_source_count": len(source_aliases),
            "raw_passage_count": len(passages),
            "effective_unique_passage_count": len(passage_aliases),
        },
    )


def _build_independence_aliases(
    source_aliases: list[dict[str, Any]],
    document_by_id: dict[str, dict[str, Any]],
    passages_by_document: dict[str, list[dict[str, Any]]],
    run_rank: dict[str, int],
) -> list[dict[str, Any]]:
    """Cluster Resource aliases, fuzzing only bounded same-wire candidates."""

    candidates: list[dict[str, Any]] = []
    for alias in source_aliases:
        representative = document_by_id.get(
            _text(alias.get("representative_document_id")), {}
        )
        member_document_ids = [
            _text(value) for value in alias.get("member_document_ids") or []
        ]
        passages = [
            passage
            for document_id in member_document_ids
            for passage in passages_by_document.get(document_id) or []
        ]
        content = "\n".join(
            _text(item.get("text"))
            for item in sorted(
                passages,
                key=lambda item: (
                    run_rank.get(_text(item.get("origin_run_id")), 10**9),
                    _text(item.get("passage_id")),
                ),
            )
            if _text(item.get("text"))
        )[:SYNDICATION_BODY_CHAR_LIMIT]
        metadata = _mapping(representative.get("metadata"))
        source_identity = _mapping(metadata.get("source_identity"))
        wire_service = _text(
            source_identity.get("original_publisher")
            or source_identity.get("syndication_source")
        ).casefold()
        first_passage = passages[0] if passages else None
        candidates.append(
            {
                "alias": alias,
                "base_key": source_independence_key(representative, first_passage),
                "wire_service": wire_service,
                "title": _text(
                    representative.get("title")
                    or source_identity.get("normalized_title")
                ),
                "content": content,
                "published_at": metadata.get("published_at") or source_identity.get("published_at"),
            }
        )

    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    exact_keys: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        base_key = _text(candidate.get("base_key"))
        if base_key in exact_keys:
            union(index, exact_keys[base_key])
        else:
            exact_keys[base_key] = index

    wire_indexes: dict[str, list[int]] = {}
    for index, candidate in enumerate(candidates):
        wire_service = _text(candidate.get("wire_service"))
        if wire_service:
            wire_indexes.setdefault(wire_service, []).append(index)
    for indexes in wire_indexes.values():
        bounded = indexes[:_MAX_SYNDICATION_CANDIDATES_PER_WIRE]
        for position, first_index in enumerate(bounded):
            first = candidates[first_index]
            for second_index in bounded[position + 1 :]:
                second = candidates[second_index]
                if syndication_near_duplicate(
                    first_wire_service=first["wire_service"],
                    first_title=first["title"],
                    first_content=first["content"],
                    first_published_at=first["published_at"],
                    second_wire_service=second["wire_service"],
                    second_title=second["title"],
                    second_content=second["content"],
                    second_published_at=second["published_at"],
                ):
                    union(first_index, second_index)

    clusters: dict[int, list[dict[str, Any]]] = {}
    for index, candidate in enumerate(candidates):
        clusters.setdefault(find(index), []).append(candidate)
    aliases: list[dict[str, Any]] = []
    for members in clusters.values():
        resource_keys = sorted(
            _text(item["alias"].get("identity_key")) for item in members
        )
        base_keys = {_text(item.get("base_key")) for item in members}
        if len(base_keys) == 1:
            identity_key = next(iter(base_keys))
        else:
            wire_service = min(
                _text(item.get("wire_service")) for item in members if item.get("wire_service")
            )
            digest = hashlib.sha256("\x1f".join(resource_keys).encode("utf-8")).hexdigest()[:32]
            identity_key = f"syndication:{wire_service}|near:{digest}"
        document_ids = sorted(
            {
                _text(document_id)
                for item in members
                for document_id in item["alias"].get("member_document_ids") or []
            },
            key=lambda document_id: (
                run_rank.get(
                    _text(document_by_id.get(document_id, {}).get("origin_run_id")),
                    10**9,
                ),
                document_id,
            ),
        )
        aliases.append(
            {
                "representative_document_id": document_ids[0] if document_ids else "",
                "member_document_ids": document_ids,
                "resource_identity_keys": resource_keys,
                "identity_key": identity_key,
            }
        )
    aliases.sort(key=lambda item: (item["identity_key"], item["representative_document_id"]))
    return aliases


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
    origin_run_id = _text(passage.get("origin_run_id") or document.get("origin_run_id"))
    traceability_complete = bool(
        _text(passage.get("trace_id"))
        and _text((snapshot or {}).get("snapshot_id"))
        and _text(document.get("document_id"))
    )
    return (
        -int(_text(passage.get("content_basis")).casefold() == "full_text"),
        -max(confidence_values, default=0.0),
        -int(traceability_complete),
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
    "source_independence_key",
    "source_identity_key",
    "source_resource_key",
]
