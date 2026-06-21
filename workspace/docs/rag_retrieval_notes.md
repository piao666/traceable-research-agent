# RAG Retrieval Notes

## Retrieval Backends

The lightweight baseline uses deterministic token-frequency embeddings and a
JSON vector index. It is fast, offline, and reproducible, so default smoke and
CI do not need a model. It is not intended to match the semantic quality of a
trained dense encoder.

The real dense path uses SentenceTransformers with a local model and stores
vectors in ChromaDB. The validated model is `bge-small-zh-v1.5`, which produces
512-dimensional embeddings. Model loading is local-files-only; the application
does not download model files during a run.

BM25 provides sparse lexical retrieval. It is useful when queries contain
exact identifiers, API names, SQL keywords, configuration fields, or uncommon
terms that a dense model might smooth over. The lightweight tokenizer keeps
Latin words/numbers and adds CJK unigram and bigram tokens.

## Hybrid Retrieval and RRF

Hybrid mode obtains candidates from Dense retrieval and BM25 retrieval. Their
raw scores are not directly comparable, so Reciprocal Rank Fusion (RRF) uses
rank positions. Each result contributes `1 / (k + rank)`, and contributions
for the same source/chunk are summed. The public hit metadata retains dense
rank, BM25 rank, source scores, and the final RRF score.

RRF therefore rewards evidence ranked well by either retrieval method without
pretending cosine similarity and BM25 scores share a scale. The setting
`retrieval_mode=dense|bm25|hybrid` selects behavior. Hybrid can return the
available side with explicit fallback metadata if one index is unavailable.

## Chunk Size Trade-offs

Chunking uses character windows with overlap. A 256-character chunk is precise
and creates many candidates, but a fact split across sentences can lose context
continuity. A 1024-character chunk preserves more surrounding explanation but
can mix unrelated topics and reduce evidence granularity. A 512-character
chunk is a middle point, not an automatically correct answer.

The experiment compares 256, 512, and 1024. Some corpus facts intentionally
cross paragraph or window boundaries: the paragraph introducing dense and
BM25 may be separated from the paragraph explaining RRF; a safety rule may be
introduced before its trace consequence. Overlap reduces abrupt loss but does
not remove the precision/context trade-off.

## Experiment Modes

The default chunk experiment embeds the same expanded corpus with the
deterministic backend, builds a temporary JSON dense index, builds BM25, and
evaluates fused hits. This remains the reproducible baseline.

When `RUN_REAL_RAG_CHUNK_EXPERIMENT=true`, the experiment explicitly requests
the SentenceTransformers backend through the shared backend factory. It uses
the configured model path, device, and normalization settings. An unavailable
model is a clear error, not a reason to silently report deterministic results
as real embeddings.

Recall@3 and Recall@5 check expected evidence/source in fused results. Average
latency includes query embedding and retrieval, so real and deterministic
numbers must be interpreted separately. The corpus remains a demo corpus even
after expansion and is not a substitute for a public retrieval benchmark.
