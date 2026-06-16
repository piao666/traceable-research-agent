# Traceable Research Agent

Traceable Research Agent is an independent FastAPI project for building a
traceable task-oriented research agent. The Day4 version exposes health, task,
tool catalog, and report endpoints, with task runs persisted to local SQLite.

## Local Start

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Smoke Checks

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/tools
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"task":"Read local docs and generate a traceable report","report_type":"summary","source_mode":"mock"}'
```

## Current Scope

- Implemented: FastAPI app, `/health`, database-backed `/api/tasks`, mock
  `/api/tools`, and mock `/api/reports/{run_id}`.
- Implemented: SQLite database setup, `agent_runs` and `tool_traces` ORM
  tables, database-backed task creation/status, and reserved trace listing.
- Not implemented yet: real Tool Registry, Agent Planner, Executor, RAG, MCP,
  and persistent reports.
