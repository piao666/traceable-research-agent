"""Run deterministic or explicit real-embedding chunk-size experiments."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, settings
from app.rag.bm25_backend import BM25RetrievalBackend
from app.rag.chunker import chunk_documents
from app.rag.embedding_backends import (
    DeterministicEmbeddingBackend,
    EmbeddingBackend,
    create_embedding_backend,
)
from app.rag.hybrid_search import reciprocal_rank_fusion
from app.rag.loader import load_documents
from app.rag.vector_backends import JsonVectorBackend


CASES_PATH = ROOT / "app" / "eval" / "rag_chunk_experiment_cases.jsonl"


def _parse_sizes(value: str) -> list[int]:
    sizes = []
    for item in value.split(","):
        try:
            sizes.append(max(64, min(int(item.strip()), 4096)))
        except ValueError:
            continue
    return sizes or [256, 512, 1024]


def real_embedding_requested(value: str | None = None) -> bool:
    """Parse the explicit real-experiment switch without reading LLM config."""

    raw = os.getenv("RUN_REAL_RAG_CHUNK_EXPERIMENT", "false") if value is None else value
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_experiment_cases(path: str | Path = CASES_PATH) -> list[dict[str, Any]]:
    source = Path(path)
    cases = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [str(case.get("case_id") or "") for case in cases]
    if len(cases) < 15:
        raise ValueError("RAG chunk experiment requires at least 15 cases.")
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("RAG chunk experiment case_id values must be present and unique.")
    for case in cases:
        if not str(case.get("query") or "").strip():
            raise ValueError(f"Case {case.get('case_id')} is missing query.")
        if not list(case.get("expected_keywords") or []):
            raise ValueError(f"Case {case.get('case_id')} is missing expected_keywords.")
        if not (case.get("expected_source") or case.get("expected_doc")):
            raise ValueError(f"Case {case.get('case_id')} is missing expected_source.")
    return cases


def _build_embedding_backend(
    use_real: bool,
    settings_obj: Settings,
) -> EmbeddingBackend:
    """Create the requested experiment backend; never silently downgrade real."""

    if not use_real:
        return DeterministicEmbeddingBackend()

    real_settings = settings_obj.model_copy(
        update={
            "rag_embedding_backend": "sentence_transformers",
            "rag_real_backend_enabled": True,
        }
    )
    backend = create_embedding_backend(real_settings)
    description = backend.describe()
    if not backend.is_available() or backend.name != "sentence_transformers":
        reason = description.get("reason") or "requested backend was not created"
        model_path = real_settings.rag_model_path or "<not configured>"
        raise RuntimeError(
            "Real embedding backend unavailable. "
            "Check RAG_REAL_BACKEND_ENABLED, RAG_EMBEDDING_BACKEND, "
            f"RAG_MODEL_PATH ({model_path}), and sentence-transformers installation. "
            f"Reason: {reason}."
        )
    return backend


def _matches(hits: list[dict[str, Any]], case: dict[str, Any], k: int) -> bool:
    selected = hits[:k]
    expected_source = str(case.get("expected_source") or case.get("expected_doc") or "")
    if expected_source and any(str(hit.get("source") or "") == expected_source for hit in selected):
        return True
    text = "\n".join(str(hit.get("text") or "").lower() for hit in selected)
    return any(str(keyword).lower() in text for keyword in case["expected_keywords"])


def _run_size(
    documents: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    chunk_size: int,
    runtime_dir: Path,
    embedding_backend: EmbeddingBackend,
    rrf_k: int,
) -> dict[str, Any]:
    overlap = min(80, chunk_size // 5)
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=overlap)
    vectors = embedding_backend.embed_texts([chunk["text"] for chunk in chunks])
    if not vectors.success:
        raise RuntimeError(vectors.error_message or "Document embedding failed.")

    dense = JsonVectorBackend(runtime_dir / f"dense_{chunk_size}.json")
    dense_summary = dense.build_index(chunks, vectors.vectors)
    if not dense_summary.success:
        raise RuntimeError(dense_summary.error_message or "Dense index build failed.")

    bm25 = BM25RetrievalBackend(runtime_dir / f"bm25_{chunk_size}.json")
    bm25_summary = bm25.build(chunks)
    if not bm25_summary.get("success"):
        raise RuntimeError(bm25_summary.get("error_message") or "BM25 build failed.")
    saved = bm25.save()
    if not saved.get("success"):
        raise RuntimeError(saved.get("error_message") or "BM25 save failed.")

    recall3: list[bool] = []
    recall5: list[bool] = []
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter()
        query_embedding = embedding_backend.embed_query(case["query"])
        if not query_embedding.success or not query_embedding.vectors:
            raise RuntimeError(query_embedding.error_message or "Query embedding failed.")
        dense_result = dense.search(query_embedding.vectors[0], top_k=10)
        if not dense_result.success:
            raise RuntimeError(dense_result.error_message or "Dense search failed.")
        bm25_result = bm25.search(case["query"], top_k=10)
        if not bm25_result.success:
            raise RuntimeError(bm25_result.error_message or "BM25 search failed.")
        dense_hits = [hit.to_dict() for hit in dense_result.hits]
        hits = reciprocal_rank_fusion(
            [dense_hits, bm25_result.hits],
            rrf_k,
            top_k=5,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        recall3.append(_matches(hits, case, 3))
        recall5.append(_matches(hits, case, 5))

    return {
        "chunk_size": chunk_size,
        "chunk_overlap": overlap,
        "retrieval_mode": "hybrid",
        "embedding_backend": embedding_backend.name,
        "recall_at_3": round(sum(recall3) / len(recall3), 4),
        "recall_at_5": round(sum(recall5) / len(recall5), 4),
        "avg_latency_ms": round(mean(latencies), 3),
        "total_cases": len(cases),
        "total_documents": len(documents),
        "total_chunks": len(chunks),
        "chunk_count": len(chunks),
    }


def _recommended_size(results: list[dict[str, Any]]) -> int:
    ranked = sorted(
        results,
        key=lambda result: (
            -float(result["recall_at_5"]),
            -float(result["recall_at_3"]),
            float(result["avg_latency_ms"]),
            int(result["chunk_size"]),
        ),
    )
    return int(ranked[0]["chunk_size"])


def run_experiment(
    output_path: str | Path | None = None,
    markdown_path: str | Path = "docs/rag_chunk_experiment.md",
    settings_obj: Settings | None = None,
    use_real_embedding: bool = False,
) -> dict[str, Any]:
    active = settings_obj or settings
    documents = load_documents(ROOT / "workspace" / "docs")
    if not documents:
        raise RuntimeError("RAG chunk experiment corpus is empty.")
    cases = load_experiment_cases()
    sizes = _parse_sizes(active.rag_chunk_experiment_sizes)
    embedding_backend = _build_embedding_backend(use_real_embedding, active)
    runtime_dir = ROOT / "workspace" / "tmp" / "rag_chunk_experiment"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    results = [
        _run_size(
            documents,
            cases,
            size,
            runtime_dir,
            embedding_backend,
            active.rag_rrf_k,
        )
        for size in sizes
    ]
    backend_description = embedding_backend.describe()
    payload = {
        "experiment": "rag_chunk_size",
        "use_real_embedding": use_real_embedding,
        "embedding_backend": embedding_backend.name,
        "embedding_backend_name": backend_description.get("name") or embedding_backend.name,
        "model_path": backend_description.get("model_path"),
        "model_name": (
            Path(str(backend_description["model_path"])).name
            if backend_description.get("model_path")
            else None
        ),
        "retrieval_mode": "hybrid",
        "chunk_sizes": sizes,
        "total_documents": len(documents),
        "total_queries": len(cases),
        "corpus_files": [document["source"] for document in documents],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "recommended_chunk_size": _recommended_size(results),
        "notes": [
            "Deterministic embeddings remain the default CI-reproducible baseline."
            if not use_real_embedding
            else "SentenceTransformers embeddings were explicitly requested.",
            "Dense and BM25 candidates are fused with RRF for every query.",
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
    result_title = (
        "Results - Real SentenceTransformers Embedding"
        if payload["use_real_embedding"]
        else "Results - Deterministic Embedding, for CI reproducibility"
    )
    lines = [
        "# RAG Chunk Size Experiment",
        "",
        "## Purpose",
        "",
        "Compare 256, 512, and 1024 character chunks on an expanded multi-topic demo corpus using Dense + BM25 + RRF hybrid retrieval.",
        "",
        "## Dataset And Method",
        "",
        f"The corpus contains {payload['total_documents']} Markdown documents and the evaluation contains {payload['total_queries']} query/reference cases. Cases record expected keywords, topic, and expected source. Some facts are placed across sentences or paragraph boundaries to make chunk continuity observable.",
        "",
        "Recall@3/Recall@5 measure whether an expected source or evidence phrase appears in the first 3/5 fused chunks. Avg Latency is mean in-process query embedding and hybrid retrieval latency.",
        "",
        "`RUN_REAL_RAG_CHUNK_EXPERIMENT=true` is now connected to the SentenceTransformers backend. The default remains deterministic and does not require a model.",
        "",
        f"## {result_title}",
        "",
        "| Chunk Size | Recall@3 | Recall@5 | Avg Latency (ms) | Total Cases | Documents | Chunks | Embedding Backend |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in payload["results"]:
        lines.append(
            f"| {result['chunk_size']} | {result['recall_at_3']:.4f} | {result['recall_at_5']:.4f} | {result['avg_latency_ms']:.3f} | {result['total_cases']} | {result['total_documents']} | {result['total_chunks']} | {result['embedding_backend']} |"
        )
    if not payload["use_real_embedding"]:
        lines.extend(
            [
                "",
                "## Results - Real SentenceTransformers Embedding",
                "",
                "To be executed in Day36-B. No real-embedding result is claimed in Day36-A.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Results - Deterministic Embedding, for CI reproducibility",
                "",
                "Run the default command separately in Day36-B and preserve both measured tables in the final report.",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The previous one-document/eight-query experiment saturated Recall@3 and Recall@5 at 1.0 for every chunk size because the corpus was too small and the retrieval task was too easy. The expanded corpus improves topic diversity, document length, and boundary-sensitive evidence. Results remain honestly computed; saturation is still possible and must be explained rather than artificially prevented.",
            "",
            f"The deterministic baseline currently recommends chunk size {payload['recommended_chunk_size']} by sorting Recall@5, Recall@3, then measured latency. The final recommendation will be revisited after the explicit real-embedding run in Day36-B.",
            "",
            "## Current Limitations",
            "",
            "* This is still a small repository demo corpus, not a public large-scale benchmark.",
            "* Deterministic embeddings are the default so CI and smoke do not require a model.",
            "* Real SentenceTransformers results are intentionally deferred to Day36-B.",
            "* No reranker or production vector database cluster is included.",
            "* Raw JSON output is written to ignored `workspace/eval_outputs`.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    use_real = real_embedding_requested()
    print(
        json.dumps(
            run_experiment(use_real_embedding=use_real),
            ensure_ascii=False,
            indent=2,
        )
    )
