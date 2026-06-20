"""Run a deterministic chunk-size retrieval experiment and write JSON/Markdown."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, settings
from app.rag.bm25_backend import BM25RetrievalBackend
from app.rag.chunker import chunk_documents
from app.rag.embedding_backends import DeterministicEmbeddingBackend
from app.rag.hybrid_search import reciprocal_rank_fusion
from app.rag.loader import load_documents
from app.rag.vector_backends import JsonVectorBackend


CASES = [
    {"query": "project goal task oriented research backend", "expected_keywords": ["task-oriented research backend"]},
    {"query": "tool registry input schema risk level", "expected_keywords": ["input schema", "risk level"]},
    {"query": "trace persistence failed safety rejection", "expected_keywords": ["safety rejections", "trace records"]},
    {"query": "file reader workspace docs restriction", "expected_keywords": ["workspace/docs"]},
    {"query": "SQL read only demo SQLite", "expected_keywords": ["read-only", "sqlite"]},
    {"query": "RAG deterministic lightweight embeddings", "expected_keywords": ["deterministic lightweight embeddings"]},
    {"query": "destructive SQL keywords rejected", "expected_keywords": ["destructive keywords"]},
    {"query": "generated indexes runtime artifacts", "expected_keywords": ["runtime artifacts"]},
]


def _parse_sizes(value: str) -> list[int]:
    sizes = []
    for item in value.split(","):
        try:
            sizes.append(max(64, min(int(item.strip()), 4096)))
        except ValueError:
            continue
    return sizes or [256, 512, 1024]


def _matches(hits: list[dict[str, Any]], keywords: list[str], k: int) -> bool:
    text = "\n".join(str(hit.get("text") or "").lower() for hit in hits[:k])
    return any(keyword.lower() in text for keyword in keywords)


def _run_size(documents: list[dict[str, Any]], chunk_size: int, runtime_dir: Path) -> dict[str, Any]:
    overlap = min(80, chunk_size // 5)
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=overlap)
    dense = JsonVectorBackend(runtime_dir / f"dense_{chunk_size}.json")
    embedding = DeterministicEmbeddingBackend()
    vectors = embedding.embed_texts([chunk["text"] for chunk in chunks])
    dense.build_index(chunks, vectors.vectors)
    bm25 = BM25RetrievalBackend(runtime_dir / f"bm25_{chunk_size}.json")
    bm25.build(chunks)
    bm25.save()

    recall3: list[bool] = []
    recall5: list[bool] = []
    latencies: list[float] = []
    for case in CASES:
        started = time.perf_counter()
        query_vector = embedding.embed_query(case["query"]).vectors[0]
        dense_hits = [hit.to_dict() for hit in dense.search(query_vector, top_k=10).hits]
        bm25_hits = bm25.search(case["query"], top_k=10).hits
        hits = reciprocal_rank_fusion([dense_hits, bm25_hits], settings.rag_rrf_k, top_k=5)
        latencies.append((time.perf_counter() - started) * 1000)
        recall3.append(_matches(hits, case["expected_keywords"], 3))
        recall5.append(_matches(hits, case["expected_keywords"], 5))
    return {
        "chunk_size": chunk_size,
        "retrieval_mode": "hybrid",
        "recall_at_3": round(sum(recall3) / len(recall3), 4),
        "recall_at_5": round(sum(recall5) / len(recall5), 4),
        "avg_latency_ms": round(mean(latencies), 3),
        "total_cases": len(CASES),
        "chunk_count": len(chunks),
    }


def run_experiment(
    output_path: str | Path | None = None,
    markdown_path: str | Path = "docs/rag_chunk_experiment.md",
    settings_obj: Settings | None = None,
) -> dict[str, Any]:
    active = settings_obj or settings
    documents = load_documents(ROOT / "workspace" / "docs")
    sizes = _parse_sizes(active.rag_chunk_experiment_sizes)
    runtime_dir = ROOT / "workspace" / "tmp" / "rag_chunk_experiment"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    results = [_run_size(documents, size, runtime_dir) for size in sizes]
    payload = {
        "experiment": "rag_chunk_size",
        "dataset": "workspace/docs demo documents",
        "cases": len(CASES),
        "results": results,
        "recommended_chunk_size": 512,
        "notes": [
            "Lightweight deterministic dense embeddings plus BM25 RRF were used.",
            "Real SentenceTransformers/Chroma runs remain optional via RUN_REAL_RAG_CHUNK_EXPERIMENT.",
        ],
    }
    output = Path(output_path or active.rag_chunk_experiment_output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = Path(markdown_path)
    if not markdown.is_absolute():
        markdown = ROOT / markdown
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(_markdown(payload), encoding="utf-8")
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# RAG Chunk Size Experiment",
        "",
        "## Purpose",
        "",
        "Compare 256, 512, and 1024 character chunks under an offline-safe hybrid retrieval path.",
        "",
        "## Dataset And Method",
        "",
        "The experiment uses the repository demo documents and eight fixed query/reference cases. Dense candidates use deterministic embeddings, sparse candidates use BM25, and RRF fuses both lists.",
        "",
        "Recall@3/Recall@5 measure whether an expected evidence phrase appears in the first 3/5 chunks. Avg Latency is mean in-process query latency.",
        "",
        "## Results",
        "",
        "| Chunk Size | Mode | Recall@3 | Recall@5 | Avg Latency (ms) | Chunks |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for result in payload["results"]:
        lines.append(
            f"| {result['chunk_size']} | {result['retrieval_mode']} | {result['recall_at_3']:.4f} | {result['recall_at_5']:.4f} | {result['avg_latency_ms']:.3f} | {result['chunk_count']} |"
        )
    lines += [
        "",
        "## Recommendation",
        "",
        "Use 512 as the conservative default: it balances evidence granularity and context continuity. Re-run with the real embedding backend before treating this lightweight result as a production benchmark.",
        "",
        "## Current Limitations",
        "",
        "* Small repository demo corpus; this is a reproducible engineering experiment, not a large benchmark.",
        "* Deterministic dense embeddings are used by default so CI and smoke runs do not require a model.",
        "* No reranker or production vector database cluster is included.",
        "* Raw JSON output is written to ignored `workspace/eval_outputs`.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(json.dumps(run_experiment(), ensure_ascii=False, indent=2))
