# Demo Script

## 5-minute Demo

1. Open Streamlit at `http://127.0.0.1:8501` and show the backend health card.
2. Select the full-tools task template and create a task. Explain that creation
   persists a pending run and plan but does not auto-execute tools.
3. Open Plan and point out `planner_source`, provider/model metadata, allowed
   tools, risk level, and confirmation policy.
4. Use the backend-configured planned or ReAct execution mode and run the task.
5. Show the trace table: step, tool, status, latency, input/output summaries.
6. In ReAct mode, expand Thought/Action/Observation entries and explain how a
   failed or empty observation influences the next action.
7. Show RAG metadata: retrieval mode, dense/BM25 candidate counts, RRF K,
   backend, index, and fallback state.
8. Open the generated Markdown report and connect its evidence to trace rows.
9. Close with the engineering boundaries: optional auth, async run, SQL
   read-only parser, HITL, and read-only GitHub fallback.

Suggested task:

```text
Read local docs, query database metrics, retrieve hybrid trace evidence,
search GitHub mock evidence, and generate a markdown report.
```

## 10-minute Demo

1. Run the 5-minute flow, then contrast planned and ReAct execution.
2. Explain lightweight RAG versus local SentenceTransformers/Chroma real RAG.
3. Explain hybrid retrieval: dense semantics + BM25 lexical matching + RRF.
4. Run a HITL task, show `waiting_human`, approve it, and resume completion.
5. Execute a rejected SQL example such as `DELETE FROM documents`; show the
   rejected trace and explain SQLGlot plus the secondary keyword guard.
6. Describe auth behavior: `/health` is public; protected APIs require
   `X-API-Key` or Bearer when `AUTH_ENABLED=true`.
7. Show `/run_async` returning immediately and explain status polling and the
   in-process `BackgroundTasks` limitation.
8. Show GitHub mock/cache/fallback metadata and the GET-only MCP policy.
9. Show `alembic upgrade head` and explain coexistence with demo `init_db`.
10. Open `docs/eval_react_vs_planned.md`: planned is shorter/faster; ReAct
    recovered 6/7 failure cases versus 1/7 for planned execution.
11. Finish with `python scripts/smoke_final_project.py` and the final feature
    matrix/checkpoint.

## Demo Talking Points

* This is not a simple RAG QA application. It models task planning, bounded
  execution, tool safety, trace persistence, HITL, and evidence reporting.
* Traceability is the product boundary: every success, failure, rejection,
  fallback, and confirmation can be inspected after execution.
* Planned and ReAct modes are deliberate complements. Planned is predictable;
  ReAct is useful when observations require adaptation.
* Safety remains in deterministic handlers: allowed tools, path restrictions,
  SQL parsing, GET-only GitHub policy, bounded loops, and HITL.
* The project includes engineering evidence, not just feature claims: smoke
  scripts, eval cases, migration smoke, retrieval experiments, and a measured
  ReAct/planned comparison.

## Demo Recovery Plan

* If no external LLM key is configured, use deterministic planning and planned
  execution or the scripted ReAct smoke.
* If the local embedding model is unavailable, use deterministic/JSON RAG.
* If GitHub/network access fails, use mock or fallback evidence.
* If Docker Desktop is unavailable, use the documented local Python path.
