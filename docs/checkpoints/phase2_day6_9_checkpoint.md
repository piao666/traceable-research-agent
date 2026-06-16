# Phase 2 Day6-9 CheckPoint

## Scope

Phase 2 covers Day6-9:

- Day6 real `file_reader`
- Day7 real `sql_query`
- Day8 local RAG foundation
- Day9 `rag_search` Tool Registry handler

## Completed Capabilities

### file_reader

- `workspace/docs` whitelist
- Path resolve validation
- Path traversal rejection
- `max_chars` support with hard cap
- Supported text formats: `.txt`, `.md`, `.csv`, `.json`, `.py`, `.log`
- Trace writing through execute API when `run_id` is provided

### sql_query

- Default demo database: `workspace/demo.sqlite`
- `SELECT` / `WITH` only
- Dangerous keyword rejection
- Default and max row limit
- Trace writing through execute API when `run_id` is provided

### RAG Foundation

- Local docs loader
- Character chunker
- Deterministic lightweight bag-of-words embedding
- JSON vector index
- `scripts/build_rag_index.py`
- `scripts/smoke_rag_query.py`

### rag_search

- Reads `workspace/index/rag_index.json`
- Supports `query` and `top_k`
- Returns top-k chunks with `source`, `chunk_id`, `score`, and `text`
- Handles invalid query
- Handles `index_missing`
- Handles no-hit / empty index
- Trace writing through execute API when `run_id` is provided

## Smoke Test Summary

Date: 2026-06-16

Checkpoint run id:

```text
96ecdf2fa6fb4c6b9b154c8918f02e6c
```

Health result:

```json
{
  "status": "ok",
  "service": "traceable-research-agent",
  "phase": "day9"
}
```

Representative tool results:

- `file_reader` success:
  `Read demo_research_note.md: 1000 chars (truncated)`
- `file_reader` safety rejection:
  `Path is outside workspace/docs.`, `metadata.error_type=safety_rejected`
- `sql_query` success:
  `Returned 5 row(s) with columns: id, title, category.`
- `sql_query` dangerous SQL rejection:
  `Only SELECT or WITH read-only queries are allowed.`,
  `metadata.error_type=safety_rejected`
- `rag_search` success:
  `rag_search returned 3 hits for query: trace tool registry`; top source:
  `demo_research_note.md`
- `rag_search` invalid args:
  `Missing required argument: query.`, `metadata.error_type=invalid_args`

Trace result:

```text
trace_count = 6
success: 3
rejected: 2
failed: 1
```

Trace tools:

```text
1:file_reader:success
2:file_reader:rejected
3:sql_query:success
4:sql_query:rejected
5:rag_search:success
6:rag_search:failed
```

Task and report checks:

- `GET /api/tasks/{run_id}` returned `status=pending`.
- `GET /api/reports/{run_id}` returned mock Markdown.
- This is expected for Phase 2 because Planner, Executor, and Reporter are
  Phase 3 work.

## Commands Run

```powershell
git status
git pull origin main
python -m pip install -r requirements.txt
python -m compileall app tests scripts
python scripts/init_demo_db.py
python scripts/build_rag_index.py
python scripts/smoke_rag_query.py

$p = Start-Process -FilePath python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -PassThru
Start-Sleep -Seconds 5
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tasks -Method POST -ContentType "application/json" -Body '{"task":"Phase 2 checkpoint smoke task for file sql rag traces","report_type":"summary","source_mode":"mock","allowed_tools":["file_reader","sql_query","rag_search"]}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/file_reader/execute -Method POST -ContentType "application/json" -Body "{...step_no 1...}"
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/file_reader/execute -Method POST -ContentType "application/json" -Body "{...step_no 2...}"
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/sql_query/execute -Method POST -ContentType "application/json" -Body "{...step_no 3...}"
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/sql_query/execute -Method POST -ContentType "application/json" -Body "{...step_no 4...}"
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/rag_search/execute -Method POST -ContentType "application/json" -Body "{...step_no 5...}"
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/tools/rag_search/execute -Method POST -ContentType "application/json" -Body "{...step_no 6...}"
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)/trace"
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/reports/$($task.run_id)"
Stop-Process -Id $p.Id -Force
```

## Workspace Git Tracking

`git ls-files workspace` returned:

```text
workspace/data/.gitkeep
workspace/docs/demo_research_note.md
```

## Runtime Artifacts

These files are runtime artifacts and are intentionally not committed:

- `workspace/demo.sqlite`
- `workspace/traceable_research_agent.sqlite`
- `workspace/index/rag_index.json`

`git status --ignored --short workspace` showed them as ignored.

## Current Limitations

- `POST /api/tasks` only creates a pending run and does not execute a plan.
- Planner is not implemented.
- Executor is not implemented.
- Reporter is not implemented.
- Report endpoint still returns mock Markdown.
- MCP/GitHub is not implemented.
- HITL is not implemented.
- Eval cases are not implemented.

## Phase 3 Handoff

Next phase:

- Day10 deterministic JSON Planner.
- Store `plan_json` in `agent_runs`.
- Keep planner deterministic before LLM-based planning.
- Day11 Executor step loop.
- Day12 Reporter Markdown generation.
- Day13 end-to-end task flow.
- Day14 exception handling.
