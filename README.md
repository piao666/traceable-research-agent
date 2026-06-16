# Traceable Research Agent

Traceable Research Agent is an independent FastAPI project for building a
traceable task-oriented research agent. The Day 1-3 version is a runnable mock
skeleton: it exposes health, task, tool catalog, and report endpoints without
database persistence or real tool execution.

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

- Implemented: FastAPI app, `/health`, mock `/api/tasks`, mock `/api/tools`,
  mock `/api/reports/{run_id}`.
- Not implemented yet: SQLite, SQLAlchemy models, real Tool Registry, Agent
  Planner, Executor, RAG, MCP, and persistent reports.
