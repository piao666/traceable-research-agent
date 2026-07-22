# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

Traceable Research Agent — turns research questions into auditable reports with a full chain of custody: task → plan → tool execution → trace → evidence → report. Every tool call is persisted to SQLite and linked through a Claim-level evidence graph so answers can be verified back to their original source fragments.

## Commands

### Setup (Windows, from project root)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\migrate_database.py
.\.venv\Scripts\python.exe scripts\init_demo_db.py
.\.venv\Scripts\python.exe scripts\build_rag_index.py
```

### Run Backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Run Streamlit UI

```powershell
.\.venv\Scripts\streamlit.exe run frontend/streamlit_app.py --server.port 8501
```

### One-Click Demo (Windows)

```powershell
.\start_traceable_demo.bat
```

Launches MCP Source Pack Bridge (port 9001), FastAPI (port 8000), and Streamlit (port 8501).

### Docker

```powershell
docker compose up -d --build           # Light mode (deterministic RAG)
docker compose up -d --no-build        # Skip rebuild
docker compose down                    # Stop, keep workspace data
```

For semantic RAG with local BGE model:
```powershell
$env:RAG_MODEL_HOST_PATH="E:/Models/bge-small-zh-v1.5"
docker compose -f docker-compose.yml -f docker-compose.real-rag.yml up -d --build
```

### Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v          # Unit tests
.\.venv\Scripts\python.exe -m pytest                                 # Pytest runner
python -m compileall -q app scripts frontend                         # Syntax check
```

### Smoke Checks

Individual smoke scripts live in `scripts/smoke_*.py`. Key ones:
- `smoke_e2e.py` — full task→plan→execute→trace→report loop
- `smoke_evidence_aggregation.py` — multi-source evidence grouping
- `smoke_mcp_source_pack_bridge.py` — MCP bridge tool registration
- `smoke_react_vs_planned_eval.py` — eval run comparing execution modes
- `smoke_realtime_trace.py` — SSE events and HITL flow

### Health Check

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 10
```

## Architecture

### Execution Pipeline

```
POST /api/tasks → Planner → Executor (planned/parallel/ReAct) → Tool Registry
    → Trace Store (SQLite) → Evidence Pipeline (V2) → Reporter → Markdown/Word/PDF
```

### Layer Map

| Layer | Location | Responsibility |
|-------|----------|----------------|
| API | `app/api/` | FastAPI routers: tasks, reports, tools, events (SSE) |
| Agent | `app/agent/` | Planner, executors (planned/parallel/ReAct), reporter, evidence aggregation, plan guardrails |
| Tools | `app/tools/` | Unified Tool Registry + implementations: file_reader, sql_query, rag_search, mcp_github_search, tavily_search |
| Trace | `app/trace/` | SQLAlchemy models (AgentRun, ToolTrace), persistence, SSE event emission |
| Evidence V2 | `app/evidence/` | Claim-level provenance graph: SourceDocument → Passage → Assertion → ResearchClaim → Citation → ReportClaim |
| RAG | `app/rag/` | Chunking, embeddings (deterministic/SentenceTransformers), BM25, Chroma/JSON vector backends, hybrid search |
| MCP | `app/mcp/` | MCP server (exposes tools externally), remote client, channel policy (readonly/interactive/write) |
| MCP Bridge | `app/mcp_bridge/` | HTTP JSON-RPC bridge for Firecrawl, Exa, Context7 source packs |
| LLM | `app/llm/` | Provider abstraction (Qwen/DeepSeek), planner client, schema validation |
| Security | `app/security/` | API key auth, tenant/user request context, recursive output redaction |
| Config | `app/config.py` | Single flat Pydantic Settings model from env vars; validated at startup |

### Key Design Decisions

- **Deterministic by default**: Planner and reporter run without LLM calls unless explicitly enabled (`LLM_PLANNER_ENABLED=false`, `REPORT_GENERATION_MODE=deterministic`). This prevents implicit API usage even when keys exist in the environment.
- **Three execution modes**: `planned` (sequential deterministic), `parallel` (independent safe steps run concurrently), `ReAct` (step-by-step with LLM decision loop, falls back to planned on failure).
- **Evidence V2 pipeline**: Tool outputs are SHA-256 hashed, gzip-compressed, and stored as immutable artifacts. SQLite stores only graph relationships, locators, hashes, and structured fields. Source reliability is scored on authority, traceability, timeliness, relevance, independence, and extraction completeness.
- **Citation integrity**: Reporter accepts only persistent Citation IDs from the evidence graph. Fabricated or missing citations trigger validation failures.
- **Safety boundaries**: File reader restricted to `workspace/docs` with HITL for outside paths. SQL only allows SELECT/WITH. All external tools are read-only. MCP readonly channel auto-registers only `read_only=true, side_effect_free=true, requires_confirmation=false` tools.

### State Machine

```
pending → running → completed
pending → running → failed
running → waiting_human → running → completed
running → waiting_human → failed
```

### Database

SQLite at `workspace/traceable_research_agent.sqlite`, managed via Alembic migrations (`scripts/migrate_database.py`). Core tables: `agent_runs` and `tool_traces`. Evidence V2 adds tables for provenance graph (SourceDocument, SourceSnapshot, EvidencePassage, EvidenceAssertion, ResearchClaim, ClaimEvidenceEdge, Citation, ReportClaim).

### Key Configuration Switches

- `EXECUTION_MODE=planned|react` — execution strategy
- `PARALLEL_EXECUTION_ENABLED=false` — enable parallel step execution
- `LLM_PLANNER_ENABLED=false` — use LLM for plan generation
- `REPORT_GENERATION_MODE=deterministic|llm` — report generation strategy
- `EVIDENCE_PIPELINE_VERSION=v2` — evidence pipeline version (v2 adds claim graph)
- `RAG_EMBEDDING_BACKEND=deterministic|sentence_transformers` — embedding engine
- `RAG_VECTOR_BACKEND=json|chroma` — vector storage
- `MCP_REMOTE_REGISTRY_ENABLED=false` — register remote MCP tools at startup

## Constraints from AGENTS.md

1. All work stays inside the project directory; do not add this project under `agent-service-toolkit`.
2. Tools are read-only by default. Risky operations must support dry-run and/or human confirmation.
3. Do not use concrete company/project names in code, docs, commit messages, examples, or demo data.
4. Never commit secrets, tokens, `.env`, private data, local databases, caches, virtual environments, or model artifacts.
5. Before every phase checkpoint: update `task.txt`, run available tests, verify API, update README if usage changed, verify no secrets staged, commit, and push.
6. All meaningful work must be recorded in repository-root `task.txt` before and after implementation.

## Test Structure

- `tests/test_contracts.py` — unit tests for SQL safety, RAG chunking, MCP JSON-RPC contracts, embedding backends
- `tests/test_p0_runtime.py` — P0 runtime validation tests
- `tests/test_p1_provenance.py` — P1 evidence provenance tests
- `tests/test_p2_reasoning.py` — P2 reliability and conflict reasoning tests
- `scripts/smoke_*.py` — integration smoke tests covering each subsystem
