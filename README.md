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
- Manual Executor through `POST /api/tasks/{run_id}/run`.
- Markdown Reporter that writes ignored runtime reports under `workspace/reports`.
- Tool Registry with real handlers:
  - `file_reader`: reads only from `workspace/docs`, blocks path traversal.
  - `sql_query`: read-only SQLite queries, SELECT/WITH only.
  - `rag_search`: local JSON vector index search.
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

Expected phase: `day19`.

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

Direct GitHub mock tool smoke:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tools/mcp_github_search/execute `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"arguments":{"query":"traceable research agent tool registry","repo":"piao666/traceable-research-agent","limit":3,"mode":"mock"}}'
```

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
- Manually run a task.
- View trace rows and status distribution.
- View and download generated Markdown reports.
- Handle HITL `waiting_human` confirmation and resume.
- Demo templates for normal file/sql/rag/report, GitHub mock report, HITL
  report, and LLM planner full-tools flow.

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

## Eval

Run local smoke and eval:

```bash
python -m compileall app tests scripts
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python scripts/smoke_planner.py
python scripts/smoke_e2e.py
python scripts/smoke_exceptions.py
python scripts/smoke_hitl.py
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

Checkpoint records:

- [Phase 2 Day6-9](docs/checkpoints/phase2_day6_9_checkpoint.md)
- [Phase 3 Day10-15](docs/checkpoints/phase3_day10_15_checkpoint.md)
- [Phase 4 Day16-19](docs/checkpoints/phase4_day16_19_checkpoint.md)

## Security Notes

- `file_reader` only reads under `workspace/docs` after path resolution.
- `sql_query` accepts only SELECT/WITH and rejects destructive SQL keywords.
- `rag_search` reads a local ignored JSON index and rejects empty query.
- `mcp_github_search` is read-only and uses mock mode by default.
- HITL is a minimal confirmation flow, not production authorization.
- Secrets, `.env`, runtime DBs, RAG indexes, reports, eval outputs, and logs are ignored.

## Current Limitations

- No real LLM planner.
- No production auth or tenant isolation.
- No background job queue.
- No persistent migration framework.
- GitHub public API mode is best-effort and may be rate-limited.
- MCP is represented by a read-only compatible adapter, not a full MCP server.
