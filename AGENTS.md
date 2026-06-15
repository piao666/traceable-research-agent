# AGENTS.md - Traceable Research Agent

## Source of Truth

This file and `task.txt` in this repository root are the only active project
constraint and task-record files:

- `E:\BOSS\traceable-research-agent\AGENTS.md`
- `E:\BOSS\traceable-research-agent\task.txt`

Outer files under `E:\BOSS\AGENTS.md` and `E:\BOSS\task.txt` are backups only.
Do not use them as the active source of constraints or progress records.

## Project Identity

Project name: Traceable Research Agent

Repository: `https://github.com/piao666/traceable-research-agent`

Local path: `E:\BOSS\traceable-research-agent`

Planning document location: `E:\BOSS`, expected filename matching
`Traceable_Research_Agent_*.docx`.

This is an independent resume-grade Agent engineering repository. It is not a
module inside `agent-service-toolkit`, and it is not a direct GPT Researcher
fork. GPT Researcher is a read-only architectural reference.

One-line boundary:

- Existing Agent+RAG backend projects answer questions from a knowledge base.
- Traceable Research Agent executes multi-step research tasks through tools and
  records traceable evidence for every tool call.

## Mandatory Reading Before Coding

Before implementing core functionality, complete a reading pass and update the
repository-root `task.txt`.

Read GPT Researcher in read-only mode:

1. `README.md`: positioning, features, installation, Docker, local document, MCP.
2. `main.py`: service startup entry.
3. `backend/server/app.py`: FastAPI app, report APIs, WebSocket/log streaming,
   file upload/delete behavior.
4. `gpt_researcher/agent.py`: `GPTResearcher`, `conduct_research`,
   `write_report`, `report_source`, `mcp_configs`, costs and step costs.
5. `gpt_researcher/skills/researcher.py`: planning, web/local/hybrid/vector/MCP
   branches, sub-query flow, result aggregation.
6. `gpt_researcher/skills/writer.py`: introduction, conclusion, final report.
7. `gpt_researcher/document/document.py`: local formats and loader strategy.
8. `gpt_researcher/mcp/*`: client lifecycle, tool selection, research
   execution, streaming/logging, security considerations.
9. Docker and local startup files.

Read the planning document under `E:\BOSS`. If direct `.docx` reading fails,
extract it with a local script or `python-docx`. Record the extraction method
and key conclusions in `task.txt`.

Key planning conclusions:

- Independent repo: `traceable-research-agent`.
- Core value: task execution loop, Tool Registry, trace persistence,
  file/SQL/RAG/MCP tools, FastAPI task APIs, evaluation system.
- MVP should not be blocked by MCP. MCP/GitHub integration is a later
  enhancement.
- Planner can start with fixed/controlled JSON plans before fully autonomous
  tool selection.
- Markdown report is required; PDF is optional.
- SQLite + SQLAlchemy are the default persistence layer.
- Every tool call must be persisted into `tool_traces`.
- Failures must be visible through status and trace, not hidden in logs.

## Hard Constraints

1. Keep all project work inside `E:\BOSS\traceable-research-agent`.
2. Do not add this project under `agent-service-toolkit`.
3. Do not modify the GPT Researcher reference repository.
4. Do not copy large GPT Researcher source blocks. Reuse design ideas only.
5. Do not use concrete company project names in code, docs, commit messages,
   examples, environment files, or demo data.
6. Do not commit secrets, tokens, `.env`, private data, local database files
   with private content, caches, virtual environments, bulky generated reports,
   or model artifacts.
7. Tools are read-only by default. Risky operations must support dry-run and/or
   human confirmation.
8. Do not start from complex MCP. First make file, SQL, RAG, report, trace, and
   API loop stable.
9. All meaningful work must be recorded in repository-root `task.txt` before
   and after implementation.
10. At every phase checkpoint, run available tests/smoke checks, update docs and
    task log, commit, and push to GitHub.

## Target Architecture

Expected directory layout:

```text
traceable-research-agent/
|-- app/
|   |-- main.py
|   |-- config.py
|   |-- schemas.py
|   |-- database.py
|   |-- api/
|   |   |-- tasks.py
|   |   |-- tools.py
|   |   `-- reports.py
|   |-- agent/
|   |   |-- state.py
|   |   |-- graph.py
|   |   |-- planner.py
|   |   |-- executor.py
|   |   |-- reporter.py
|   |   `-- prompts.py
|   |-- tools/
|   |   |-- base.py
|   |   |-- registry.py
|   |   |-- file_reader.py
|   |   |-- sql_query.py
|   |   |-- rag_search.py
|   |   |-- mcp_github.py
|   |   `-- report_writer.py
|   |-- rag/
|   |   |-- loader.py
|   |   |-- chunker.py
|   |   |-- embeddings.py
|   |   |-- vector_store.py
|   |   `-- build_index.py
|   |-- trace/
|   |   |-- models.py
|   |   |-- store.py
|   |   `-- logger.py
|   `-- eval/
|       |-- cases.jsonl
|       `-- run_eval.py
|-- workspace/
|   |-- docs/
|   |-- reports/
|   `-- demo.sqlite
|-- scripts/
|   |-- init_demo_db.py
|   |-- build_rag_index.py
|   `-- smoke_test.sh
|-- tests/
|-- task.txt
|-- AGENTS.md
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
|-- .env.example
`-- README.md
```

If implementation needs to adjust this layout, explain why in `task.txt` and
keep the public API stable.

## Required API Surface

Implement these endpoints progressively:

| Endpoint | Purpose | Required fields/behavior |
|---|---|---|
| `GET /health` | Health check | Return service/database readiness. |
| `POST /api/tasks` | Create and execute a task | Return `run_id`, `status`, `status_url`, `trace_url`, `report_url`. |
| `GET /api/tasks/{run_id}` | Query task status | Return `status`, `current_step`, `total_steps`, `report_path`, `error_message`. |
| `GET /api/tasks/{run_id}/trace` | Query tool traces | Return trace list with `step_no`, `tool_name`, `status`, summaries, latency, error. |
| `GET /api/reports/{run_id}` | Fetch Markdown report | Return report markdown and path. |
| `GET /api/tools` | List registered tools | Return tool name, description, risk level, schema, confirmation requirement. |
| `POST /api/tasks/{run_id}/confirm` | Human confirmation | Required for high-risk/resumable operations in later phases. |

## State Machine

Required statuses:

```text
pending -> running -> completed
pending -> running -> failed
running -> waiting_human -> running -> completed
running -> waiting_human -> failed
```

Rules:

- Create an `agent_runs` row as soon as a task is accepted.
- Update `current_step`, `total_steps`, `plan_json`, `error_message`,
  `report_path`, and timestamps consistently.
- Failed tool calls must create failed traces before the run is marked failed or
  recovered.
- API errors should be structured. Do not crash silently.

## Database Requirements

Use SQLite + SQLAlchemy by default.

Required `agent_runs` columns:

```sql
CREATE TABLE agent_runs (
  run_id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  report_type TEXT NOT NULL,
  source_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step INTEGER DEFAULT 0,
  total_steps INTEGER DEFAULT 0,
  plan_json TEXT,
  allowed_tools_json TEXT,
  report_path TEXT,
  error_message TEXT,
  total_tool_calls INTEGER DEFAULT 0,
  total_latency_ms INTEGER DEFAULT 0,
  estimated_cost REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Required `tool_traces` columns:

```sql
CREATE TABLE tool_traces (
  trace_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_no INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  input_summary TEXT,
  input_json TEXT,
  output_summary TEXT,
  output_json TEXT,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  error_message TEXT,
  token_in INTEGER DEFAULT 0,
  token_out INTEGER DEFAULT 0,
  estimated_cost REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);
```

Optional later table: `prompt_versions`.

## Tool Registry Requirements

Create a unified tool layer.

Minimum abstractions:

- `ToolSpec`: name, description, input schema, output schema/summary, risk
  level, requires confirmation, timeout, enabled flag.
- `ToolResult`: status, output, output summary, error message, metadata,
  latency.
- `register_tool`: register tool spec and handler.
- `list_tools`: expose tool metadata to API.
- `execute_tool`: validate input, enforce safety rules, call handler, persist
  trace, return structured result.

Required tools by phase:

| Tool | Phase | Required constraints |
|---|---:|---|
| `file_reader` | Day 6-9 | Only read under `workspace/docs`; block path traversal; support max chars; trace success/failure. |
| `sql_query` | Day 6-9 | Only allow `SELECT`/`WITH`; reject `DELETE`, `UPDATE`, `INSERT`, `DROP`, `ALTER`, `CREATE`; apply limit. |
| `rag_search` | Day 6-9 | Return top-k chunks with source, chunk id, score; trace query/hits/top scores. |
| `report_writer` | Day 10-14 | Generate structured Markdown from observations/evidence; save under `workspace/reports`. |
| `mcp_github_search` | Day 15-19 | Read-only GitHub API/mock first; MCP integration optional; token from env only. |

## Agent Flow

Target execution chain:

```text
START
  -> create_run
  -> route_task
  -> plan_task
  -> execute_step
  -> tool_call
  -> trace_write
  -> summarize_observation
  -> has_more_steps?
      -> yes: execute_step
      -> no: generate_report
  -> save_report
  -> finish_run
END
```

MVP can use deterministic JSON plans. Later phases may use LLM-generated plans
with strict schema validation.

Planner output must be JSON-serializable and stored in `agent_runs.plan_json`.
Executor must never call an unregistered tool. Reporter must use task, plan,
observations, and evidence rather than simply concatenating raw outputs.

## Safety Requirements

### File safety

- Allow reads only under `workspace/docs`.
- Normalize and resolve paths before reading.
- Block `..`, absolute external paths, symlinks that escape workspace, and
  unsupported extensions.
- Enforce `max_chars`.

### SQL safety

- Allow only read queries: `SELECT` or `WITH`.
- Reject write/DDL/destructive operations by parser or conservative keyword
  check.
- Enforce row limit.
- Trace rejected queries as safety hits.

### MCP/GitHub safety

- Use read-only operations only.
- Do not print tokens.
- Load tokens only from environment variables.
- Timeout external calls.
- If MCP is unstable, use GitHub API/mock to preserve main project progress.

### Human confirmation

High-risk tools must return `waiting_human` before execution. Human confirmation
should be implemented in Day 15-19.

## Quality Gates

Before every checkpoint commit:

1. Update `task.txt` with completed work, changed files, commands run, test
   result, unresolved issues, and next phase plan.
2. Run available checks, such as:
   - `python -m pytest`
   - `python -m uvicorn app.main:app --reload` for manual API check
   - smoke scripts under `scripts/`
   - eval script when available
3. Verify API responses manually or via curl for phase deliverables.
4. Update README if usage changed.
5. Verify no secrets, `.env`, cache, local DB, bulky generated files, or
   reference repositories are staged.
6. Commit with a clear generic message.
7. Push to GitHub.

Suggested checkpoint commits:

- `checkpoint(day1-5): bootstrap api database and tool registry`
- `checkpoint(day6-9): implement file sql rag tools with traces`
- `checkpoint(day10-14): complete planner executor reporter e2e loop`
- `checkpoint(day15-19): add hitl github docker eval and docs`

Optional tags:

- `checkpoint-day1-5`
- `checkpoint-day6-9`
- `checkpoint-day10-14`
- `checkpoint-day15-19`

## Four Major Phases

### Phase 1: Day 1-5 - Reading, bootstrap, API skeleton, database, registry

Goals:

- Read GPT Researcher and the project planning document.
- Create/confirm `AGENTS.md` and `task.txt`.
- Build FastAPI skeleton with `/health`, `/api/tasks`, `/api/tools` mock or
  minimal behavior.
- Add SQLite + SQLAlchemy models for `agent_runs` and `tool_traces`.
- Implement Tool Registry abstractions and `GET /api/tools`.

Checkpoint standard:

- Service starts locally.
- Task creation creates a run record.
- Tool list endpoint works.
- `task.txt` contains reading notes and Day 1-5 completion detail.
- Commit and push to GitHub.

### Phase 2: Day 6-9 - file_reader, sql_query, RAG build/search with trace

Goals:

- Implement `file_reader` with whitelist and max chars.
- Implement `sql_query` with read-only safety and limit.
- Build demo SQLite data.
- Build RAG loader/chunker/embedding/vector store.
- Register `rag_search` and persist traces for all tool calls.

Checkpoint standard:

- File success and file missing both produce traces.
- `SELECT` succeeds and `DELETE` is rejected with visible trace.
- RAG returns top-k chunks with source metadata.
- Commit and push to GitHub.

### Phase 3: Day 10-14 - Planner, Executor, Reporter, E2E loop, exceptions

Goals:

- Implement Planner with JSON plan output.
- Implement Executor to run steps through Tool Registry.
- Implement Reporter to generate structured Markdown.
- Implement `/api/tasks/{run_id}`, trace query, and report query fully.
- Add robust exception handling for missing tool, invalid args, illegal SQL,
  missing file, empty RAG, and model timeout.

Checkpoint standard:

- `POST /api/tasks -> trace -> report` works end to end.
- Reports are saved under `workspace/reports/{run_id}.md`.
- Failure cases do not crash the service and are visible through status and
  trace.
- Commit and push to GitHub.

### Phase 4: Day 15-19 - HITL, GitHub/MCP, Docker, eval, README/interview docs

Goals:

- Implement human-in-the-loop confirmation for risky operations.
- Add `mcp_github_search` as read-only GitHub API/mock first; MCP integration
  optional.
- Add Dockerfile and docker-compose.
- Add 10 eval cases and eval report generation.
- Complete README, architecture notes, trace examples, DB schema, bad cases,
  and resume/interview materials.

Checkpoint standard:

- `waiting_human` and confirm flow work.
- GitHub/MCP tool returns traceable read-only results.
- `docker compose up --build` starts the app.
- 10 eval cases run; at least 7 task successes; all failures are visible; trace
  completeness is checked.
- Commit and push to GitHub.

## `task.txt` Rules

`task.txt` is the project execution ledger. It must be maintained from the first
session. Write only to repository-root `task.txt`, not the outer backup file.

Required sections:

```text
# Traceable Research Agent Task Log

## Project Paths
## Original GPT Researcher Reading Notes
## Project Planning Document Reading Notes
## Global Constraints
## Phase Plan: Day 1-5 / Day 6-9 / Day 10-14 / Day 15-19
## Current Phase Checklist
## Completed Work Log
## Commands Run
## Test / Smoke Results
## Git Checkpoints
## Open Issues / Risks
## Next Actions
```

Every completed item should include:

- Date/time if available.
- Files changed.
- What was implemented.
- Commands run.
- Test result.
- Known limitations.
- Commit hash after push if available.

## Coding Style

- Prefer simple, readable Python over over-engineered abstractions.
- Keep module boundaries clear: API layer, agent layer, tools layer, trace
  layer, rag layer.
- Use Pydantic schemas for API inputs/outputs and tool inputs where practical.
- Use type hints for public functions.
- Keep exceptions structured and user-visible through API and trace.
- Write small tests around safety-critical code, especially SQL safety, path
  whitelist, and trace persistence.
- Keep dependencies minimal and document them in `requirements.txt`.

## Definition of Done

Final project is acceptable only when:

- Independent GitHub repository exists and contains this project.
- FastAPI starts successfully.
- `/api/tasks` creates tasks.
- `/api/tasks/{run_id}` returns status.
- `/api/tasks/{run_id}/trace` returns tool traces.
- `/api/reports/{run_id}` returns Markdown report.
- `/api/tools` returns Tool Registry metadata.
- `file_reader`, `sql_query`, `rag_search`, and `report_writer` are usable.
- SQLite contains `agent_runs` and `tool_traces`.
- At least 5 smoke demos and 10 eval cases exist.
- README includes startup, API, trace, DB, evaluation, bad cases, and
  resume/interview explanation.
- Each major phase has a checkpoint commit pushed to GitHub.
