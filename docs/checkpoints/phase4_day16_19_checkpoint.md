# Phase 4 Day16-19 CheckPoint

## Scope

- Day16 read-only `mcp_github_search`.
- Day17 Docker and Docker Compose.
- Day18 eval cases and eval runner.
- Day19 README, docs, and interview packaging.

## Completed Capabilities

- Read-only GitHub/MCP-style search adapter.
- Mock mode without token or network.
- Optional best-effort public GitHub API mode through read-only GET requests.
- Tool Registry wiring for `mcp_github_search`.
- Planner keyword path for GitHub, repo, issue, PR, and code search tasks.
- Executor trace writing for GitHub tool success and failure paths.
- Dockerfile and `docker-compose.yml`.
- Eval cases in `app/eval/cases.jsonl`.
- Eval runner in `app/eval/run_eval.py`.
- Architecture docs, API examples, trace examples, bad cases, and interview notes.

## Smoke Summary

Local command smoke:

- `python -m pip install -r requirements.txt`: succeeded.
- `python -m compileall app tests scripts`: succeeded.
- `python scripts/init_demo_db.py`: generated `workspace/demo.sqlite`.
- `python scripts/build_rag_index.py`: generated 1 document and 3 chunks.
- `python scripts/smoke_planner.py`: returned `planner=ok`, including GitHub planning path `mcp_github_search, report_writer`.
- `python scripts/smoke_e2e.py`: returned `e2e=ok`, run_id `4d20dcde8ded4e6f96e6aeb06ab5ae51`.
- `python scripts/smoke_exceptions.py`: returned `exceptions=ok`.
- `python scripts/smoke_hitl.py`: returned `hitl=ok`, run_id `1b4823bd6c574353a0cc7ffa9288e80e`.
- `python -m app.eval.run_eval`: total 11, passed 11, failed 0.

API smoke:

- `/health`: `status=ok`, `service=traceable-research-agent`, `phase=day19`.
- Tool catalog contained 5 tools.
- `mcp_github_search` metadata: enabled, risk_level `medium`, requires_confirmation `false`.
- Direct GitHub mock tool result:
  - success: `true`
  - summary: `mcp_github_search returned 3 mock results.`
  - read_only: `true`
  - mode: `mock`
  - result_count: 3
- GitHub task run_id: `2eefdf099cc847bca268c22fc7fdf0c9`.
- GitHub task plan tools: `mcp_github_search, report_writer`.
- GitHub task run status: `completed`.
- GitHub task trace status distribution: `success:1`.
- GitHub report exists: `true`.
- GitHub report mentions GitHub or `mcp_github_search`: `true`.
- Uvicorn was stopped after API smoke and no residual process was found.

Eval summary:

```json
{
  "total_cases": 11,
  "passed": 11,
  "failed": 0,
  "task_success_rate": 1.0,
  "trace_complete_rate": 1.0,
  "report_exists_count": 8,
  "safety_hit_count": 2,
  "failure_visible_count": 3
}
```

Docker verification:

- Docker version: `Docker version 29.5.2, build 79eb04c`.
- Docker Compose version: `Docker Compose version v5.1.4`.
- `docker compose build`: succeeded.
- `docker compose up -d`: succeeded.
- Container `/health`: `status=ok`, `phase=day19`.
- `docker compose down`: succeeded.

Git workspace tracking:

```text
workspace/data/.gitkeep
workspace/docs/demo_research_note.md
```

Ignored runtime artifacts observed:

- `workspace/demo.sqlite`
- `workspace/traceable_research_agent.sqlite`
- `workspace/index/`
- `workspace/reports/`
- `workspace/eval_outputs/`

## Runtime Artifacts

Do not commit:

- `workspace/*.sqlite`
- `workspace/*.db`
- `workspace/index/`
- `workspace/reports/`
- `workspace/eval_outputs/`
- `.env`
- logs

## Current Limitations

- `mcp_github_search` is read-only.
- Mock mode is default and is the stable eval path.
- Public GitHub API mode may be rate-limited.
- No production auth.
- No background job queue.
- No real LLM planner.
- No persistent migrations.
- At this checkpoint, MCP is represented by an adapter; Day38 later adds a
  bounded read-only MCP-compatible JSON-RPC server.

## Final Demo Flow

1. `python scripts/init_demo_db.py`
2. `python scripts/build_rag_index.py`
3. `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
4. Create task.
5. Inspect plan.
6. Run task manually.
7. Inspect trace.
8. Inspect report.
9. Run `python -m app.eval.run_eval`.

## Final Handoff

Project is ready for final manual acceptance and resume/interview packaging.
