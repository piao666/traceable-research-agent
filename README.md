# Traceable Research Agent

Traceable Research Agent is an independent FastAPI project for building a
traceable task-oriented research agent. The Day15 version exposes health, task,
plan, manual execution, tool catalog, trace, and report endpoints, with task
runs, plans, tool traces, and report paths persisted to local SQLite.

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
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/{run_id}/plan
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks/{run_id}/run `
  -Method POST `
  -ContentType "application/json" `
  -Body '{}'
Invoke-RestMethod http://127.0.0.1:8000/api/reports/{run_id}
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks/{run_id}/confirm `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"approved":true,"comment":"Approved for demo.","resume":true}'
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

## Phase 3 Day10-12 Capabilities

- `POST /api/tasks` now creates a pending run and stores a deterministic
  `plan_json` in `agent_runs`. It does not execute tools automatically.
- The deterministic JSON Planner maps task keywords to `file_reader`,
  `sql_query`, `rag_search`, and `report_writer` steps, applies
  `allowed_tools` restrictions, keeps `step_no` values consecutive, and does
  not call any LLM, external API, or tool execution path.
- `GET /api/tasks/{run_id}/plan` returns the persisted plan, including
  `version`, `task`, `source_mode`, `allowed_tools`, `steps`, and `notes`.
- `POST /api/tasks/{run_id}/run` manually executes the persisted plan. The
  Executor calls Tool Registry handlers for `file_reader`, `sql_query`, and
  `rag_search`, writes one `tool_traces` row per real tool call, tracks
  progress and latency, and leaves `POST /api/tasks` as create-and-plan only.
- `report_writer` planner steps are handled by the deterministic Reporter,
  not by the Tool Registry stub.
- The Markdown Reporter writes `workspace/reports/{run_id}.md` from the task,
  plan, observations, and trace summaries. Generated reports are runtime
  artifacts and are ignored by Git.
- `GET /api/reports/{run_id}` now reads the real Markdown report when it
  exists and returns `exists=false` with a clear placeholder when it has not
  been generated yet.

## Phase 3 Day13-15 Capabilities

- Manual end-to-end demo flow is the primary path:
  `POST /api/tasks` -> `GET /api/tasks/{run_id}/plan` ->
  `POST /api/tasks/{run_id}/run` -> `GET /api/tasks/{run_id}/trace` ->
  `GET /api/reports/{run_id}`.
- `POST /api/tasks` remains create-and-plan only. It returns `status_url`,
  `trace_url`, `report_url`, plus `plan_url` and `run_url`.
- Repeated `POST /api/tasks/{run_id}/run` on a completed run returns the
  current completed summary with `Run already completed; no tools executed.`
  and does not add duplicate traces.
- `GET /api/reports/{run_id}` before execution returns `exists=false` and the
  message `Report has not been generated yet. Run POST /api/tasks/{run_id}/run first.`
- Exception visibility is hardened for file missing, path traversal rejection,
  SQL write rejection, SQL runtime errors, RAG index missing, invalid arguments,
  unknown tools, empty plans, repeated runs, and report-before-run cases.
- Tool results that fail or are safety rejected are persisted as `tool_traces`
  with `failed` or `rejected` status; successful tool calls remain `success`.
- Minimal HITL is available through `waiting_human` and
  `POST /api/tasks/{run_id}/confirm`.
- HITL trigger is deterministic: tasks containing `human approval`,
  `human confirm`, `requires confirmation`, or the equivalent Chinese phrases
  mark the `report_writer` step as `risk_level=high` and
  `requires_confirmation=true`.
- If execution reaches a step requiring confirmation, the run stops with
  `status=waiting_human`, preserves completed prior steps, and does not generate
  the report until confirmation is approved.
- `POST /api/tasks/{run_id}/confirm` accepts `approved`, `comment`, and
  `resume`. Rejection sets the run to `failed`; approval with `resume=false`
  records confirmation and leaves the run pending; approval with `resume=true`
  resumes from the waiting step without repeating completed traces.
- Reports now include Human Confirmation, Failure / Rejection Details, Trace
  Summary, and Runtime Limitations sections when relevant.

Phase 3 checkpoint notes are available at
`docs/checkpoints/phase3_day10_15_checkpoint.md`.

## Phase 3 Day10-15 CheckPoint

- Full smoke passed for deterministic Planner, manual Executor, Markdown
  Reporter, exception visibility, repeated-run guard, report-before-run
  behavior, and minimal HITL confirmation.
- Normal API flow remains manual:
  create task, inspect plan, call `/run`, inspect trace, and read report.
- `POST /api/tasks` intentionally does not auto-run.
- Runtime artifacts remain ignored: SQLite databases, RAG index files, and
  generated Markdown reports.

## Current Scope

- Implemented: FastAPI skeleton, `/health`, database-backed `/api/tasks`,
  registry-backed `/api/tools`, and real report lookup through
  `/api/reports/{run_id}`.
- Implemented: SQLite database setup, `agent_runs` and `tool_traces` ORM
  tables, database-backed task creation/status, and reserved trace listing.
- Implemented: Tool Registry metadata with `ToolSpec`, `ToolResult`,
  `register_tool`, `get_tool`, `list_tools`, and `execute_tool`.
- Implemented: real `file_reader`, real `sql_query`, real `rag_search`, trace
  logging through the tool execution API, and lightweight local RAG
  indexing/query modules.
- Implemented: deterministic Planner, manual Executor step loop, trace writing
  from executor, and deterministic Markdown Reporter.
- Implemented: Day13 manual demo polish, Day14 exception visibility, and Day15
  minimal HITL confirmation/resume.
- Not implemented yet: MCP/GitHub real integration, Docker, eval cases,
  production auth, async background job queue, LLM planner, and full automatic
  task execution from `POST /api/tasks`.
