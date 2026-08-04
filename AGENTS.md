# AGENTS.md - Traceable Research Agent

## Source Of Truth

This file and repository-root `TASK.md` are the only active project constraint
and progress-record files. Keep both tracked in Git. Do not recreate or use
`task.txt`.

## Project Identity

- Project: Traceable Research Agent
- Repository: `https://github.com/piao666/traceable-research-agent`
- Local workspace: repository root (do not depend on a machine-specific path)
- Deployment: open-source, self-hosted, single-instance Docker application

The platform executes multi-step research tasks through read-only tools and
persists traceable evidence for every tool call. It is an independent project,
not a GPT Researcher fork. GPT Researcher is a read-only design reference.

## Product Boundary

- Do not implement tenant isolation, tenant headers, tenant IDs, or user IDs.
- Sessions and optional memory belong to the single local deployment.
- Users deploy with Docker and configure API keys and runtime settings in
  `.env`; `.env` must never be committed.
- The project does not provide RAG, embeddings, vector stores, document
  indexing, or a generic retrieval abstraction. Deployments can integrate
  their own retrieval through read-only tools or MCP.
- MCP is optional. The core task, trace, evidence, report, file, SQL, and web
  research loop must work without it.

## Hard Constraints

1. Keep project work inside the repository.
2. Do not modify external reference repositories or copy large source blocks.
3. Do not use concrete company project names in code, docs, examples, or demo
   data.
4. Never commit secrets, `.env`, private data, local databases, caches, virtual
   environments, generated reports, or model artifacts.
5. Tools are read-only by default. Risky operations require dry-run and/or
   human confirmation.
6. Persist every tool call in `tool_traces`; failures must be visible in run
   status and traces rather than hidden in logs.
7. Record meaningful work in repository-root `TASK.md` before and after
   implementation.
8. At checkpoints, run available tests and smoke checks, update docs and
   `TASK.md`, inspect staged content, then commit and push.

## Required API Surface

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service and database readiness |
| `POST /api/tasks` | Create and execute a task |
| `GET /api/tasks/{run_id}` | Query task status |
| `GET /api/tasks/{run_id}/trace` | Query persisted tool traces |
| `GET /api/reports/{run_id}` | Fetch a Markdown report |
| `GET /api/tools` | List registered tool metadata |
| `POST /api/tasks/{run_id}/confirm` | Resume a confirmed high-risk operation |

## State And Persistence

Required run states:

```text
pending -> running -> completed
pending -> running -> failed
running -> waiting_human -> running -> completed
running -> waiting_human -> failed
```

Use SQLite and SQLAlchemy by default. Create `agent_runs` when a task is
accepted, keep plan/progress/report/error/cost fields consistent, and write a
failed trace before a failed tool call is recovered or the run is failed.

## Tool Registry And Safety

The unified registry exposes `ToolSpec`, `ToolResult`, registration, listing,
validated execution, timeout enforcement, safety policy, and trace
persistence. The executor must never call an unregistered tool.

- File reads stay under `workspace/docs`, block traversal and escaping
  symlinks, restrict extensions, and enforce `max_chars`.
- SQL permits only read-only `SELECT`/`WITH`, rejects writes and DDL, and
  enforces a row limit.
- External HTTP, GitHub, and MCP operations are read-only, load tokens from
  environment variables, redact secrets, and enforce timeouts.
- High-risk tools enter `waiting_human` before execution.

## Agent Flow

```text
create_run -> route_task -> plan_task -> execute_step -> tool_call
-> trace_write -> summarize_observation -> generate_report -> finish_run
```

Plans must be JSON-serializable and persisted. Reports must use the task, plan,
observations, and evidence, rather than concatenate raw tool output.

## Quality Gates

Before a checkpoint commit:

1. Update `TASK.md` with files changed, implementation details, commands,
   results, limitations, and next actions.
2. Run `python -m compileall`, the full test suite, relevant smoke checks, a
   live API check, Streamlit startup check, and `docker compose config` when
   available.
3. Update README and environment examples when behavior changes.
4. Verify no secrets, `.env`, cache, database, or bulky generated file is
   staged.
5. Commit with a generic descriptive message and push the current branch.

## Coding Style

- Prefer simple typed Python and clear API, agent, tool, trace, evidence, and
  memory module boundaries.
- Use Pydantic for structured inputs and responses where practical.
- Keep exceptions structured and user-visible.
- Add focused tests for safety policy, persistence, and public contracts.
- Keep dependencies minimal and pinned for reproducible Docker deployment.

## Definition Of Done

The FastAPI and Streamlit applications start, required APIs work, registered
read-only tools are traceable, reports are persisted, Docker deployment needs
only `.env` configuration, tests and smoke demos pass, documentation matches
the implemented product, and the checkpoint is committed and pushed.
