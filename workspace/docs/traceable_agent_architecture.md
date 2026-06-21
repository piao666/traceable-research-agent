# Traceable Agent Architecture

## System Goal

Traceable Research Agent is organized around an inspectable task lifecycle, not
around a single chat completion. A client creates a task through the FastAPI
API Layer. Creation writes an `agent_runs` row with pending status and stores a
structured plan, but it does not execute a tool. This boundary lets a user or
UI inspect the proposed work before side effects or expensive calls begin.

## Planning and Execution

The Planner can produce a deterministic plan or request a structured plan from
an LLM provider. Provider output is validated before persistence. Invalid JSON,
unknown tools, unavailable providers, and schema errors use a deterministic
fallback. The Planned Executor consumes the complete plan sequentially. It is
suited to predictable work because the tool order is known before execution.

The optional ReAct Executor uses a different control loop. It chooses one
action from the current task and observation history, executes the action, and
feeds the result into the next decision. Both executors use the same Tool
Registry. A tool must be registered, enabled, and present in `allowed_tools`.
This means dynamic decisions do not bypass deterministic handler safety.

## Tool and Evidence Flow

The Tool Registry owns tool descriptions, input schemas, risk metadata, and
handler dispatch. The registered research tools are `file_reader`,
`sql_query`, `rag_search`, `mcp_github_search`, and the Reporter-managed
`report_writer`. The registry returns structured results instead of allowing
handler exceptions to become unhandled API errors.

Every real call is written to the Trace Store. The persistence model separates
`agent_runs` from `tool_traces`. A trace records step number, tool, status,
latency, summarized input, summarized output, structured JSON, and an error
message when applicable. Success, failed, and safety-rejected states therefore
remain visible after the request completes.

## Reporting and Presentation

The Reporter reads the persisted plan, observations, and trace rows to produce
an evidence-based Markdown report. It includes execution mode, tool evidence,
failure details, trace identifiers, retrieval metadata, and limitations. The
report is not treated as a replacement for Trace; it is a human-readable view
whose claims can be checked against the stored execution history.

Streamlit communicates with FastAPI over HTTP. It does not read SQLite or call
Agent internals directly. The UI can create a task, inspect the plan, start a
run, render trace data, approve HITL work, and view the final report. This
separation keeps the backend usable by CLI, tests, and other clients.

## Audit Boundary

The complete pipeline is: API Layer to Planner, then Planned Executor or ReAct
Executor, then Tool Registry, then Trace Store, and finally Reporter. The key
architectural property appears across these boundaries: decision data and tool
evidence are persisted before presentation. That ordering makes the system
traceable, controllable, and auditable rather than merely capable of producing
an answer.
