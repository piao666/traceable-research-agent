# Traceable Research Agent

> A traceable task-oriented research agent backend with planned/ReAct
> execution, LLM planning, tool execution, persistent traces, real/hybrid RAG,
> Streamlit UI, auth, async execution, SQL safety, and read-only
> GitHub/MCP-style tooling.

## Project Overview

Traceable Research Agent is not a simple RAG QA application. It models a
controllable research workflow in which planning, execution, safety decisions,
observations, and reports remain inspectable:

```text
create task -> inspect plan -> run planned/react -> inspect trace -> read report
```

`POST /api/tasks` intentionally creates only a pending run and persisted plan.
Execution starts only through `/run` or `/run_async`. Every tool success,
failure, rejection, fallback, and HITL transition can be reviewed through the
trace API and generated report. The design goal is **traceable, controllable,
and auditable Agent execution**.

## Key Features

* Planned executor and optional bounded ReAct executor.
* Concise Thought/Action/Observation decision traces.
* Qwen/DeepSeek LLM Planner with deterministic fallback.
* Tool Registry for file, SQL, RAG, GitHub, and reporting tools.
* Persistent SQLite run/tool traces and evidence-based Markdown reports.
* Real RAG with SentenceTransformers + ChromaDB.
* Hybrid RAG with Dense + BM25 + reciprocal rank fusion (RRF).
* Streamlit demo UI with plan, trace, report, HITL, auth, and async controls.
* Optional API-key auth and request-scoped Tenant/User context.
* FastAPI `BackgroundTasks` async execution with repeated-run guards.
* SQLGlot single-query read-only validation and file path safety.
* Read-only GitHub adapter with mock/cache/retry/fallback and MCP policy.

See the [Project Feature Matrix](docs/project_feature_matrix.md) for status,
evidence, and documented limitations.

## Architecture

```text
FastAPI API
   -> Planner (deterministic or LLM + fallback)
   -> agent_runs.plan_json
   -> Planned Executor or ReAct Executor
   -> Tool Registry
      -> file_reader / sql_query / rag_search / mcp_github_search
   -> tool_traces + observations
   -> Reporter -> workspace/reports/*.md

Streamlit -> FastAPI HTTP API only
```

Main modules:

* **FastAPI API:** task, plan, run, trace, confirm, report, and tool endpoints.
* **Planner:** structured plan creation with LLM and deterministic paths.
* **Executors:** stable sequential planned flow and bounded ReAct loop.
* **Tool Registry:** enabled-tool allowlist, metadata, validation, dispatch.
* **Trace Store / Reporter:** persistent audit history and evidence reports.
* **RAG:** deterministic/JSON, SentenceTransformers/Chroma, BM25, and RRF.
* **Security:** optional API key, request context, path and SQL boundaries.
* **Alembic / GitHub adapter:** schema baseline and GET-only external evidence.

Detailed design: [Architecture](docs/architecture.md).

## Execution Modes

### Planned

The default stable mode creates the complete plan first and executes it in
order. It is predictable, easy to audit, and efficient for short stable tasks.

```text
task -> plan -> tool 1 -> tool 2 -> report
```

### ReAct

The optional mode chooses one bounded action at a time from the latest
observation history:

```text
task -> Thought -> Action -> Observation -> repeat -> finish
```

Actions must be allowed and registered. `REACT_MAX_STEPS`,
`REACT_SAME_TOOL_MAX_CALLS`, strict decision JSON, HITL, and deterministic tool
safety remain enforced. Provider failure or invalid output can fall back to the
persisted planned path.

Day34 measured 18 reproducible cases:

| Mode | Completion | Recovery | Trace Quality | Avg Steps | Avg Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Planned | 100% | 1/7 | 3.889 | 1.278 | 826.514 ms |
| ReAct | 100% | 6/7 | 4.278 | 2.611 | 1299.416 ms |

ReAct is stronger for complex/failure scenarios; planned remains faster and
more stable for known short paths. See
[ReAct vs Planned Quantitative Evaluation](docs/eval_react_vs_planned.md).

## Quick Start: Lightweight Mode

The default mode is offline-friendly: deterministic planning, deterministic
embeddings, JSON vector storage, GitHub mock, and auth disabled.

```powershell
cd E:\BOSS\traceable-research-agent
python -m pip install -r requirements.txt
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

API flow:

```text
POST /api/tasks
GET  /api/tasks/{run_id}/plan
POST /api/tasks/{run_id}/run
GET  /api/tasks/{run_id}/trace
GET  /api/reports/{run_id}
```

Examples: [API Examples](docs/api_examples.md) and
[Trace Examples](docs/trace_examples.md).

## Streamlit Demo

With the backend running:

```powershell
python -m streamlit run frontend/streamlit_app.py --server.port 8501
```

Open `http://127.0.0.1:8501`, create a task, inspect its plan, run it, and view
trace/report output. The UI can display:

* planner source, provider, model, and allowed steps;
* planned/ReAct execution metadata;
* ReAct Thought/Action/Observation expanders;
* success/failed/rejected trace rows and full JSON;
* dense/BM25/hybrid RAG metadata and fallback state;
* HITL confirmation, optional API-key headers, Tenant/User context, and async
  polling.

The API key input is password-masked and is not persisted. Use the
[Demo Script](docs/demo_script.md) for 5-minute and 10-minute walkthroughs.

## Real and Hybrid RAG

The default lightweight path uses deterministic embeddings and a JSON index.
Optional real RAG uses a local SentenceTransformers model such as
`bge-small-zh-v1.5` with persistent ChromaDB. Models are never committed or
downloaded automatically.

Hybrid retrieval combines:

* **Dense:** semantic retrieval through the active vector backend.
* **BM25:** lexical retrieval with lightweight English/CJK tokens.
* **RRF:** rank-based fusion that avoids comparing incompatible raw scores.

```dotenv
RAG_RETRIEVAL_MODE=hybrid
RAG_BM25_ENABLED=true
RAG_HYBRID_ENABLED=true
RAG_RRF_K=60
```

The Day33 small-corpus chunk experiment produced:

| Chunk Size | Recall@3 | Recall@5 | Avg Latency |
| ---: | ---: | ---: | ---: |
| 256 | 1.0 | 1.0 | 2.098 ms |
| 512 | 1.0 | 1.0 | 1.588 ms |
| 1024 | 1.0 | 1.0 | 1.814 ms |

Chunk size 512 remains the conservative default. These saturated small-corpus
results are engineering regression evidence, not a public benchmark. Details:
[RAG Chunk Size Experiment](docs/rag_chunk_experiment.md).

Optional local real-RAG smoke:

```powershell
$env:RAG_REAL_BACKEND_ENABLED="true"
$env:RAG_EMBEDDING_BACKEND="sentence_transformers"
$env:RAG_VECTOR_BACKEND="chroma"
$env:RAG_MODEL_PATH="E:\Models\bge-small-zh-v1.5"
$env:RAG_RETRIEVAL_MODE="hybrid"
python scripts/build_rag_index.py
python scripts/smoke_real_rag.py
python scripts/smoke_hybrid_rag.py
```

## Auth, Async, SQL Safety, and Alembic

Authentication is disabled by default. Enable demo-level API-key auth only in
the local `.env` file:

```dotenv
AUTH_ENABLED=true
DEMO_API_KEY=your-local-demo-key
```

Protected APIs accept `X-API-Key` or `Authorization: Bearer`; `/health` stays
public. `X-Tenant-ID` and `X-User-ID` are sanitized request context only and are
not persisted.

`POST /api/tasks/{run_id}/run_async` uses in-process FastAPI
`BackgroundTasks`; clients poll status, trace, and report endpoints. Repeated
running/completed/waiting-HITL runs are not queued again.

For an empty engineering-managed database:

```powershell
alembic upgrade head
```

Demo startup can still use `init_db`. SQL execution uses SQLGlot plus a
secondary keyword guard and only accepts one `SELECT` or `WITH` statement. It
rejects `DELETE`, `DROP`, `INSERT`, `UPDATE`, `PRAGMA`, `ATTACH`, `DETACH`,
`VACUUM`, DDL/DML, and multi-statement input without causing API 500.

## GitHub and MCP Read-only Direction

`mcp_github_search` defaults to offline mock mode. Optional `public_api` mode
uses GET only and supports TTL cache, bounded retry/timeout, rate-limit
classification, and fallback to mock evidence. Metadata identifies
`mock`, `public_api`, `cache`, or `fallback` as the data source.

No issue creation, PR comment, push, or repository mutation is exposed. This
is a read-only compatible adapter, not a full MCP server. See
[MCP Read-only Direction](docs/mcp_readonly_direction.md).

## Smoke and Eval

Run the final lightweight regression aggregator:

```powershell
python scripts/smoke_final_project.py
```

It runs 15 existing smoke scripts plus `python -m app.eval.run_eval` and stops
on the first failure. Core checks include:

* `smoke_react_executor.py`
* `smoke_hybrid_rag.py`
* `smoke_react_vs_planned_eval.py`
* `smoke_auth_async.py`
* `smoke_alembic_sql_parser.py`
* `smoke_github_mcp.py`
* `smoke_streamlit_frontend.py`
* `python -m app.eval.run_eval`

`smoke_real_rag.py` is intentionally separate because it requires a local
model. Runtime eval JSON, reports, indexes, databases, and caches are ignored.

## Documentation

* [Project Feature Matrix](docs/project_feature_matrix.md)
* [Demo Script](docs/demo_script.md)
* [Final Project Summary](docs/final_project_summary.md)
* [Interview Pitch](docs/interview_pitch.md)
* [Architecture](docs/architecture.md)
* [API Examples](docs/api_examples.md)
* [Bad Cases](docs/bad_cases.md)
* [Final Engineering Checkpoint](docs/checkpoints/day35_final_engineering_checkpoint.md)

## Current Limitations

* Docker Desktop Engine is currently unstable on the local Windows
  environment; Docker regression was attempted but not accepted as passed.
* Streamlit is a demo UI, not a production frontend.
* `BackgroundTasks` provides local async execution, not a distributed queue.
* Tenant/User context is request-level only and is not persisted in the DB.
* MCP support is a read-only adapter, not a full MCP server.
* Real RAG requires a separately managed local model path.
* Default ReAct quantitative eval uses fake/mock LLM decisions for
  reproducibility; real Qwen/DeepSeek eval is optional.
* Current Agent/RAG benchmarks are small engineering datasets, not large public
  benchmarks.

## Security and Runtime Artifacts

Never commit `.env`, API keys, GitHub tokens, local models, SQLite databases,
Chroma/index/cache data, generated reports, or eval outputs. The tracked
`workspace` content is intentionally limited to:

```text
workspace/data/.gitkeep
workspace/docs/demo_research_note.md
```

The project is in **feature freeze** after Day35. Future productionization
should be isolated from this stable demonstration baseline.
