# Day29 API Key Auth, Tenant Context, And Async Run CheckPoint

## Scope

- Optional demo API-key authentication.
- Tenant/User request context without database persistence.
- FastAPI BackgroundTasks async run endpoint.
- Streamlit authentication and async-run controls.
- Default, auth-enabled, state-machine, API, and UI regression validation.

## Implementation Summary

- `app/security/auth.py` reads the configured API-key header or an
  `Authorization: Bearer` credential and returns stable 401, 403, or 503
  errors without exposing credential values.
- `app/security/context.py` sanitizes Tenant/User header values and attaches a
  request-only context to `request.state`.
- `POST /api/tasks/{run_id}/run_async` atomically claims pending runs, reuses
  the existing synchronous `run_plan` executor in a fresh database session,
  and returns status, trace, and report URLs immediately.
- `frontend/streamlit_app.py` provides password-masked API Key, Tenant ID,
  User ID, and `Use async run` sidebar controls. Shared HTTP helpers add the
  optional headers without displaying or writing the key.
- `scripts/smoke_auth_async.py` now covers protected endpoints, auth-disabled
  compatibility, tenant sanitization, async state guards, HITL, and disabled
  async fallback.

## Auth Validation

- `AUTH_ENABLED=false` remains the default. Core task, report, and tool APIs
  continue to work without credentials.
- With `AUTH_ENABLED=true`, missing credentials return 401 and invalid
  credentials return 403.
- A configured `X-API-Key` succeeds, and `Authorization: Bearer` succeeds.
- `/health` remains public.
- Router-level dependencies protect:
  - task create, status, plan, synchronous run, async run, trace, and confirm;
  - report retrieval;
  - tool catalog, tool detail, and direct tool execution.
- The enhanced smoke checks every protected endpoint with missing, invalid,
  and configured credentials. No credential value is printed.

## Tenant Context

- `X-Tenant-ID` and `X-User-ID` are trimmed and accept only 1-80 letters,
  numbers, underscores, hyphens, or dots.
- Missing values fall back to `demo` and `local-user`.
- Invalid values also fall back, so they do not pollute `request.state`.
- Tenant/User context is intentionally not written to `agent_runs` or
  `tool_traces`. Persistence is reserved for a future Alembic phase.

## Async Run Validation

- Manual auth-enabled run ID: `08e11beff06c4136bd98abc8bffe0925`.
- Task creation returned pending; `run_async` returned running; polling ended
  at completed with two traces and an existing report.
- Repeating `run_async` on the completed run returned the existing completed
  state and did not add traces.
- The enhanced smoke also verified:
  - a running run is not queued again and writes no duplicate traces;
  - a waiting_human run remains waiting and cannot bypass HITL;
  - approved confirmation with resume completes the HITL run;
  - `ASYNC_RUN_ENABLED=false` returns `Async run is disabled.` with HTTP 400;
  - synchronous `/run` remains available when async execution is disabled.

## Streamlit Validation

- Sidebar source and static smoke confirm API Key `type="password"`, Tenant
  ID, User ID, and `Use async run` controls.
- Shared requests send `X-API-Key`, `X-Tenant-ID`, and `X-User-ID` only when
  values are present.
- Async mode calls `/run_async`; normal mode continues to call `/run`.
- Async start displays a Refresh instruction, and status, trace, and report
  use the existing refresh flow.
- The API key remains only in Streamlit session state and is not rendered,
  printed, or persisted to a file.

## Regression Summary

- Dependency installation check: satisfied.
- `python -m compileall app tests scripts frontend`: passed.
- Demo database initialization and deterministic/JSON index build: passed.
- Planner, E2E, exception, HITL, LLM config, LLM planner, RAG query/backend,
  Streamlit, and enhanced auth/async smoke scripts: passed.
- `smoke_auth_async.py`: all 11 summary checks returned `ok`.
- Eval: 15/15 passed, failed=0, task success rate 1.0, trace complete rate 1.0.
- The only warning was FastAPI TestClient's future `httpx2` migration warning.

## Docker Status

- Docker client: 29.5.2; Docker Compose: v5.1.4.
- The previous Day29 full image build timed out after 10 minutes, after which
  Docker Desktop Linux Engine returned HTTP 500.
- During Day29-B, `docker info` again failed to return within 15 seconds.
- Docker Desktop Linux Engine remains unstable. No new build was attempted,
  and Docker regression was attempted but not accepted as passed.
- Local smoke, eval, Uvicorn API, and Streamlit checks are the accepted Day29
  validation paths.

## Security And Runtime Artifacts

The checkpoint excludes and does not stage:

- `.env` and all real API keys;
- model files under `E:\Models`;
- `workspace/chroma`, `workspace/index`, and `workspace/reports`;
- SQLite databases, eval outputs, caches, and logs.

## Current Limitations

- Tenant/User context is not persisted in the database.
- Alembic migrations and parser-based SQL validation are not implemented.
- Async execution uses in-process FastAPI BackgroundTasks, not a durable
  Celery, RQ, or Arq queue.
- Docker Desktop Linux Engine is currently unstable in the local environment.

## Next Step

Day30 may start after this checkpoint with:

- Alembic migration support.
- Parser-based read-only SQL validation.
