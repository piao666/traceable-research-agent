# Interview Notes

## 30 Second Introduction

Traceable Research Agent is a FastAPI backend for a task-oriented research
agent. It creates a deterministic plan, executes file, SQL, RAG, and read-only
GitHub-style tools through a registry, records every tool call into SQLite
traces, and generates a Markdown report with evidence and failure visibility.

## 3 Minute Introduction

The project focuses on Agent execution rather than only RAG QA. A user creates
a task, inspects the plan, manually runs the executor, checks trace rows, and
reads the generated report. The implementation includes safety controls for
file paths and SQL, a lightweight local RAG index, a read-only GitHub mock/API
adapter, minimal human confirmation, Docker packaging, and eval cases.

## 10 Minute Main Line

1. Show `POST /api/tasks` and explain that it only creates a pending run and
   `plan_json`.
2. Show `GET /api/tasks/{run_id}/plan` and explain deterministic planning.
3. Show `POST /api/tasks/{run_id}/run` and walk through Executor behavior.
4. Show `GET /api/tasks/{run_id}/trace` and explain success, failed, rejected.
5. Show `GET /api/reports/{run_id}` and explain evidence-backed reporting.
6. Demonstrate HITL by triggering `waiting_human` and confirming resume.
7. Show eval summary and Docker entrypoints.

## Core Highlights

- Clear separation of Planner, Executor, Tool Registry, Trace DB, and Reporter.
- Tool calls are observable through persisted traces.
- Safety failures are first-class results, not hidden exceptions.
- `POST /api/tasks` does not auto-run, which makes demos and audits controlled.
- MCP/GitHub capability is introduced as a read-only adapter to avoid write-risk.

## Q&A

Q: Why deterministic planning instead of LLM planning?

A: The project prioritizes a stable execution and trace backbone first. LLM
planning can be swapped in later once tool contracts, trace persistence, and
failure behavior are reliable.

Q: How is this different from a normal RAG QA project?

A: RAG QA usually retrieves context and answers. This project executes a
multi-step task through tools, persists evidence for each call, handles tool
failures visibly, and produces a report that can be audited.

Q: How do you prevent dangerous tool behavior?

A: The current MVP uses path whitelisting, SQL read-only checks, read-only
GitHub GET/mock behavior, and HITL for confirmation-required steps.

Q: What would you improve next?

A: Add production auth, async job execution, migration management, stronger SQL
parsing, real MCP server integration, and LLM planner/evaluator loops.

## Relationship With GPT Researcher

GPT Researcher was used as an architectural reference for research flow,
document loading, reporting, and MCP considerations. This repository is an
independent task-oriented Agent backend and does not copy GPT Researcher source
code.
