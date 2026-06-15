# AGENTS.md - Traceable Research Agent

## Project Identity

Project name: Traceable Research Agent

Repository: https://github.com/piao666/traceable-research-agent

Local path: `E:\BOSS\traceable-research-agent`

This is an independent Agent engineering repository. It is not a module inside
`agent-service-toolkit`, and it is not a direct GPT Researcher fork. GPT
Researcher is a read-only architectural reference.

One-line boundary:

- Existing Agent+RAG backend projects answer questions from a knowledge base.
- Traceable Research Agent executes multi-step research tasks through tools and
  records traceable evidence for every tool call.

## Hard Constraints

- Keep all project work inside `E:\BOSS\traceable-research-agent`.
- Do not modify the GPT Researcher reference repository.
- Do not copy large GPT Researcher source blocks. Reuse design ideas only.
- Do not commit secrets, tokens, `.env`, private data, caches, local databases,
  bulky generated reports, or model artifacts.
- Do not use concrete company project names in code, docs, examples, demo data,
  or commit messages.
- Tools are read-only by default. Risky operations must require dry-run and/or
  human confirmation.
- MCP/GitHub integration is a later enhancement and must not block the MVP.
- Every tool call, including failures and safety rejections, must be persisted
  as a trace.
- Each major phase must end with `task.txt` updates, checks, a checkpoint
  commit, and a GitHub push.

## Required Reading Before Coding

Before implementing core functionality, read and summarize:

- GPT Researcher README, `main.py`, `backend/server/app.py`,
  `gpt_researcher/agent.py`, `gpt_researcher/skills/researcher.py`,
  `gpt_researcher/skills/writer.py`, `gpt_researcher/document/document.py`,
  `gpt_researcher/mcp/*`, Docker and compose files.
- The local project planning document under `E:\BOSS`, currently:
  `Traceable_Research_Agent_独立项目规划与GPT_Researcher必读结果.docx`.

Record reading conclusions in `task.txt` before writing core application code.

## Target Architecture

Use a simple FastAPI backend with clear boundaries:

- `app/api`: HTTP endpoints.
- `app/agent`: planner, executor, state, reporter.
- `app/tools`: Tool Registry, tool specs, tool implementations.
- `app/rag`: document loading, chunking, embeddings, vector store.
- `app/trace`: SQLAlchemy models and trace persistence.
- `app/eval`: evaluation cases and runner.
- `workspace/docs`: allowed local document input directory.
- `workspace/reports`: generated report output directory.

SQLite + SQLAlchemy is the default persistence layer.

## Required API Surface

Implement progressively:

- `GET /health`
- `POST /api/tasks`
- `GET /api/tasks/{run_id}`
- `GET /api/tasks/{run_id}/trace`
- `GET /api/reports/{run_id}`
- `GET /api/tools`
- `POST /api/tasks/{run_id}/confirm`

## Required State Machine

Supported statuses:

```text
pending -> running -> completed
pending -> running -> failed
running -> waiting_human -> running -> completed
running -> waiting_human -> failed
```

Rules:

- Create an `agent_runs` row when a task is accepted.
- Store `plan_json`, allowed tools, progress, errors, report path, timestamps,
  total tool calls, latency, and estimated cost.
- Failed tool calls must create failed traces before the run is marked failed or
  recovered.
- API errors must be structured and visible.

## Required Tables

`agent_runs` stores run-level state:

- `run_id`, `task`, `report_type`, `source_mode`, `status`
- `current_step`, `total_steps`, `plan_json`, `allowed_tools_json`
- `report_path`, `error_message`
- `total_tool_calls`, `total_latency_ms`, `estimated_cost`
- `created_at`, `updated_at`

`tool_traces` stores one row per tool call:

- `trace_id`, `run_id`, `step_no`, `tool_name`
- `input_summary`, `input_json`
- `output_summary`, `output_json`
- `status`, `latency_ms`, `error_message`
- `token_in`, `token_out`, `estimated_cost`
- `created_at`, `finished_at`

## Tool Registry Requirements

Minimum abstractions:

- `ToolSpec`: name, description, schema, risk level, confirmation requirement,
  timeout, enabled flag.
- `ToolResult`: status, output, output summary, error message, metadata,
  latency.
- `register_tool`, `list_tools`, `execute_tool`.

Minimum tools by phase:

- Day 6-9: `file_reader`, `sql_query`, `rag_search`.
- Day 10-14: `report_writer`.
- Day 15-19: read-only `mcp_github_search` or GitHub API/mock fallback.

Safety requirements:

- `file_reader` reads only under `workspace/docs`, blocks path traversal and
  escaping symlinks, and enforces `max_chars`.
- `sql_query` allows only `SELECT` or `WITH`, rejects write/DDL/destructive
  operations, and applies limits.
- MCP/GitHub tools are read-only, load tokens only from environment variables,
  and must not print tokens.

## Four Phases

### Phase 1: Day 1-5

Reading, bootstrap, FastAPI skeleton, SQLite models, Tool Registry base.

Checkpoint acceptance:

- Service starts locally.
- `/health` works.
- `POST /api/tasks` creates a run record.
- `GET /api/tools` returns tool metadata.
- `agent_runs` and `tool_traces` tables exist.
- `task.txt` has reading notes and completion details.

### Phase 2: Day 6-9

Implement `file_reader`, `sql_query`, RAG build/search, and trace persistence
for success, failure, and safety blocks.

### Phase 3: Day 10-14

Implement deterministic Planner, Executor, Reporter, full task/status/trace/
report endpoints, and robust exception handling.

### Phase 4: Day 15-19

Implement human confirmation, read-only GitHub/MCP tool, Docker Compose, eval
cases, README, trace examples, bad cases, and interview materials.

## Checkpoint Rules

Before each checkpoint commit:

1. Update `task.txt`.
2. Run available tests or smoke checks.
3. Record commands and results in `task.txt`.
4. Verify no secrets, `.env`, cache, local DB, or bulky generated files are
   staged.
5. Commit with a generic checkpoint message.
6. Push to GitHub.

