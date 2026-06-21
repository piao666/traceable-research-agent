# Streamlit Demo Notes

## UI Boundary

The Streamlit UI is a demo client for the FastAPI API. It does not import the
executor, read SQLite, or access `.env` directly. The sidebar configures the
backend URL, optional masked API Key, Tenant ID, User ID, async preference, and
an Execution Mode display/control appropriate to the backend configuration.

## Demonstration Flow

The Health panel calls `/health`. Create Task sends a task, report type, source
mode, and allowed tools. The Plan viewer shows the persisted plan plus
`planner_source`, provider, and model metadata. Creating a task does not run it;
the Run control calls the synchronous or asynchronous endpoint explicitly.

The Trace Viewer shows tool, step, status, latency, input/output summaries, and
the complete JSON payload. A ReAct Trace section expands Thought, Action, and
Observation values. RAG metadata can show embedding/vector backend,
retrieval_mode, dense and BM25 hit counts, RRF K, collection, dimension, and
fallback status.

The Report viewer requests the generated Markdown report from the report API.
It displays evidence and limitations without exposing an API key. The password
field is session-only and is sent as a request header.

## HITL and Async Behavior

A confirmation-required report action changes status to `waiting_human`. The
UI displays the pending tool and calls the confirm endpoint. Only approval and
resume can complete that action; ReAct cannot bypass HITL.

With async enabled, the UI calls `/run_async`, then refreshes status, trace, and
report endpoints. FastAPI BackgroundTasks is suitable for this local demo but
is not a distributed durable queue. The UI presents that limitation rather
than implying production job delivery guarantees.

The common demo sequence crosses several semantic boundaries: create the task,
inspect the plan, run, inspect failed/success traces, open ReAct decisions,
inspect RAG metadata, approve HITL when required, and verify the report against
trace evidence.
