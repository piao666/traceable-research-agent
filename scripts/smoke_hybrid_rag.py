"""Offline smoke coverage for BM25, RRF hybrid retrieval, and chunk experiments."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.rag.bm25_backend import BM25RetrievalBackend, tokenize_query, tokenize_text
from app.rag.build_index import build_local_index
from app.rag.hybrid_search import reciprocal_rank_fusion
from app.tools.rag_search import search_rag
from scripts.run_rag_chunk_experiment import run_experiment


def main() -> None:
    assert tokenize_text("Trace Registry 2026")
    assert tokenize_query("可追踪研究代理")
    chunks = [
        {"chunk_id": "a#0", "source": "a.md", "text": "trace persistence registry", "metadata": {}},
        {"chunk_id": "b#0", "source": "b.md", "text": "hybrid retrieval with bm25", "metadata": {}},
    ]
    smoke_path = ROOT / "workspace" / "tmp" / "bm25_smoke.json"
    backend = BM25RetrievalBackend(smoke_path)
    assert backend.build(chunks)["success"]
    assert backend.save()["success"]
    bm25_result = backend.search("trace registry", 2)
    assert bm25_result.success and bm25_result.hits
    assert bm25_result.hits[0]["metadata"]["retrieval_mode"] == "bm25"

    build = build_local_index()
    assert build["success"] and build["bm25_corpus_size"] > 0
    dense = search_rag({"query": "trace persistence tool registry", "top_k": 3, "retrieval_mode": "dense"})
    sparse = search_rag({"query": "trace persistence tool registry", "top_k": 3, "retrieval_mode": "bm25"})
    hybrid = search_rag({"query": "trace persistence tool registry", "top_k": 3, "retrieval_mode": "hybrid"})
    assert dense.success and dense.metadata["retrieval_mode"] == "dense"
    assert sparse.success and sparse.metadata["retrieval_mode"] == "bm25"
    assert hybrid.success and hybrid.metadata["retrieval_mode"] == "hybrid"
    assert hybrid.metadata["fallback_used"] is False
    assert hybrid.metadata["rrf_k"] == settings.rag_rrf_k
    assert hybrid.output["hits"][0]["metadata"].get("rrf_score") is not None
    fused = reciprocal_rank_fusion([dense.output["hits"], sparse.output["hits"]], 60, 3)
    assert fused and fused[0]["metadata"]["retrieval_mode"] == "hybrid"

    import app.tools.rag_retrieval as retrieval

    original_bm25 = retrieval.BM25_INDEX
    retrieval.BM25_INDEX = ROOT / "workspace" / "tmp" / "missing_bm25.json"
    try:
        fallback = search_rag({"query": "trace registry", "top_k": 2, "retrieval_mode": "hybrid"})
    finally:
        retrieval.BM25_INDEX = original_bm25
    assert fallback.success and fallback.metadata["retrieval_mode"] == "dense"
    assert fallback.metadata["fallback_used"] is True

    experiment = run_experiment()
    assert [item["chunk_size"] for item in experiment["results"]] == [256, 512, 1024]
    assert (ROOT / "docs" / "rag_chunk_experiment.md").exists()
    assert (ROOT / settings.rag_chunk_experiment_output).exists()
    print(
        json.dumps(
            {
                "hybrid_rag": "ok",
                "bm25": "ok",
                "dense_regression": "ok",
                "hybrid": "ok",
                "fallback": "ok",
                "chunk_experiment": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
