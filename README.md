# Traceable Research Agent

Traceable Research Agent is an independent FastAPI project for building a
traceable task-oriented research agent. The Day5 version exposes health, task,
tool catalog, and report endpoints, with task runs persisted to local SQLite and
tool metadata managed by a Tool Registry.

## Local Start

```bash
python -m pip install -r requirements.txt
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
  -Body '{"arguments":{"path":"demo.md","max_chars":1000}}'
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"task":"Read local docs and generate a traceable report","report_type":"summary","source_mode":"mock","allowed_tools":["file_reader","report_writer"]}'
```

## Current Scope

- Implemented: FastAPI skeleton, `/health`, database-backed `/api/tasks`,
  registry-backed `/api/tools`, and mock `/api/reports/{run_id}`.
- Implemented: SQLite database setup, `agent_runs` and `tool_traces` ORM
  tables, database-backed task creation/status, and reserved trace listing.
- Implemented: Tool Registry metadata with `ToolSpec`, `ToolResult`,
  `register_tool`, `get_tool`, `list_tools`, and `execute_tool` stub behavior.
- Not implemented yet: real `file_reader`, real `sql_query`, real
  `rag_search`, real planner/executor, real reporter, MCP/GitHub integration,
  and persistent report files.
