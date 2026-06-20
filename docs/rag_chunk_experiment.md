# RAG Chunk Size Experiment

## Purpose

Compare 256, 512, and 1024 character chunks under an offline-safe hybrid retrieval path.

## Dataset And Method

The experiment uses the repository demo documents and eight fixed query/reference cases. Dense candidates use deterministic embeddings, sparse candidates use BM25, and RRF fuses both lists.

Recall@3/Recall@5 measure whether an expected evidence phrase appears in the first 3/5 chunks. Avg Latency is mean in-process query latency.

## Results

| Chunk Size | Mode | Recall@3 | Recall@5 | Avg Latency (ms) | Chunks |
|---:|---|---:|---:|---:|---:|
| 256 | hybrid | 1.0000 | 1.0000 | 2.098 | 6 |
| 512 | hybrid | 1.0000 | 1.0000 | 1.588 | 3 |
| 1024 | hybrid | 1.0000 | 1.0000 | 1.814 | 2 |

## Recommendation

Use 512 as the conservative default: it balances evidence granularity and context continuity. Re-run with the real embedding backend before treating this lightweight result as a production benchmark.

## Current Limitations

* Small repository demo corpus; this is a reproducible engineering experiment, not a large benchmark.
* Deterministic dense embeddings are used by default so CI and smoke runs do not require a model.
* No reranker or production vector database cluster is included.
* Raw JSON output is written to ignored `workspace/eval_outputs`.
