# Project Feature Matrix

| Area | Feature | Status | Evidence | Notes |
| --- | --- | --- | --- | --- |
| Backend | FastAPI backend | Done | `app/main.py`, `/health` | Stable HTTP API boundary. |
| Lifecycle | Task create/plan/run/trace/report | Done | `app/api/tasks.py`, `app/api/reports.py` | Create persists pending run and plan only. |
| Planning | LLM Planner | Done with documented limitation | `app/llm/`, `app/agent/planner.py` | Qwen default, DeepSeek optional; credentials are local only. |
| Planning | Deterministic fallback | Done | `app/agent/planner.py` | Invalid/unavailable LLM output falls back safely. |
| Execution | Planned executor | Done | `app/agent/executor.py` | Default stable sequential mode. |
| Execution | Optional ReAct executor | Done with documented limitation | `app/agent/react_executor.py` | Bounded loop; real provider remains optional. |
| Execution | Thought/Action/Observation trace | Done | `app/agent/react_schema.py`, trace API | Stores concise decision rationale, not raw provider output. |
| Tools | Tool Registry | Done | `app/tools/registry.py`, `app/tools/defaults.py` | Central allowlist, schema metadata, handler dispatch. |
| Tools | `file_reader` | Done | `app/tools/file_reader.py` | Restricts reads to allowed local docs. |
| Tools | `sql_query` | Done | `app/tools/sql_query.py` | Read-only execution and structured failures. |
| Safety | SQL parser safety | Done | `app/tools/sql_safety.py` | SQLGlot plus keyword guard; single SELECT/WITH only. |
| RAG | Deterministic/JSON retrieval | Done | `app/rag/embedding_backends.py`, `vector_backends.py` | Offline lightweight default. |
| RAG | SentenceTransformers/Chroma | Done with documented limitation | `scripts/smoke_real_rag.py` | Requires local model path; model artifacts are not committed. |
| RAG | BM25 retrieval | Done | `app/rag/bm25_backend.py` | Lightweight English/CJK tokenizer. |
| RAG | Hybrid RAG with RRF | Done | `app/rag/hybrid_search.py` | Dense and BM25 rank fusion with fallback metadata. |
| RAG | Chunk-size experiment | Done with documented limitation | `docs/rag_chunk_experiment.md` | Small fixed corpus; not a public benchmark. |
| GitHub | GitHub mock search | Done | `app/tools/mcp_github.py` | Offline default, no token required. |
| GitHub | Cache/retry/fallback | Done | `app/tools/github_cache.py` | TTL cache, bounded retry, stable mock fallback. |
| MCP | Read-only policy | Done with documented limitation | `app/mcp/readonly.py`, `docs/mcp_readonly_direction.md` | Adapter only, not a full MCP server. |
| Trace | Persistent run/tool trace | Done | `app/trace/models.py`, `app/trace/store.py` | SQLite persistence for runs and tool traces. |
| Reporting | Markdown report | Done | `app/agent/reporter.py` | Evidence, failures, trace summary, limitations. |
| Control | HITL confirmation | Done | `/api/tasks/{run_id}/confirm` | `waiting_human` cannot be bypassed by ReAct/async. |
| UI | Streamlit demo | Done with documented limitation | `frontend/streamlit_app.py` | Demo UI, not production frontend. |
| Security | Optional API-key auth | Done with documented limitation | `app/security/auth.py` | Demo-level key auth; disabled by default. |
| Context | Tenant/User request context | Done with documented limitation | `app/security/context.py` | Request-scoped only; not persisted. |
| Async | `run_async` | Done with documented limitation | FastAPI `BackgroundTasks` | Local process execution, not a distributed queue. |
| Database | Alembic initial migration | Done | `migrations/versions/0001_initial_trace_schema.py` | Coexists with demo `init_db`. |
| Evaluation | ReAct vs Planned comparison | Done with documented limitation | `docs/eval_react_vs_planned.md` | 18 deterministic cases; real LLM eval optional. |
| Quality | Smoke/eval suite | Done | `scripts/smoke_final_project.py`, `app/eval/` | Aggregates 15 smoke scripts plus eval. |
| Packaging | Docker lightweight mode | Done with documented limitation | `Dockerfile`, `docker-compose.yml` | Local Docker Desktop Engine was unavailable during final validation. |

## Status Summary

The research-agent demonstration path is complete. Remaining items are
productionization work, not blockers for the local GitHub/resume/interview
demo: durable distributed jobs, persisted tenant isolation, full MCP server,
GitHub write tools, production frontend, and a larger external benchmark.
