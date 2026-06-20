"""Reciprocal-rank fusion for dense and sparse RAG hits."""

from __future__ import annotations

from typing import Any


def reciprocal_rank_fusion(
    result_lists: list[list[dict[str, Any]]],
    k: int = 60,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Fuse dense then BM25 lists, deduplicating by source and chunk id."""

    rrf_k = max(1, min(int(k), 1000))
    fused: dict[tuple[str, str], dict[str, Any]] = {}
    labels = ("dense", "bm25")
    for list_index, hits in enumerate(result_lists):
        label = labels[list_index] if list_index < len(labels) else f"source_{list_index}"
        for rank, hit in enumerate(hits, start=1):
            key = (str(hit.get("source") or ""), str(hit.get("chunk_id") or ""))
            entry = fused.setdefault(
                key,
                {
                    "source": key[0],
                    "chunk_id": key[1],
                    "text": str(hit.get("text") or ""),
                    "score": 0.0,
                    "metadata": {"retrieval_mode": "hybrid"},
                },
            )
            contribution = 1.0 / (rrf_k + rank)
            entry["score"] += contribution
            entry["metadata"][f"{label}_rank"] = rank
            entry["metadata"][f"{label}_score"] = hit.get("score")
            entry["metadata"]["rrf_score"] = entry["score"]
    ranked = sorted(
        fused.values(),
        key=lambda hit: (-float(hit["score"]), hit["source"], hit["chunk_id"]),
    )
    for hit in ranked:
        hit["score"] = round(float(hit["score"]), 8)
        hit["metadata"]["rrf_score"] = hit["score"]
    return ranked[: max(1, int(top_k))]
