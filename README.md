# Traceable Research Agent

Traceable Research Agent is an independent FastAPI project for building a
traceable task-oriented research agent. The Day9 version exposes health, task,
tool catalog, tool execution, trace, and report endpoints, with task runs and
tool traces persisted to local SQLite.

## Local Start

```bash
python -m pip install -r requirements.txt
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## API Examples

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/tools
Invoke-RestMethod http://127.0.0.1:8000/api/tools/file_reader
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tools/file_reader/execute `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"arguments":{"path":"demo_research_note.md","max_chars":1000}}'
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"task":"Read local docs and generate a traceable report","report_type":"summary","source_mode":"mock","allowed_tools":["file_reader","report_writer"]}'
```

## Phase 2 Day6-9 Capabilities

- `file_reader` is a real read-only handler. It only reads files under
  `workspace/docs`, resolves paths before reading, rejects path traversal and
  external absolute paths, enforces `max_chars` with a hard cap of 20000, and
  supports `.txt`, `.md`, `.csv`, `.json`, `.py`, and `.log`.
- `.docx`, `.pdf`, and `.xlsx` return a clear `format not implemented in Day6`
  error instead of pulling in heavier parsing dependencies.
- `sql_query` is a real read-only handler against `workspace/demo.sqlite`. It
  allows only `SELECT` or `WITH`, rejects destructive keywords, appends a limit
  when needed, and caps returned rows at 100.
- `rag_search` is a real read-only handler against
  `workspace/index/rag_index.json`. It accepts `query` and `top_k`, caps
  `top_k` at 10, returns top-k chunks with `source`, `chunk_id`, `score`, and
  `text`, handles missing indexes with `metadata.error_type=index_missing`, and
  rejects empty queries with `metadata.error_type=invalid_args`.
- `POST /api/tools/{tool_name}/execute` accepts optional `run_id` and `step_no`
  fields for Phase 2 smoke verification. When `run_id` exists, the API writes a
  `tool_traces` row for success, failure, or safety rejection.
- The RAG foundation can load `.md`/`.txt` files from `workspace/docs`, split
  them into overlapping character chunks, embed them with deterministic
  bag-of-words vectors, persist a JSON index under `workspace/index`, and query
  top-k chunks.

Runtime artifacts are intentionally ignored by Git: `workspace/demo.sqlite`,
`workspace/traceable_research_agent.sqlite`, and
`workspace/index/rag_index.json`.

Phase 2 checkpoint notes are available at
`docs/checkpoints/phase2_day6_9_checkpoint.md`.

## Current Scope

- Implemented: FastAPI skeleton, `/health`, database-backed `/api/tasks`,
  registry-backed `/api/tools`, and mock `/api/reports/{run_id}`.
- Implemented: SQLite database setup, `agent_runs` and `tool_traces` ORM
  tables, database-backed task creation/status, and reserved trace listing.
- Implemented: Tool Registry metadata with `ToolSpec`, `ToolResult`,
  `register_tool`, `get_tool`, `list_tools`, and `execute_tool`.
- Implemented: real `file_reader`, real `sql_query`, real `rag_search`, trace
  logging through the tool execution API, and lightweight local RAG
  indexing/query modules.
- Not implemented yet: Planner, Executor, Reporter, MCP/GitHub integration,
  HITL, eval cases, persistent report files, and full automatic task execution
  from `POST /api/tasks`.
