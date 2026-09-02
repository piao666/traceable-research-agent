# Traceable Research Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](#quick-start)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](#quick-start)

**Traceable Research Agent** is a self-hosted research application for teams
that need to inspect how an answer was produced. It plans a multi-step task,
runs only registered read-only tools, stores every call and failure as a trace,
and produces an evidence-backed Markdown report.

[中文说明](README_zh.md) | [Quick start](#quick-start) | [API](#api) | [Architecture](#architecture)

## Why Traceable Research Agent

### Research integrity (R0–R3)

Tool success is not research completion. Missing required configuration blocks
execution without discarding a draft. `GET /api/runtime/capabilities` discloses
configuration presence (never secrets); `GET /api/tasks/{run_id}/preflight`
checks the actual plan. Neither endpoint verifies external connectivity.

- Local file/SQL plans need no search or model keys unless LLM mode is requested.
- Empty upstream results skip dependent fetches. Zero usable evidence or a failed
  required fetch/step fails the run before report generation. Partial results
  retain explicit warnings; an explicitly selected LLM report cannot silently
  become a rule-based report when synthesis fails.
- Errors, approvals, memory-recall messages and model finish summaries remain in
  Trace but are not sources. Search snippets and fetched page text are distinct.
  Citation checks evaluate answer text, not the citation index; no citations means
  not evaluated, never 100%. Citation IDs are never repaired by numeric proximity.
- Failed/cancelled runs can be fully retried as a new Run with current configuration
  and fresh approvals. Cancellation cannot be overwritten by late completion.
- Old reports and traces are retained and labelled for review. Legacy quality
  records are excluded from trusted trends/routing; active evidence revisions are
  append-only. No automatic database purge or historical rewrite is performed.

The **Deep Web template**, **ReAct execution mode**, and deployment-level
`DEEP_RESEARCH_ENABLED` switch are separate. The switch only adds rounds to
ReAct. Follow-up learning notes are not supported conclusions: inspect their
linked sub-runs. D01–D11 now have API-connected pages. R6 shared-state,
responsive and keyboard/focus changes are implemented locally; browser visual
and current Figma reconciliation checks remain unverified. R7 regression and
deployment preparation is recorded in [release validation](RELEASE_VALIDATION.md);
Docker/Streamlit runtime, full pytest and visual/provider acceptance remain open.

### Shared UI states and accessibility (R6)

Unknown/loading metrics show `—`, not zero. Task and health requests fail
independently and offer retry. Plan review supports recovery, rejects approval
without explicit ready preflight, synchronizes conflicting Run state, distinguishes
pending approval/rejection and ignores late responses after leaving a Run.
Denied browser storage no longer crashes draft creation or promises a saved draft.

Shared native modals label their purpose, manage initial/return focus and guard
busy operations. Navigation has a skip link, route titles and focus restoration;
status tabs support arrows/Home/End. Evidence/Trace links focus the exact target
without stealing focus on refresh. Tables and scrollable payloads are keyboard
reachable; external links announce a new window. Long text, narrow-screen task
cards, wrapping actions, reduced motion and text-token contrast are addressed.

Run offline frontend checks under `web/`: `npm run typecheck`, `npm run lint`,
`npm test`, `npm run build`. `node qa/smoke.mjs` checks the isolated fixture server;
`npm run dev -- --config qa/vite.config.ts` exposes `/qa/viewport.html` for manual
desktop/390px checks. This QA server disables the API proxy, forces a same-origin
fixture API and rejects all writes; it is not shipped in the production build.
See [QA instructions](web/qa/README.md) and [design mapping limits](web/README.md).
Mock DOM tests do not verify rendered layout, native focus trapping, screen-reader
behavior or complete accessibility conformance. These checks and real-provider
acceptance remain separate gates; final user acceptance stays after R6–R7.

### Local modules (R5 / D08–D11)

- `/sessions`: create/rename sessions, inspect persisted turns and paginated
  linked Runs. Follow-up research carries `session_id`, uses a separate browser
  draft and still requires plan approval. Unknown sessions are rejected before
  Run creation. Session grouping does not inject the entire conversation into
  the planner: include needed background in the next research question.
- `/memory`: pending/active/expired/superseded filters, provenance links and
  explicit confirmation for activate/reject/delete. Rejection permanently deletes
  a pending item; clearing all statuses requires typing the confirmation phrase.
  Source sessions/Runs/reports remain intact. Effective expiry is interpreted
  without rewriting historical rows; expired items are excluded from recall.
- Migration `0010_memory_audit` adds a content-free audit table. Confirm/reject/
  delete/clear and their audit event are transactional; failed audit writes roll
  back the action. `GET /api/memory/audit` returns recent events, not research
  evidence. There is no fabricated audit backfill for past deletions.
- `/capabilities`: registered tools, risk/confirmation/schema details and Skill
  definitions/dependencies. Configuration presence is distinct from runtime
  success; remote MCP is optional. No tool-execute or browser key-edit controls.
- `/system`: `GET /api/runtime/diagnostics` checks actual DB reads/module tables
  and workspace directory permissions without external requests or write probes.
  Quality windows, daily trends and per-Run details use existing integrity gates.
  Empty quality is not evaluable; heuristic scores are not factual accuracy.

R5 offline checks: `python -m unittest tests.test_r5_modules tests.test_memory`
and `python scripts/smoke_research_integrity.py`. The smoke uses a disposable
SQLite DB, verifies session/memory/audit persistence across API restart and
never contacts providers. Startup applies the new migration to the deployment
DB only when you deploy; back up persisted data before deploying migrations.
Code and mocked tests do not replace browser, container or real-provider acceptance.

### Research workspace (R4 / D05–D07)

- `/runs/{id}` shows persisted status, plan, Trace payloads/failures, timing and
  recorded cost estimates. Explicit confirmation controls start, cancellation,
  human approval/rejection and full retry; retry creates a new Run without
  automatically starting it. Plan approval opens the corresponding workspace.
- `/runs/{id}/evidence` shows source snippets/content basis and the exact
  citation → claim → passage → source/Trace association. Missing/ambiguous IDs
  remain unresolved. Export downloads grouped sources/passages as JSON.
- `/runs/{id}/report` reads/downloads Markdown, links exact citation IDs to the
  evidence page and distinguishes not-generated, missing and blocked reports.
  The bounded safe reader supports headings, lists, tables, code and HTTP(S)
  links; it does not execute HTML or load remote images. Download the original
  for unsupported Markdown formatting. A resolved link is not fact verification.
- Live runs use resumable SSE plus 5-second HTTP reconciliation; waiting-human
  runs poll without repeated SSE reconnects, terminal runs close the stream.
  Nginx disables event buffering. List filtering/search/pagination is server-side
  (`status=waiting` covers both approval states, `q` searches task text/Run ID).
  SQLite timestamps without offsets are displayed as UTC converted to local time.
- `POST /api/tasks/{id}/confirm?start_async=true` schedules confirmed execution;
  the default synchronous contract remains compatible. Report JSON adds
  `availability`: `available`, `not_generated`, `missing`, or `blocked`.

Offline R4 checks: `python -m unittest tests.test_r4_workflow` and, under `web/`,
`npm run typecheck`, `npm run lint`, `npm test`, `npm run build`. These are not
real-provider or browser visual acceptance. Follow the remaining release gates
before final user-side deployment/API-key acceptance; never infer completion from Markdown
or a `completed` status alone.

After editing `.env` in the actual deployment directory, recreate the API service
with `docker compose up -d --force-recreate api`, then recheck the plan. No image
rebuild is needed for key-only changes. Do not send keys through the browser UI.

Offline regression commands (from the repository root):

```bash
python -m unittest tests.test_research_integrity -v
python scripts/smoke_research_integrity.py
python scripts/run_offline_tests.py --runner pytest
```

The smoke script copies code/bundled fixtures into a disposable repository and
uses a temporary database and localhost API. It checks missing-key blocking,
local file/SQL report generation and restart persistence of reports, Trace,
sessions, memory and audit. External socket requests are blocked. Real provider
connectivity, live research quality, and Docker startup require separate acceptance
after deployment keys are configured.

- **Inspectable execution**: persist the task plan, run state, progress, tool
  inputs and outputs, errors, latency, and cost estimates in SQLite.
- **Read-only by default**: local files, SQL, web, source-control, academic,
  and MCP tools are registered explicitly and checked before they execute.
- **Human control**: a plan can pause for review, and guarded operations pause
  for confirmation instead of running silently.
- **Evidence-first reports**: citations, provenance, source basis, conflicts,
  and citation-validation metrics are available alongside the report.
- **Governed research inputs**: retrieval profiles enforce source-tier quotas,
  bounded discovery and fetch budgets, and auditable targeted refetches.
- **Local extraction pipeline**: HTML fallback extraction, integrity-checked
  fetch caching, page-level PDF evidence, and reference verification work
  through the same traceable tool boundary.
- **Works without remote services**: deterministic planning and local tools
  support an offline-friendly audit flow; remote search and LLM synthesis are
  optional enhancements.
- **One-command deployment**: FastAPI and Streamlit start together with Docker
  Compose and retain runtime data under the mounted `workspace/` directory.

## Demo

The shortest demonstration uses only local data. It creates a three-step plan,
reads an allowlisted document, runs a read-only SQL query, and produces a
traceable report.

```powershell
$body = @{
  task = "Audit the local research note and document metadata"
  report_type = "summary"
  source_mode = "mock"
  skill_name = "local_audit"
  require_plan_approval = $true
} | ConvertTo-Json

$created = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/tasks `
  -ContentType application/json -Body $body

Invoke-RestMethod -Method Get `
  -Uri "http://localhost:8000/api/tasks/$($created.run_id)/review"

$approval = @{ approved = $true; comment = "approved after review" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/tasks/$($created.run_id)/approve-plan" `
  -ContentType application/json -Body $approval

Invoke-RestMethod -Method Get `
  -Uri "http://localhost:8000/api/tasks/$($created.run_id)/trace"
```

The resulting run moves from `waiting_human_plan` to `completed`. Its trace
contains `memory_recall`, `plan_approval`, the executed local tools, and
`citation_validator`. Retrieve the report at
`GET /api/reports/{run_id}`.

For a real web-research demonstration, configure `TAVILY_API_KEY` and run:

```powershell
.\.venv\Scripts\python.exe scripts\demo_real_research.py --preset 1 --report-type detailed_report
```

This script performs search, fetch, evidence compression, and report creation.
Generated output remains local under `docs/examples/` and is intentionally not
published by default.

## Quick Start

Prerequisite: Docker Desktop or Docker Engine with Compose v2.

```powershell
git clone https://github.com/piao666/traceable-research-agent.git
Set-Location traceable-research-agent
Copy-Item .env.example .env
docker compose up --build -d
```

For the React frontend only, use `docker compose up --build -d api web`.
The `api` image installs only `requirements/api.txt`; it does not download
Streamlit, PyArrow, Pandas, NumPy, PyDeck or pytest. The optional `streamlit`
image adds `requirements/streamlit.txt`. The legacy `light` Docker target is
still available with both runtimes. `pip install -r requirements.txt` remains
the full local development setup, including `requirements/dev.txt`.

### Recovering from interrupted dependency downloads

Docker bootstraps pip **26.2.1** before installing application dependencies,
with 5 connection attempts, 10 incomplete-download recovery attempts and a
120-second socket timeout. BuildKit caches pip downloads outside the final
image; downloaded artifact hashes and TLS verification remain enabled.
These settings follow the [pip download options](https://pip.pypa.io/en/stable/cli/pip/)
and [Docker cache-mount guidance](https://docs.docker.com/build/cache/optimize/#use-cache-mounts).
Direct dependencies are pinned; this split is not a complete transitive lockfile.

After updating to the repair commit, rebuild only the failed API image from
the repository/worktree root. Run each step only if the preceding one succeeds:

```powershell
docker compose --progress plain build api
if ($LASTEXITCODE -ne 0) { throw "API build failed; stop and inspect the download error." }
docker compose up -d --no-build api web
if ($LASTEXITCODE -ne 0) { throw "Startup failed; inspect docker compose logs api." }
docker compose ps
```

This recovery command assumes the web image already built successfully in
the same Compose project. For a fresh checkout, use
`docker compose up --build -d api web`. Do not use `--no-cache`, clear all
Docker caches, disable hash verification or replace an expected hash with the
hash of a failed download. If it still fails, check Docker Desktop's proxy and
package-download connectivity; longer timeouts cannot repair a broken proxy.
Upgrading Windows-host pip does not upgrade pip inside the Docker image.

Docker applies schema migrations and seeds the local demo database only if it is
absent when the API container starts. Existing demo files are preserved, including
unknown/corrupt files that require manual inspection; they are never reset.
Set `DOCKER_INIT_DEMO_DATA=false` to disable seeding. Back up the stopped
deployment workspace before upgrading; see [release validation](RELEASE_VALIDATION.md).
Wait until the API is healthy, then open:

- Streamlit: <http://localhost:8501>
- React web (D01–D11): <http://localhost:5173>
- FastAPI documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

To inspect the service lifecycle:

```powershell
docker compose ps
docker compose logs --tail 100 api
docker compose logs --tail 100 streamlit
```

Runtime data, reports, evidence artifacts, and the SQLite database live under
`workspace/`, which Compose mounts into both services. Stop the application
without removing that data:

```powershell
docker compose down
```

For a local Windows virtual environment, the repository-root starter resolves
the project path from its own location and launches FastAPI plus Streamlit:

```powershell
.\start_traceable_demo.bat --check
.\start_traceable_demo.bat
```

MCP is not required by the core application. To also launch the optional MCP
Source Pack on port 9001, use `start_traceable_demo.bat --with-mcp`.
When a default port is unavailable, set `TRACEABLE_API_PORT`,
`TRACEABLE_STREAMLIT_PORT`, or `TRACEABLE_MCP_PORT` before running the script.

## Configuration

Copy `.env.example` to `.env`; it documents every available setting. `.env` is
local-only and must never be committed.

| Setting | Default | Purpose |
|---|---|---|
| `AUTH_ENABLED` | `false` | Enable local API-key authentication. |
| `DEMO_API_KEY` | empty | API key required when authentication is enabled. |
| `EXECUTION_MODE` | `planned` | Choose stable planned execution or `react`. |
| `OFFLINE_MODE` | `false` | Disable remote tool use for offline operation. |
| `REPORT_GENERATION_MODE` | `deterministic` | Use offline-safe reporting or configured LLM reporting. |
| `TAVILY_API_KEY` | empty | Enable real web search. |
| `QWEN_API_KEY` / `DEEPSEEK_API_KEY` | empty | Enable an optional configured LLM provider. |
| `FILE_READER_ALLOWED_ROOTS` | `workspace/docs` | Allowlisted roots for local file reads. |
| `CITATION_VALIDATION_LLM_ENABLED` | `false` | Enable optional second-pass LLM citation validation. |

Remote keys are optional. The local demo and deterministic report path do not
need them. When `AUTH_ENABLED=true`, send the configured key in the
`X-API-Key` header (or as a Bearer credential).

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service and database readiness. |
| `POST /api/tasks` | Create a planned research task. |
| `GET /api/tasks/{run_id}` | Read status, progress, cost, and citation metrics. |
| `POST /api/tasks/{run_id}/run` | Execute a created task. |
| `GET /api/tasks/{run_id}/review` | Read a plan waiting for approval. |
| `POST /api/tasks/{run_id}/approve-plan` | Approve, edit, or reject that plan. |
| `POST /api/tasks/{run_id}/confirm` | Resume or reject a guarded operation. |
| `GET /api/tasks/{run_id}/trace` | Read persisted tool traces. |
| `GET /api/tasks/{run_id}/evidence/v2` | Read provenance and citations. |
| `GET /api/reports/{run_id}` | Fetch the Markdown report. |
| `GET /api/tools` | List registered tool metadata. |
| `GET /api/skills` | List installed task skills. |
| `GET /api/improvement/stats` | Read final-run quality statistics for a real date window. |
| `GET /api/improvement/runs/{run_id}` | Read the five-dimensional final quality evaluation for one run. |
| `GET /api/improvement/state` | Inspect local routing-weight and Few-shot cold-start state. |

Plans and task status responses expose multi-skill composition and adaptive
Planned-to-ReAct metadata. During an adaptive quality gate or deep-research
round, realtime clients keep the run open and receive `report_ready` only when
the final report is stable.

The OpenAPI interface at `/docs` is the complete, versioned request and
response reference.

## Architecture

```mermaid
flowchart TD
    UI["Streamlit operator interface"] --> API["FastAPI API"]
    API --> Planner["Planner and plan review"]
    Planner --> Executor["Planned or ReAct executor"]
    Executor --> Registry["Validated tool registry"]
    Registry --> Tools["Read-only file, SQL, web, source-control, academic, MCP tools"]
    Executor --> Trace["Tool traces and run state"]
    Tools --> Trace
    Trace --> Evidence["Evidence, provenance, citations, conflicts"]
    Evidence --> Reporter["Markdown, Word, and PDF reports"]
    Reporter --> Storage["SQLite and workspace artifacts"]
```

```text
app/api/       FastAPI endpoints and response contracts
app/agent/     planning, execution, report generation, and guardrails
app/tools/     registered read-only tool implementations
app/trace/     run and tool-call persistence
app/evidence/  provenance, citation, and conflict reasoning
app/memory/    single-instance sessions and optional memory
app/skills/    reusable task definitions and validation
app/mcp/       optional read-only MCP integration
frontend/      Legacy Streamlit interface
web/           React, TypeScript, and Vite interface
migrations/    Alembic schema history
scripts/       migration, demo, smoke, and evaluation commands
workspace/     local databases, reports, artifacts, and skills
```

## Safety Model

- The executor can invoke only tools registered in the unified registry.
- `file_reader` resolves paths, blocks traversal and escaping symlinks, limits
  content length, and reads only configured roots.
- `sql_query` accepts one read-only `SELECT` or `WITH` statement and enforces a
  row limit.
- External and MCP integrations are read-only, time-bounded, and redact
  secrets from persisted trace data.
- Failed and rejected tool calls remain visible in run status and traces.
- Plan approval and high-risk tool confirmation are explicit state transitions,
  never hidden background actions.

## Quality Checks

The current registry exposes **12 read-only tools**. Earlier phase counts are not
acceptance of the R0–R7 repairs. The R7 offline run discovered 479 unittest entries:
467 passed and 12 failed to import pytest/Streamlit dependencies, with zero
blocked external attempts after fixture fixes. This is **not a full pytest pass**.
Frontend: 93 tests, typecheck, lint and build passed; isolated QA middleware:
52 checks passed. See [release validation](RELEASE_VALIDATION.md) for limits.

Run the same core checks locally:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app scripts frontend migrations tests
.\.venv\Scripts\python.exe scripts\run_offline_tests.py --runner pytest
.\.venv\Scripts\python.exe scripts\smoke_research_integrity.py
docker compose config --quiet
```

## Roadmap

- [x] Traceable planned and optional ReAct execution
- [x] Evidence provenance, citation validation, and human plan approval
- [x] Source-tier governance, cached extraction, PDF evidence, and academic verification
- [x] Docker deployment configuration and local runtime persistence implementation
- [ ] R0–R7 real Docker build/restart, Streamlit and browser acceptance
- [ ] Add a repository license before public redistribution
- [ ] Expand operational observability for long-running self-hosted instances

## Contributing

Issues and focused pull requests are welcome. Keep changes within the project
boundary, preserve read-only tool guarantees, add focused tests for behavior
changes, and do not commit `.env`, local databases, generated reports, or
other local runtime data. See [AGENTS.md](AGENTS.md) for engineering rules.

## License

This repository currently has no root-level `LICENSE` file. Until a license is
added, no permission to use, copy, modify, or redistribute the code is granted
by this README. Add an explicit license before treating the project as a public
open-source distribution.

## References

This is an independent implementation. External agent-system materials are
used only as read-only design references; no external project source is copied
into this repository.
