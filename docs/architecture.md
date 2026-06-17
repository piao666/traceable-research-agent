# Architecture

## Project Positioning

Traceable Research Agent is a task-oriented research agent backend. Its core
value is not only answering a question, but executing a planned sequence of
tools and preserving evidence for every tool call.

## Components

```text
FastAPI
  -> Planner
  -> agent_runs.plan_json
  -> Executor
  -> Tool Registry
  -> tool_traces
  -> Reporter
  -> Markdown report
```

## API Flow

1. `POST /api/tasks` creates a pending run and stores a deterministic plan.
2. `GET /api/tasks/{run_id}/plan` lets the user inspect the plan.
3. `POST /api/tasks/{run_id}/run` manually executes the persisted plan.
4. `GET /api/tasks/{run_id}/trace` exposes all tool call traces.
5. `GET /api/reports/{run_id}` returns the generated Markdown report.

`POST /api/tasks` never auto-runs tools.

## State Machine

- `pending`: task and plan exist, tools have not started.
- `running`: manual executor is active.
- `waiting_human`: executor stopped before a confirmation-required step.
- `completed`: executor finished and Reporter generated a report.
- `failed`: system or human rejection stopped the run.

## SQLite Tables

`agent_runs` stores run metadata, task text, status, counters, plan JSON,
report path, and error message.

`tool_traces` stores one row per real tool call, including step number, tool
name, input summary, output summary, status, latency, and error message.

## Tool System

- `file_reader`: read-only local document reader.
- `sql_query`: read-only demo SQLite query tool.
- `rag_search`: local vector index search.
- `mcp_github_search`: read-only GitHub/MCP-style adapter, mock by default.
- `report_writer`: planner step handled by deterministic Reporter.

## Safety Design

- Path whitelist: `file_reader` resolves paths and only allows `workspace/docs`.
- SQL read-only: `sql_query` only allows SELECT/WITH and rejects risky keywords.
- Read-only GitHub: `mcp_github_search` only returns search evidence.
- HITL: confirmation-required report steps stop at `waiting_human`.
- Trace visibility: success, failure, and safety rejection are persisted.
