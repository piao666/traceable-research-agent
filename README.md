# Traceable Research Agent

Traceable Research Agent is an independent FastAPI backend for a traceable,
task-oriented research agent. It demonstrates a manual Agent loop:

```text
create task -> inspect plan -> run manually -> inspect trace -> read report
```

The project is designed for resume and interview discussion around Agent
execution, Tool Registry design, trace persistence, local RAG, SQL/file safety,
minimal HITL, and read-only MCP/GitHub-style tool integration.

## Current Capabilities

- FastAPI service with task, plan, run, trace, tool, and report APIs.
- SQLite persistence for `agent_runs` and `tool_traces`.
- Deterministic Planner that maps task text to tool steps without LLM calls.
- Synchronous Executor through `POST /api/tasks/{run_id}/run` and optional
  FastAPI BackgroundTasks execution through `POST /api/tasks/{run_id}/run_async`.
- Optional demo API-key authentication and request-scoped tenant/user context.
- Markdown Reporter that writes ignored runtime reports under `workspace/reports`.
- Tool Registry with real handlers:
  - `file_reader`: reads only from `workspace/docs`, blocks path traversal.
  - `sql_query`: read-only SQLite queries, SELECT/WITH only.
  - `rag_search`: configurable RAG backend search, deterministic/JSON by default.
  - `mcp_github_search`: read-only GitHub/MCP-style search adapter, mock by default.
  - `report_writer`: handled by Reporter during plan execution.
- Minimal HITL using `waiting_human` and `POST /api/tasks/{run_id}/confirm`.
- Eval cases and runner covering success, safety, failure visibility, HITL, and repeated run guard.
- Dockerfile and Docker Compose for container startup.

`POST /api/tasks` intentionally creates a pending run and persisted plan only.
It does not execute tools automatically.

## Quick Start

```bash
python -m pip install -r requirements.txt
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected phase: `day29-api-key-auth-async-run`.

## Manual API Flow

Create a task:

```powershell
$body = @{
  task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
  report_type = "summary"
  source_mode = "mock"
  allowed_tools = @("file_reader", "sql_query", "rag_search", "report_writer")
} | ConvertTo-Json -Depth 10

$task = Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

Inspect and run:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)/plan"
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/tasks/$($task.run_id)/run" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{}'
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)/trace"
Invoke-RestMethod "http://127.0.0.1:8000/api/reports/$($task.run_id)"
```

## Optional API Key Auth

Authentication is disabled by default so local smoke and eval flows remain
credential-free. To enable demo authentication, configure only the local
`.env` file:

```dotenv
AUTH_ENABLED=true
DEMO_API_KEY=your-local-demo-key
```

Send the configured value using either `X-API-Key: your-local-demo-key` or
`Authorization: Bearer your-local-demo-key`. The `.env` file must not be
committed. This is demo-level API-key authentication, not a complete
production user or authorization system. `/health` remains public.

When enabled, authentication covers task create/status/plan/run/run_async/
trace/confirm, report retrieval, and tool catalog/detail/execute endpoints.
`/health` is intentionally exempt for readiness checks.

## Tenant Context

Core API requests accept `X-Tenant-ID` and `X-User-ID`. Values are trimmed,
restricted to 1-80 letters, numbers, underscores, hyphens, or dots, and fall
back to `demo` / `local-user` when absent or invalid. Day29 keeps this context
on `request.state` only and does not change the database schema. A future
Alembic revision can persist `tenant_id` and `user_id` on `agent_runs`.

## Async Run

The synchronous endpoint remains available:

```text
POST /api/tasks/{run_id}/run
```

The optional background endpoint returns immediately with status, trace, and
report URLs:

```text
POST /api/tasks/{run_id}/run_async
GET  /api/tasks/{run_id}
GET  /api/tasks/{run_id}/trace
GET  /api/reports/{run_id}
```

Clients poll the GET endpoints for completion. Day29 uses in-process FastAPI
`BackgroundTasks`; it is not a durable queue. A later phase can replace this
adapter with Celery, RQ, or Arq when multi-process delivery is required.
Repeated calls do not queue completed, running, or waiting_human runs again,
and waiting_human cannot bypass confirmation. The synchronous `/run` endpoint
remains available even when async execution is disabled.

## Alembic Migrations

Development and demo startup still use `init_db` / SQLAlchemy `create_all`, so
existing local workflows remain compatible. For an empty engineering-managed
database, apply the versioned schema with:

```powershell
alembic upgrade head
```

The initial migration is
`migrations/versions/0001_initial_trace_schema.py`; it creates `agent_runs`,
`tool_traces`, their foreign key, and the current trace run index. Alembic does
not automatically stamp or migrate an existing runtime database. SQLite
runtime databases and `workspace/tmp/` migration-smoke artifacts are ignored
and must not be committed.

## SQL Read-only Parser Validation

`sql_query` uses `sqlglot` to accept exactly one parsed `SELECT` or `WITH`
query. A conservative keyword guard remains as a second defense. The tool
rejects multiple statements, DDL, DML, `PRAGMA`, `ATTACH`, `DETACH`, and
`VACUUM` without raising an API 500. Rejections remain visible as rejected
tool traces for auditability.

```sql
-- allowed
SELECT id, title FROM documents LIMIT 5;

-- rejected
DELETE FROM documents;
SELECT 1; DROP TABLE documents;
```

Direct GitHub mock tool smoke:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tools/mcp_github_search/execute `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"arguments":{"query":"traceable research agent tool registry","repo":"piao666/traceable-research-agent","limit":3,"mode":"mock"}}'
```

## GitHub Search Cache / Retry / Fallback

`mcp_github_search` defaults to deterministic `mock` mode, which requires no
network or GitHub token. `public_api` mode uses only GitHub GET requests and
supports a local TTL cache, bounded retry with 0.5/1 second backoff, request
timeouts, rate-limit detection, and optional fallback to mock evidence.
`GITHUB_TOKEN` is optional and is never written to cache or metadata.

```dotenv
GITHUB_SEARCH_CACHE_ENABLED=true
GITHUB_SEARCH_CACHE_PATH=workspace/cache/github_search_cache.json
GITHUB_SEARCH_CACHE_TTL_SECONDS=3600
GITHUB_PUBLIC_API_TIMEOUT_SECONDS=10
GITHUB_PUBLIC_API_MAX_RETRIES=2
GITHUB_PUBLIC_API_FALLBACK_TO_MOCK=true
```

Result metadata identifies `data_source` as `mock`, `public_api`, `cache`, or
`fallback`, and records cache hits, retry count, rate limiting, and fallback
reason. Cache read/write errors are non-fatal. Runtime cache files under
`workspace/cache/` are ignored by Git. No GitHub write operation is exposed.

## MCP Read-only Direction

The current integration is a read-only compatible adapter, not a full MCP
server. Its fixed policy allows GET and denies POST, PUT, PATCH, and DELETE even
if write-oriented environment settings are mistakenly enabled. See
[MCP Read-only Direction](docs/mcp_readonly_direction.md) for the future client,
tool-discovery, allowlist, HITL, and trace-persistence direction.

## Streamlit Demo UI

The Streamlit UI is a lightweight demo layer. It only calls the FastAPI HTTP
API and does not read `.env`, display API keys, access SQLite directly, or call
internal Python functions.

Start the backend:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend:

```bash
streamlit run frontend/streamlit_app.py
```

Default UI URL:

```text
http://localhost:8501
```

The UI supports:

- Create task.
- Inspect persisted plan.
- Display `planner_source`, `llm_provider`, and `llm_model`.
- Run a task synchronously or through the async endpoint.
- Send an optional password-masked API key plus Tenant ID and User ID headers.
- View trace rows and status distribution.
- Inspect complete trace JSON and real RAG backend metadata when available.
- View and download generated Markdown reports.
- Handle HITL `waiting_human` confirmation and resume.
- Demo templates for normal file/sql/rag/report, GitHub mock report, HITL
  report, and LLM planner full-tools flow.

## Streamlit Auth Support

The sidebar provides password-masked API Key, Tenant ID, User ID, and
`Use async run` controls. The key is held only in Streamlit session state,
sent as `X-API-Key`, never rendered in response panels, and never written to a
file. With default `AUTH_ENABLED=false`, the API key field may remain empty.

## Docker

Build and run with Docker:

```bash
docker build -t traceable-research-agent .
docker run -p 8000:8000 -v ${PWD}/workspace:/app/workspace traceable-research-agent
```

Docker Compose:

```bash
docker compose up --build
docker compose down
```

`GITHUB_TOKEN` is optional. The default GitHub tool mode is `mock` and does not
need network or credentials.

If Docker Desktop Linux Engine is unstable, Docker is not counted as passed;
local smoke, eval, API, and Streamlit verification remain the accepted local
checks.

## Eval

Run local smoke and eval:

```bash
python -m compileall app tests scripts
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python scripts/smoke_react_executor.py
python scripts/smoke_github_mcp.py
python scripts/smoke_planner.py
python scripts/smoke_e2e.py
python scripts/smoke_exceptions.py
python scripts/smoke_hitl.py
python scripts/smoke_auth_async.py
python -m app.eval.run_eval
```

Eval cases live in `app/eval/cases.jsonl`. The runner prints a summary and may
write `workspace/eval_outputs/eval_report.json`, which is ignored by Git.

## Optional LLM Planner

The default stable path remains deterministic planning. An optional LLM Planner
can be enabled through local environment variables and currently supports Qwen
and DeepSeek through OpenAI-compatible chat APIs.

Planner modes:

- `deterministic`: use the rule-based planner only.
- `auto`: try the configured LLM only when enabled and available, otherwise
  fallback to deterministic.
- `llm`: try the configured LLM, and fallback to deterministic on any failure.

Provider defaults:

- Qwen: `qwen-plus` at `https://dashscope.aliyuncs.com/compatible-mode/v1`
- DeepSeek: `deepseek-chat` at `https://api.deepseek.com`

Example placeholders in `.env.example`:

```env
LLM_PLANNER_ENABLED=false
LLM_PROVIDER=qwen
LLM_PLANNER_MODE=auto
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEEPSEEK_API_KEY=
QWEN_API_KEY=
LLM_TIMEOUT_SECONDS=20
LLM_MAX_RETRIES=1
LLM_STRICT_JSON=true
```

Do not commit `.env` or real API keys. The service only reports
`deepseek_has_key` / `qwen_has_key` booleans in safe config smoke output.

LLM output must pass strict JSON plan validation before it can be persisted.
Invalid JSON, schema validation failure, unavailable providers, HTTP errors,
network timeouts, and missing API keys all fallback to deterministic planning.
Planning still does not write `tool_traces`, and `POST /api/tasks` still only
creates a pending run and persisted plan.

Reporter Runtime Limitations is generated from the persisted `planner_source`,
so LLM, deterministic fallback, and deterministic runs describe their actual
planning path.

## Optional ReAct Executor

The planned executor remains the default stable mode:

```text
create task -> inspect persisted plan -> run plan -> trace -> report
```

The optional ReAct executor makes one bounded decision at a time:

```text
task -> Thought (short rationale) -> Action -> Observation -> repeat -> finish
```

Each decision is strict JSON with `thought`, `action`, `args`, and
`finish_reason`. The LLM receives the prior observation history, including tool
failure, empty RAG evidence, SQL safety rejection, and GitHub fallback metadata,
before selecting the next action. Raw provider responses are not persisted.

ReAct remains inside the same safety boundaries as planned execution:

- actions must be in both `allowed_tools` and the enabled Tool Registry;
- tool handlers validate arguments and keep SQL read-only, file paths scoped,
  and GitHub operations GET-only;
- report steps that require human approval stop at `waiting_human`;
- `REACT_MAX_STEPS` and `REACT_SAME_TOOL_MAX_CALLS` prevent loops;
- invalid JSON, unknown tools, or provider unavailability produce structured
  trace events and can fall back to the persisted planned executor.

Configuration:

```env
EXECUTION_MODE=react
REACT_ENABLED=true
REACT_MAX_STEPS=8
REACT_SAME_TOOL_MAX_CALLS=3
REACT_LLM_PROVIDER=qwen
REACT_LLM_MODEL=qwen-plus
REACT_FALLBACK_TO_PLANNED=true
```

`POST /api/tasks` still creates only a pending run and plan. Both synchronous
`/run` and BackgroundTasks-based `/run_async` dispatch through the configured
execution mode. ReAct observations are stored in existing `plan_json` and trace
JSON fields, so Day32-A does not change the database schema or Alembic baseline.
The report and Streamlit trace viewer expose concise Thought / Action /
Observation summaries and whether a planned fallback was used.

## Hybrid RAG

`rag_search` supports three compatible retrieval modes:

- `dense`: the existing SentenceTransformers/Chroma path or deterministic/JSON fallback.
- `bm25`: lightweight sparse lexical retrieval through `rank-bm25`.
- `hybrid`: dense and BM25 candidate lists fused with reciprocal rank fusion (RRF).

Dense remains the default to preserve existing behavior:

```env
RAG_RETRIEVAL_MODE=hybrid
RAG_BM25_ENABLED=true
RAG_HYBRID_ENABLED=true
RAG_RRF_K=60
RAG_DENSE_CANDIDATE_MULTIPLIER=2
RAG_BM25_CANDIDATE_MULTIPLIER=2
```

The BM25 tokenizer lowercases Latin words/numbers and adds CJK unigrams and
bigrams without a separate segmentation service. Hybrid retrieval deduplicates
by source/chunk ID and exposes `dense_hit_count`, `bm25_hit_count`, `rrf_k`,
per-hit RRF ranks/scores, and `fallback_used`. If one hybrid side is missing,
the available side is returned with explicit fallback metadata rather than an
API 500. A directly requested unavailable BM25 index returns a structured
failure.

## Chunk Size Experiment

Day33 compares character chunk sizes 256, 512, and 1024 on eight fixed demo
queries. Metrics are Recall@3, Recall@5, and average in-process latency. The
reproducible lightweight result and limitations are documented in
[RAG Chunk Size Experiment](docs/rag_chunk_experiment.md). Run it with:

```powershell
python scripts/run_rag_chunk_experiment.py
```

Raw JSON is written to ignored
`workspace/eval_outputs/rag_chunk_experiment_results.json`; it is never
committed. The small demo corpus is an engineering regression experiment, not
a production benchmark. Chunk size 512 remains the conservative default.

## RAG Backend Configuration

The project keeps its original lightweight path as the default:

```env
RAG_EMBEDDING_BACKEND=deterministic
RAG_VECTOR_BACKEND=json
RAG_MODEL_PATH=E:\Models\bge-small-zh-v1.5
RAG_CHROMA_DIR=workspace/chroma
RAG_COLLECTION_NAME=traceable_research_docs
RAG_DEVICE=cpu
RAG_NORMALIZE_EMBEDDINGS=true
RAG_REAL_BACKEND_ENABLED=false
```

Day27 adds an optional SentenceTransformers embedding backend and persistent
ChromaDB vector backend. The available local model choices are
`bge-small-zh-v1.5`, `qwen3-embedding-0.6b`, and `bge-m3`; the first is the
recommended lightweight starting point.

If a requested backend is unavailable and `RAG_REAL_BACKEND_ENABLED=false`,
the service falls back to deterministic embeddings and the JSON index. When
real backends are explicitly enabled, an unavailable backend returns a stable
error instead. Existing `rag_search` output fields remain compatible, with
backend and fallback metadata added.

`workspace/index`, `workspace/chroma`, and `workspace/faiss` are runtime-only
and ignored by Git. Docker lightweight mode does not require a local model.

## Real RAG Optional Mode

Real RAG requires `sentence-transformers`, `chromadb`, and a local model. It
never downloads a model at runtime; the configured path is loaded with
`local_files_only=true`.

PowerShell example:

```powershell
$env:RAG_REAL_BACKEND_ENABLED="true"
$env:RAG_EMBEDDING_BACKEND="sentence_transformers"
$env:RAG_VECTOR_BACKEND="chroma"
$env:RAG_MODEL_PATH="E:\Models\bge-small-zh-v1.5"
$env:RAG_CHROMA_DIR="workspace/chroma"
$env:RAG_COLLECTION_NAME="traceable_research_docs"
$env:RAG_DEVICE="cpu"
$env:RAG_NORMALIZE_EMBEDDINGS="true"
python scripts/build_rag_index.py
python scripts/smoke_real_rag.py
```

Optional real-RAG eval uses the same RAG variables plus:

```powershell
$env:RUN_REAL_RAG_EVAL="true"
python -m app.eval.run_eval
```

Chroma cosine distance is exposed in hit metadata and converted to the public
score using `1 / (1 + distance)`. The public `rag_search` shape remains
`query`, `top_k`, and `hits`, with backend metadata added.

The Day28 checkpoint passed with local `bge-small-zh-v1.5` and ChromaDB while
the deterministic/JSON lightweight mode remained available. When FastAPI runs
in real RAG mode, Streamlit displays the resulting trace, complete JSON, and
backend metadata such as active backends, fallback state, and dimension.

Docker does not include model files. A future real-RAG container run can mount
`E:\Models:/models:ro` and set
`RAG_MODEL_PATH=/models/bge-small-zh-v1.5`; lightweight Docker mode remains the
default and does not require that mount.

## Architecture

Core flow:

```text
FastAPI -> Planner -> agent_runs.plan_json -> manual Executor
       -> Tool Registry -> tool_traces -> Reporter -> workspace/reports
```

More detail:

- [Architecture](docs/architecture.md)
- [API Examples](docs/api_examples.md)
- [Trace Examples](docs/trace_examples.md)
- [Bad Cases](docs/bad_cases.md)
- [Interview Notes](docs/interview_notes.md)
- [MCP Read-only Direction](docs/mcp_readonly_direction.md)

Checkpoint records:

- [Phase 2 Day6-9](docs/checkpoints/phase2_day6_9_checkpoint.md)
- [Phase 3 Day10-15](docs/checkpoints/phase3_day10_15_checkpoint.md)
- [Phase 4 Day16-19](docs/checkpoints/phase4_day16_19_checkpoint.md)
- [Day28 Real RAG + Streamlit](docs/checkpoints/day28_real_rag_streamlit_checkpoint.md)
- [Day29 Auth + Async](docs/checkpoints/day29_auth_async_checkpoint.md)

## Security Notes

- `file_reader` only reads under `workspace/docs` after path resolution.
- `sql_query` accepts one parser-validated SELECT/WITH statement and retains a
  destructive-keyword fallback guard.
- `rag_search` reads an ignored JSON or Chroma index and rejects empty query.
- `mcp_github_search` is read-only and uses mock mode by default.
- HITL is a minimal confirmation flow, not production authorization.
- Secrets, `.env`, runtime DBs, RAG indexes, reports, eval outputs, and logs are ignored.

## Current Limitations

- LLM planning depends on an optional external provider and always retains a
  deterministic fallback.
- API-key auth is demo-level; there is no production identity, authorization,
  or tenant isolation, and tenant/user context is not persisted.
- Async execution is an in-process BackgroundTask, not a durable job queue.
- The initial migration mirrors the current trace schema; tenant/user columns
  remain deferred to a future revision.
- GitHub cache is a local JSON cache, not a distributed cache; public API mode
  remains subject to GitHub rate limits before fallback.
- MCP is represented by a read-only compatible adapter, not a full MCP server.
- FAISS and a multi-model selector UI are not implemented.
