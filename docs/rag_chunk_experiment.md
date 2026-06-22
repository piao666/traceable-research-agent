# RAG Chunk Size Experiment

## Purpose

This experiment compares the retrieval quality and latency of 256, 512, and
1024 character chunks. The original experiment used one small document, so all
three sizes reached Recall@3 and Recall@5 of 1.0 and provided little statistical
separation. Day36 expanded both the corpus and the query/reference set, and
connected the documented real-embedding switch to the existing
SentenceTransformers backend.

## Corpus

The corpus contains 9 Markdown documents from `workspace/docs`. It covers Agent
architecture, planned and ReAct execution, RAG, SQL safety, GitHub/MCP
read-only behavior, Streamlit/HITL, evaluation, and a long mixed-topic document.

The experiment evaluates 20 queries. Chunk counts are 135 for size 256, 67 for
size 512, and 34 for size 1024.

## Query Cases

The 20 query/reference cases cover Agent architecture, planned and ReAct
execution, Thought/Action/Observation, Trace persistence, Tool Registry, SQL
safety, Dense/BM25/RRF retrieval, GitHub fallback, MCP read-only policy,
Streamlit, HITL, evaluation metrics, Docker lightweight mode, and limitation
handling. A hit is determined from the expected source or expected keywords in
the retrieved chunks.

## Results - Real SentenceTransformers Embedding

The real run used the local `bge-small-zh-v1.5` model on CPU with normalized
SentenceTransformers embeddings and Dense + BM25 + RRF hybrid retrieval.

| Chunk Size | Recall@3 | Recall@5 | Avg Latency (ms) | Total Chunks |
| ---------: | -------: | -------: | ---------------: | -----------: |
|        256 |   1.0000 |   1.0000 |           87.465 |          135 |
|        512 |   1.0000 |   1.0000 |           52.154 |           67 |
|       1024 |   1.0000 |   1.0000 |           35.199 |           34 |

## Results - Deterministic Embedding, for CI Reproducibility

The default run remains model-free and deterministic so CI, smoke, and eval do
not depend on a local model or external network.

| Chunk Size | Recall@3 | Recall@5 | Avg Latency (ms) | Total Chunks |
| ---------: | -------: | -------: | ---------------: | -----------: |
|        256 |   1.0000 |   1.0000 |            4.239 |          135 |
|        512 |   1.0000 |   1.0000 |            3.273 |           67 |
|       1024 |   1.0000 |   1.0000 |            2.583 |           34 |

## Findings

Recall remains saturated after expanding the corpus to 9 documents and the
evaluation to 20 cases. This means the current rule-based demo evaluation is
still easy enough that retrieval quality does not distinguish the three chunk
sizes; it does not prove that any size is universally superior. Larger chunks
produce fewer candidates and lower measured in-process latency in this run.

For the current demo, 512 remains the recommended default engineering
compromise: it halves the chunk count relative to 256 while preserving more
focused evidence than 1024. The recommendation considers latency, index size,
and context granularity rather than claiming a recall advantage.

## Limitations

* This remains a project-local demo corpus, not a public large-scale benchmark.
* The real experiment depends on a configured local SentenceTransformers model.
* Default CI and smoke use the deterministic baseline. The real run requires
  `RUN_REAL_RAG_CHUNK_EXPERIMENT=true` explicitly.
* Recall uses rule-based expected-keyword or expected-source matches and is not
  equivalent to human relevance assessment.
* Latency is a local CPU measurement and varies with hardware and process state.
* Runtime JSON is written to ignored `workspace/eval_outputs` and is not
  committed.
