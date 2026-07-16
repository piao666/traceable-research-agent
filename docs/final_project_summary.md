# Final Project Summary

## What This Project Is

Traceable Research Agent is a research-operations agent for competitive
intelligence, technical vendor evaluation, account research, and internal
experiment review. A request is turned into a persisted plan, executed through a
stable planned executor or an optional observation-driven ReAct loop, recorded
as auditable traces, and rendered as an evidence-based Markdown report.
Streamlit provides a demo layer over the same HTTP API.

## Why It Is Valuable

The project goes beyond ordinary RAG question answering because it turns
business research into a repeatable tool workflow:

* Product teams can compare competitors, pricing, docs, and changelogs.
* Engineering teams can evaluate APIs, frameworks, and integration risk.
* Sales or BD teams can generate account briefs from public sources.
* Operators can merge internal notes, SQL metrics, RAG evidence, and web pages.
* Reviewers can audit which claims came from which tool calls and sources.

## Core Technical Highlights

1. **Traceable Agent execution loop.** Run state, tool calls, status, latency,
   errors, observations, and reports form an inspectable execution history.
2. **Controllable planning and execution.** A structured LLM planner has a
   deterministic fallback; planned remains the stable default while ReAct is
   optional, bounded, and observation-driven.
3. **Real/Hybrid RAG and safe tools.** The project combines optional local
   SentenceTransformers/Chroma retrieval, BM25, RRF, SQLGlot read-only SQL,
   path-scoped file access, and GET-only GitHub behavior.
4. **Engineering-ready demo.** Streamlit, auth, request context, async run,
   Alembic, HITL, checkpoints, smoke aggregation, and eval make the system easy
   to demonstrate and discuss.

## Implementation Milestones

* **Day1-5:** FastAPI skeleton, task lifecycle, Tool Registry foundation.
* **Day6-9:** real tool handlers, trace persistence, report generation.
* **Day10-15:** safety failures, HITL, eval, repeated-run guards.
* **Day16-19:** integration hardening and engineering checkpoints.
* **Day21-B:** Qwen/DeepSeek LLM Planner chain with deterministic fallback.
* **Day23:** Streamlit frontend demo layer.
* **Day26-28:** RAG abstraction, SentenceTransformers/Chroma real RAG, UI polish.
* **Day29-B:** API-key auth, request context, async run stabilization.
* **Day30:** Alembic baseline and SQLGlot read-only validation.
* **Day31:** GitHub cache/retry/fallback and MCP read-only direction.
* **Day32-A:** optional bounded ReAct executor and decision traces.
* **Day33:** BM25/RRF Hybrid RAG and chunk-size experiment.
* **Day34:** 18-case ReAct versus planned quantitative evaluation.
* **Day35:** final documentation, smoke aggregation, checkpoint, feature freeze.

## Final Validation

* `python -m compileall app tests scripts frontend`
* `python scripts/smoke_final_project.py`
* Main eval: 27/27 passed, failed=0 before final packaging.
* Day34 comparison: completion 100% in both modes; ReAct recovery 6/7 versus
  planned 1/7; planned remained shorter and faster.
* Optional real RAG previously passed with local `bge-small-zh-v1.5` and Chroma.
* Runtime artifacts and credentials remain excluded from Git.

The Day35 checkpoint records the final run-specific verification numbers.

## Limitations and Future Work

* Streamlit is a demo UI, not a production web frontend.
* `BackgroundTasks` is not a durable distributed queue.
* Tenant/User context is not persisted or isolated at database level.
* MCP support is a read-only JSON-RPC server foundation, not a write-capable
  general MCP hub.
* Real RAG requires a separately managed local model.
* Default ReAct evaluation uses a deterministic fake policy for reproducibility.
* The retrieval and agent benchmarks are small engineering datasets.
* Docker Desktop Engine instability prevented acceptance of local Docker
  regression as passed.

Future production work should live on a separate branch and begin with durable
jobs, tenant persistence/isolation, observability, deployment, write-capable MCP
governance, and larger human/public benchmarks rather than adding more demo
features to the frozen core.
