# Traceable Research Agent

Traceable Research Agent is a traceable task-oriented research agent backend
with planned/ReAct execution, LLM planning, tool execution, persistent traces,
real/hybrid RAG, Streamlit UI, auth, async execution, SQL safety, and read-only
GitHub/MCP-style tooling.

## 1. Project Overview

This project is not a conventional RAG question-answering demo. Its core goal
is to make Agent execution **traceable, controllable, and auditable**.

```text
create task -> inspect plan -> run planned/react -> inspect trace -> read report
```

`POST /api/tasks` creates a pending run and persisted plan without immediately
executing tools. The user can inspect the plan before starting synchronous or
asynchronous execution. The Agent can research through local files, read-only
SQL, local RAG, and a read-only GitHub/MCP-style adapter. Tool successes,
failures, safety rejections, fallbacks, HITL transitions, and ReAct observations
are persisted before an evidence-based Markdown report is generated.

## 2. Key Features

* Planned executor and optional bounded ReAct executor.
* Thought / Action / Observation trace for ReAct decisions.
* Qwen/DeepSeek LLM Planner with deterministic fallback.
* Tool Registry and controlled multi-tool execution.
* Persistent SQLite run and tool-trace database.
* Real RAG with SentenceTransformers and ChromaDB.
* Hybrid RAG with Dense retrieval, BM25, and RRF fusion.
* Streamlit demo UI for plan, trace, report, RAG metadata, and HITL.
* Optional API Key Auth, Tenant/User context, and `BackgroundTasks` async run.
* SQLGlot read-only SQL validation and file path safety.
* GitHub cache/retry/fallback with an MCP read-only policy.
* Lightweight Docker build and health-check workflow.

## 3. Architecture

```text
User / Streamlit / API Client
  -> FastAPI API Layer
  -> Planner / Planned Executor / ReAct Executor
  -> Tool Registry
  -> file_reader / sql_query / rag_search / mcp_github_search / report_writer
  -> Trace Store + SQLite
  -> Markdown Reporter + Streamlit Viewer
```

* **API Layer:** task lifecycle, plan, run, async run, confirmation, trace,
  report, and tool endpoints.
* **Agent Layer:** deterministic/LLM planning, sequential planned execution,
  and bounded observation-driven ReAct execution.
* **Tool Layer:** central registry, allowed-tool enforcement, handler dispatch,
  structured failures, and safety metadata.
* **RAG Layer:** deterministic/JSON fallback, SentenceTransformers/Chroma,
  BM25 sparse retrieval, and Dense+BM25 RRF fusion.
* **Trace/DB Layer:** SQLAlchemy models, SQLite persistence, Alembic baseline,
  tool trace logging, and report paths.
* **UI/Eval Layer:** Streamlit visualization, smoke scripts, retrieval
  experiments, and planned/ReAct quantitative evaluation.

Technology stack: Python, FastAPI, Pydantic, SQLAlchemy, SQLite, Alembic,
SQLGlot, SentenceTransformers, ChromaDB, rank-bm25, Streamlit, Uvicorn, and
Docker Compose.

See [Architecture](docs/architecture.md) for the detailed module flow.

## 4. Execution Modes

### Planned Executor

The Agent first creates a complete plan and then executes its tool steps in
order. This mode is the stable default and is best for predictable short-path
tasks where lower latency and straightforward auditing matter most.

### ReAct Executor

```text
Thought -> Action -> Observation -> repeat -> finish
```

Each step uses the task, allowed tools, registered tools, and
`observation_history` to choose the next safe action. It is designed for
complex tasks, tool failures, empty retrieval results, and safety rejections.
The loop is bounded by max-step and repeated-tool protections.

| Mode | Completion | Recovery | Trace Quality | Avg Latency | Best for |
| --- | ---: | ---: | ---: | ---: | --- |
| Planned | 100% | 1/7 | 3.889 | 826.514 ms | Stable short-path tasks |
| ReAct | 100% | 6/7 | 4.278 | 1299.416 ms | Failure recovery and traceability |

ReAct does not fully replace planned execution. Planned is faster and more
predictable; ReAct provides stronger recovery and decision explainability.
The complete 18-case experiment is documented in
[ReAct vs Planned Evaluation](docs/eval_react_vs_planned.md).

## 5. RAG Capabilities

### Lightweight Fallback

* Deterministic embedding backend and JSON vector index.
* Offline-friendly path for fast local demos and lightweight Docker mode.
* Stable fallback when optional real backends are not enabled.

### Real RAG

* SentenceTransformers embeddings.
* Local `bge-small-zh-v1.5` model.
* Persistent ChromaDB vector storage.
* 512-dimensional embeddings in the validated local configuration.

### Hybrid RAG

* Dense semantic retrieval.
* BM25 sparse lexical retrieval.
* Reciprocal Rank Fusion (RRF).
* `retrieval_mode=dense|bm25|hybrid`.

Day36 expanded the experiment to 9 documents and 20 query/reference cases.
The deterministic baseline remains the default reproducible path; set
`RUN_REAL_RAG_CHUNK_EXPERIMENT=true` with a configured local model to run the
real SentenceTransformers branch.

| Backend | Chunk Size | Recall@3 | Recall@5 | Avg Latency |
| --- | ---: | ---: | ---: | ---: |
| SentenceTransformers | 256 | 1.0 | 1.0 | 87.465 ms |
| SentenceTransformers | 512 | 1.0 | 1.0 | 52.154 ms |
| SentenceTransformers | 1024 | 1.0 | 1.0 | 35.199 ms |
| Deterministic | 256 | 1.0 | 1.0 | 4.239 ms |
| Deterministic | 512 | 1.0 | 1.0 | 3.273 ms |
| Deterministic | 1024 | 1.0 | 1.0 | 2.583 ms |

Recall remains saturated on the current demo corpus, so the results do not
establish a universal winner. Chunk size 512 is the recommended default
engineering compromise between latency, chunk count, and evidence granularity.
See
[RAG Chunk Experiment](docs/rag_chunk_experiment.md).

## 6. Tool System and Safety Boundaries

Available tools:

* `file_reader`: reads whitelisted files under the local docs workspace.
* `sql_query`: executes bounded read-only SQLite queries.
* `rag_search`: searches deterministic, real, BM25, or hybrid indexes.
* `mcp_github_search`: collects mock/public GitHub evidence through GET only.
* `report_writer`: generates a Markdown report from observations and traces.

Safety controls:

* Per-run `allowed_tools` enforcement.
* Tool Registry enabled/registered checks.
* File path whitelist and traversal rejection.
* SQLGlot AST validation for one `SELECT` or `WITH` statement only.
* GitHub GET-only read-only policy; no issue, PR comment, push, or mutation.
* HITL confirmation for confirmation-required report steps.
* `REACT_MAX_STEPS` and `REACT_SAME_TOOL_MAX_CALLS` loop guards.
* Structured fallback or limitation on invalid LLM JSON and unknown actions.

## 7. API Overview

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Public service health check. |
| POST | `/api/tasks` | Create a pending run and persisted plan. |
| GET | `/api/tasks/{run_id}` | Read run status and progress. |
| GET | `/api/tasks/{run_id}/plan` | Inspect the persisted plan. |
| POST | `/api/tasks/{run_id}/run` | Execute synchronously. |
| POST | `/api/tasks/{run_id}/run_async` | Start BackgroundTasks execution. |
| POST | `/api/tasks/{run_id}/confirm` | Approve or reject a HITL step. |
| GET | `/api/tasks/{run_id}/trace` | Read complete tool/decision traces. |
| GET | `/api/reports/{run_id}` | Read the generated Markdown report. |
| GET | `/api/tools` | List registered tools. |
| POST | `/api/tools/{tool_name}/execute` | Execute a tool through the API boundary. |

See [API Examples](docs/api_examples.md) and
[Trace Examples](docs/trace_examples.md).

## 8. Quick Start

```powershell
cd E:\BOSS\traceable-research-agent
python -m pip install -r requirements.txt
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health |
  ConvertTo-Json -Depth 10
```

The default lightweight configuration does not require an external LLM,
GitHub token, or local embedding model.

## 9. Streamlit Demo

```powershell
streamlit run frontend/streamlit_app.py --server.port 8501
```

Open `http://127.0.0.1:8501`. The UI displays:

* backend health;
* task creation and persisted plan;
* planner source/provider/model and execution mode;
* tool trace table and full trace JSON;
* ReAct Thought / Action / Observation entries;
* Dense/BM25/Hybrid RAG metadata;
* generated Markdown report;
* HITL confirmation controls;
* optional API key, Tenant/User headers, and async execution.

Use [Demo Script](docs/demo_script.md) for the 5-minute and 10-minute demo.

## 10. Docker Lightweight Mode

Docker intentionally installs `requirements-docker-light.txt` instead of the
full local-model dependency set. Its goal is a reliable lightweight
build/up/health demonstration. SentenceTransformers/Chroma Real/Hybrid RAG with
the local model is validated in the local Python environment and is not bundled
into the Docker image by default.

```powershell
docker compose build
docker compose up -d
Invoke-RestMethod http://127.0.0.1:8000/health |
  ConvertTo-Json -Depth 10
docker compose down
```

Latest lightweight Docker validation:

* `docker compose build` passed.
* `docker compose up -d` passed.
* `/health` returned `status=ok`.
* `docker compose down` passed.

## 11. Evaluation and Smoke Tests

Final validation status:

* `smoke_final_project.py`: 16/16 checks passed.
* Application eval: 27/27 passed, failed=0.
* Trace completeness: 1.0.
* Optional Real/Hybrid RAG validation passed.
* Lightweight Docker build/up/health/down validation passed.

Core commands:

```powershell
python -m compileall app tests scripts frontend
python scripts/smoke_final_project.py
python -m app.eval.run_eval
```

`smoke_real_rag.py` remains separate because it requires the local embedding
model. Runtime eval JSON, reports, caches, indexes, Chroma data, and SQLite
databases are ignored by Git.

## 12. Project Documents

* [Architecture](docs/architecture.md)
* [API Examples](docs/api_examples.md)
* [Trace Examples](docs/trace_examples.md)
* [Bad Cases](docs/bad_cases.md)
* [Interview Notes](docs/interview_notes.md)
* [MCP Read-only Direction](docs/mcp_readonly_direction.md)
* [RAG Chunk Experiment](docs/rag_chunk_experiment.md)
* [ReAct vs Planned Evaluation](docs/eval_react_vs_planned.md)
* [Project Feature Matrix](docs/project_feature_matrix.md)
* [Demo Script](docs/demo_script.md)
* [Final Project Summary](docs/final_project_summary.md)
* [Interview Pitch](docs/interview_pitch.md)
* [Day35 Final Engineering CheckPoint](docs/checkpoints/day35_final_engineering_checkpoint.md)

## 13. Directory Structure

```text
traceable-research-agent/
|-- app/
|   |-- api/
|   |-- agent/
|   |-- eval/
|   |-- llm/
|   |-- mcp/
|   |-- rag/
|   |-- security/
|   |-- tools/
|   `-- trace/
|-- docs/
|-- frontend/
|-- migrations/
|-- scripts/
|-- workspace/
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
|-- requirements-docker-light.txt
`-- README.md
```

Tracked workspace content is intentionally limited to:

```text
workspace/data/.gitkeep
workspace/docs/demo_research_note.md
```

## 14. Current Limitations

* Streamlit is a demo UI, not a production frontend.
* `BackgroundTasks` provides local async execution, not a distributed queue.
* Tenant/User context is request-level only, not production multi-tenancy.
* MCP support is a read-only adapter, not a full MCP server.
* GitHub write tools are intentionally not implemented.
* Default ReAct evaluation uses fake/mock LLM decisions for reproducibility.
* Real LLM eval requires a locally configured Qwen or DeepSeek API key.
* Current Agent/RAG benchmarks are small-scale engineering datasets.
* The Docker image uses lightweight dependencies and does not bundle the Real
  RAG model.

## 15. Future Work

The project is in feature freeze. Possible future productionization work:

* production frontend;
* distributed durable task queue;
* persisted tenant isolation and authorization;
* full MCP server and controlled write-tool elevation;
* GitHub OAuth App integration;
* retrieval reranker;
* larger public and human-reviewed benchmark;
* deployment, monitoring, and operational observability.

## 16. Resume Summary

Traceable Research Agent is a traceable research-agent backend that supports
planned/ReAct execution, multi-tool orchestration, persistent traces,
real/hybrid RAG, SQL safety, GitHub read-only search, Streamlit visualization,
auth, async execution, and quantitative evaluation.

Never commit `.env`, API keys, GitHub tokens, local models, SQLite databases,
generated reports, caches, indexes, Chroma data, or eval outputs.
