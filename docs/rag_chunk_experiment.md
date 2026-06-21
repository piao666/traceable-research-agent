# RAG Chunk Size Experiment

## Purpose

Compare 256, 512, and 1024 character chunks on an expanded multi-topic demo corpus using Dense + BM25 + RRF hybrid retrieval.

## Dataset And Method

The corpus contains 9 Markdown documents and the evaluation contains 20 query/reference cases. Cases record expected keywords, topic, and expected source. Some facts are placed across sentences or paragraph boundaries to make chunk continuity observable.

Recall@3/Recall@5 measure whether an expected source or evidence phrase appears in the first 3/5 fused chunks. Avg Latency is mean in-process query embedding and hybrid retrieval latency.

`RUN_REAL_RAG_CHUNK_EXPERIMENT=true` is now connected to the SentenceTransformers backend. The default remains deterministic and does not require a model.

## Results - Deterministic Embedding, for CI reproducibility

| Chunk Size | Recall@3 | Recall@5 | Avg Latency (ms) | Total Cases | Documents | Chunks | Embedding Backend |
|---:|---:|---:|---:|---:|---:|---:|---|
| 256 | 1.0000 | 1.0000 | 4.245 | 20 | 9 | 135 | deterministic |
| 512 | 1.0000 | 1.0000 | 3.311 | 20 | 9 | 67 | deterministic |
| 1024 | 1.0000 | 1.0000 | 3.513 | 20 | 9 | 34 | deterministic |

## Results - Real SentenceTransformers Embedding

To be executed in Day36-B. No real-embedding result is claimed in Day36-A.

## Interpretation

The previous one-document/eight-query experiment saturated Recall@3 and Recall@5 at 1.0 for every chunk size because the corpus was too small and the retrieval task was too easy. The expanded corpus improves topic diversity, document length, and boundary-sensitive evidence. Results remain honestly computed; saturation is still possible and must be explained rather than artificially prevented.

The deterministic baseline currently recommends chunk size 512 by sorting Recall@5, Recall@3, then measured latency. The final recommendation will be revisited after the explicit real-embedding run in Day36-B.

## Current Limitations

* This is still a small repository demo corpus, not a public large-scale benchmark.
* Deterministic embeddings are the default so CI and smoke do not require a model.
* Real SentenceTransformers results are intentionally deferred to Day36-B.
* No reranker or production vector database cluster is included.
* Raw JSON output is written to ignored `workspace/eval_outputs`.
