# Phase 3 Day10-15 CheckPoint

## Scope

Phase 3 covers:

* Day10 deterministic JSON Planner
* Day11 Executor step loop
* Day12 deterministic Markdown Reporter
* Day13 end-to-end demo flow polishing
* Day14 exception handling visibility
* Day15 Human-in-the-Loop confirmation

## Completed Capabilities

### Planner

* Deterministic rule-based `plan_task`.
* Stores `plan_json` in `agent_runs`.
* Supports `allowed_tools` restriction, including empty allowed tool lists.
* Exposes `GET /api/tasks/{run_id}/plan`.
* Does not call tools during planning.

### Executor

* Manual execution through `POST /api/tasks/{run_id}/run`.
* Executes plan steps through Tool Registry.
* Writes `tool_traces` for real tool steps.
* Skips duplicate execution for completed runs.
* Handles failed/rejected tools without crashing.
* Supports `waiting_human` state.

### Reporter

* Generates deterministic Markdown reports under `workspace/reports`.
* Reads real report through `GET /api/reports/{run_id}`.
* Includes task, plan, evidence, observations, trace summary, and runtime limitations.
* Includes Human Confirmation section when applicable.
* Shows failed/rejected traces.

### Exception Handling

Covered:

* file missing
* path traversal
* SQL dangerous statement
* SQL runtime error
* RAG empty query
* RAG index missing
* unknown tool
* empty plan
* report before run
* repeated run
* confirm when not `waiting_human`

### HITL

* Planner marks `report_writer` high-risk when task asks for human approval.
* Executor stops at confirmation step with `waiting_human`.
* `POST /api/tasks/{run_id}/confirm` supports approved/rejected and resume.
* `approved=false` -> failed.
* `approved=true, resume=false` -> pending.
* `approved=true, resume=true` -> continues and completes.

## Smoke Test Summary

Base commands:

* `python -m pip install -r requirements.txt` succeeded.
* `python -m compileall app tests scripts` succeeded.
* `python scripts/init_demo_db.py` generated `workspace/demo.sqlite`.
* `python scripts/build_rag_index.py` generated 1 document and 3 chunks.
* `python scripts/smoke_planner.py` returned `planner=ok`.
* `python scripts/smoke_e2e.py` returned `e2e=ok`.
* `python scripts/smoke_exceptions.py` returned `exceptions=ok`.
* `python scripts/smoke_hitl.py` returned `hitl=ok`.

API checkpoint smoke:

* health: `status=ok`, `service=traceable-research-agent`, `phase=day15`
* normal run_id: `d19a06ce96ef48fab599ec6180f5c617`
* normal create status: `pending`
* trace count before run: 0
* plan version: `deterministic-v1`
* plan tools: `file_reader`, `sql_query`, `rag_search`, `report_writer`
* normal run status: `completed`
* normal trace count: 3
* normal trace status distribution: `success:3`
* normal report exists: `true`
* normal report content checks:
  * `Traceable Research Report`: true
  * `## Plan`: true
  * `Evidence And Observations`: true
  * `Trace Summary`: true
* repeated run behavior: `Run already completed; no tools executed.`
* repeated run trace count: before 3, after 3
* report-before-run exists: `false`
* report-before-run message: `Report has not been generated yet. Run POST /api/tasks/{run_id}/run first.`
* HITL run_id: `ee156a9f8c154ea4a32c89cf989bd60e`
* HITL report step risk_level: `high`
* HITL report step requires_confirmation: `true`
* HITL waiting result: `status=waiting_human`, `current_step=2`
* HITL waiting error: `Waiting for human confirmation before step 3: report_writer`
* HITL report before confirm exists: `false`
* HITL confirm result: `status=completed`, `resumed=true`
* HITL report after confirm exists: `true`
* HITL report contains Human Confirmation: `true`
* HITL trace count: 2
* confirm non-waiting rejection: HTTP 400 with `Current run is not waiting for human confirmation`
* Uvicorn stopped after smoke: yes

## Runtime Artifacts

These are runtime artifacts and must not be committed:

* `workspace/demo.sqlite`
* `workspace/traceable_research_agent.sqlite`
* `workspace/index/`
* `workspace/reports/`

Tracked workspace files remain limited to:

* `workspace/data/.gitkeep`
* `workspace/docs/demo_research_note.md`

`git status --ignored --short workspace` showed ignored runtime artifacts only:

```text
!! workspace/demo.sqlite
!! workspace/index/
!! workspace/reports/
!! workspace/traceable_research_agent.sqlite
```

`git ls-files workspace` output:

```text
workspace/data/.gitkeep
workspace/docs/demo_research_note.md
```

## Current Limitations

* No MCP/GitHub real integration yet.
* No Docker setup yet.
* No eval cases yet.
* No production auth.
* No async background queue.
* No LLM planner.
* `POST /api/tasks` intentionally does not auto-run.

## Next Phase Handoff

Next steps:

* Day16 read-only MCP/GitHub mock/API path.
* Day17 Docker Compose.
* Day18 eval cases and eval report.
* Day19 README, architecture docs, trace examples, bad cases, and interview packaging.
