# Bad Cases

## SQL DELETE Rejected

Input: `DELETE FROM documents`

System behavior: `sql_query` rejects the statement before execution because it
is not a SELECT/WITH read-only query.

Trace location: a `tool_traces` row with `status=rejected`,
`tool_name=sql_query`, and an error message explaining the read-only policy.

Future improvement: add a SQL parser for deeper validation instead of keyword
checks only.

## Path Traversal Rejected

Input: `../task.txt`

System behavior: `file_reader` resolves the path and rejects it because it is
outside `workspace/docs`.

Trace location: a `tool_traces` row with `status=rejected`,
`tool_name=file_reader`, and `metadata.error_type=safety_rejected`.

Future improvement: add richer audit metadata for symlink and filesystem
policy decisions.

## RAG Index Missing

Input: `rag_search` before running `scripts/build_rag_index.py`.

System behavior: the tool returns a stable failed result with a clear message.

Trace location: a `tool_traces` row with `status=failed`,
`tool_name=rag_search`, and `error_type=index_missing`.

Future improvement: add an API endpoint to rebuild or refresh the local index.

## Unknown Tool

Input: a plan step with `tool_name=unknown_tool`.

System behavior: the Executor writes a failed trace and continues to generate a
limitation-aware report instead of returning HTTP 500.

Trace location: a `tool_traces` row with `status=failed` and tool name
`unknown_tool`.

Future improvement: validate plan JSON against registered tools before run.

## Confirm On Non-Waiting Run

Input: `POST /api/tasks/{run_id}/confirm` for a completed run.

System behavior: API returns HTTP 400 and does not execute anything.

Trace location: no new trace is written because the confirm request is rejected
before execution.

Future improvement: add a confirmation audit table for production systems.
