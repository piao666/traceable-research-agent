# ReAct Execution Notes

## Decision Contract

The ReAct executor implements a bounded Thought / Action / Observation loop.
Thought is a short decision rationale rather than an unrestricted chain of
thought. Action is either an enabled tool name from `allowed_tools` or the
internal `finish` action. Args is a JSON object that is still validated by the
selected tool. `finish_reason` explains completion or a limitation.

The LLM receives the task, run identifier, available tool descriptions, safety
rules, and a compact `observation_history`. The history contains step number,
action, success, observation summary, error message, and selected tool result
metadata. Raw provider responses and credentials are not stored in the history.

## Observation-driven Recovery

After a tool result, the executor converts the result into an Observation. A
missing file records a stable error. SQL safety rejection records the blocked
reason. RAG can record no hits. GitHub can record that public API evidence was
replaced by a labeled read-only fallback. These facts become input to the next
decision, so failure recovery can choose another tool or finish transparently.

For example, a failed `file_reader` action may be followed by `rag_search`. A
rejected write query may be followed by a safe SELECT. A no-hit retrieval can
lead to a different evidence source. Recovery is not defined as hiding an
error; the failed trace remains visible even when the final run completes.

## Loop Guards

`max_steps` is the hard upper bound for decisions in one run.
`same_tool_max_calls` bounds repeated calls to one tool. The count is checked
before another execution is dispatched. When either guard is reached, the run
can finish with limitation and generate a report that states why more work was
not attempted.

The phrase finish with limitation is important: it distinguishes a controlled
partial answer from a silent success. A limitation report may contain useful
evidence while making the missing evidence or loop guard explicit. This is
preferable to an infinite retry loop or an API timeout.

## Invalid Decisions and HITL

Strict parsing accepts plain JSON, fenced JSON, or the first valid object in a
response. Missing optional fields are normalized, but unknown tools,
non-object args, and disallowed actions are rejected. Depending on settings,
an invalid first decision can fall back to the persisted planned executor. If
fallback is not safe or useful, the run ends with a structured limitation.

ReAct cannot bypass HITL. When the planned risk policy marks report writing as
confirmation-required, selecting `report_writer` moves the run to
`waiting_human`. The pending decision is persisted. Only an approved confirm
request allows the executor to resume that action.

## Trace Semantics

Each ReAct trace contains the short Thought, selected Action, argument summary,
Observation summary, status, tool call count, provider/model identifiers, and
fallback metadata. Together with observation_history, this provides a concise
record of why execution adapted without storing long hidden reasoning.
