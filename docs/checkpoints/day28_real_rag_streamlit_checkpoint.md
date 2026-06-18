# Day28 Real RAG + Streamlit CheckPoint

## Scope

- LLM Planner display check.
- Streamlit UI check.
- Real RAG SentenceTransformers + ChromaDB check.
- Reporter consistency fix.
- Planner notes consistency fix.
- Deterministic/JSON fallback check.

## Issues Found From Manual Screenshots

- Reporter Runtime Limitations was stale and described deterministic planning
  even when `planner_source=llm`.
- LLM planning notes could claim confirmation for steps whose normalized
  `requires_confirmation` value was false.
- Real RAG backend metadata was not sufficiently visible in the trace viewer.
- The health phase was still `day19`.

## Fixes Applied

- Runtime Limitations is generated from `planner_source` for LLM,
  deterministic fallback, and deterministic runs.
- Confirmation notes are rebuilt from normalized confirmation-required steps.
- Explicit human-approval tasks append an allowed `report_writer` step when an
  LLM omits it, so the HITL demo remains deterministic.
- Executor observations and reports preserve safe tool metadata.
- The trace API exposes parsed existing `output_json` and extracted metadata;
  no database migration was required.
- Streamlit provides a full-JSON expander for each trace and a dedicated RAG
  metadata table.
- The health phase is `day28-real-rag-streamlit`.

## Default Lightweight Validation

- Dependency installation was already satisfied under Python 3.10.11.
- Compile, demo DB initialization, deterministic index build, all smoke
  scripts, and eval passed.
- Default index build: 1 document, 3 chunks, deterministic embeddings, JSON
  vector backend, `fallback_used=false`.
- Default RAG query returned 3 hits.
- Backend smoke confirmed optional real packages were not loaded in the
  lightweight path and unavailable/missing backends fail safely.
- Eval passed 14/14 with `failed=0`, task success rate 1.0, and trace complete
  rate 1.0.

## Real RAG Validation

- Model path `E:\Models\bge-small-zh-v1.5` exists and loaded locally on CPU.
- `sentence-transformers=5.6.0` and `chromadb=1.5.9`.
- Build result: 1 document, 3 chunks, dimension 512,
  SentenceTransformers/Chroma, `fallback_used=false`.
- `scripts/smoke_real_rag.py` returned 3 hits and `real_rag=ok`.

## API Validation

- Real RAG run ID: `4e0cebbe0da64f25aec4dbc776684165`.
- Planner: `planner_source=llm`, provider Qwen, model `qwen-plus`.
- Plan contained `rag_search`; run completed with one successful trace.
- Trace metadata exposed `sentence_transformers`, `chroma`, dimension 512,
  model path, persist directory, collection name, and `fallback_used=false`.
- Report existed, included the RAG metadata, and used the LLM-specific Runtime
  Limitations without `no LLM reasoning yet`.

## Streamlit Validation

- `http://127.0.0.1:8501` returned HTTP 200.
- Health displayed `day28-real-rag-streamlit`.
- LLM full-tools UI run ID: `bee42b54c9cd4c1bab69905565309d43`.
- The Plan viewer displayed LLM/Qwen/qwen-plus with five consistent steps;
  the run completed with four successful tool traces and a rendered report.
- The `rag_search` trace expander displayed full JSON and a dedicated table
  containing SentenceTransformers/Chroma, `fallback_used=false`, and dimension
  512.
- HITL UI run ID: `41413fed83534065a5ba3543f8577dca`.
- HITL completed `waiting_human -> confirm -> completed`; the report included
  the Human Confirmation section and authoritative step/tool note.

## Runtime Artifacts

The following remain ignored and are not part of the checkpoint commit:

- `.env`
- `workspace/chroma`
- `workspace/index`
- `workspace/reports`
- SQLite databases and eval outputs
- model files under `E:\Models`

Uvicorn and Streamlit were stopped after validation with no matching Python
process remaining.

## Current Limitations

- Docker Desktop Linux daemon was unavailable, so Docker regression and real
  RAG model-mount validation were not run.
- FAISS is not implemented.
- Streamlit remains a demo UI rather than a production frontend.
- Real RAG validation used a local CPU model.
- Trace metadata is derived from the existing `output_json`; a dedicated
  `metadata_json` database column is not implemented.

## Next Step

Proceed to engineering hardening: API key authentication, asynchronous runs,
Alembic migrations, parser-based SQL validation, GitHub retry/cache behavior,
and a read-only MCP client/server direction.
