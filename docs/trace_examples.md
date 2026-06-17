# Trace Examples

## Success Trace

```json
{
  "step_no": 1,
  "tool_name": "file_reader",
  "status": "success",
  "input_summary": "path=demo_research_note.md, max_chars=4000",
  "output_summary": "Read demo_research_note.md: 1200 chars, truncated=False",
  "error_message": null
}
```

This means the tool executed normally and produced evidence for the report.

## Rejected Trace

```json
{
  "step_no": 2,
  "tool_name": "sql_query",
  "status": "rejected",
  "input_summary": "query=DELETE FROM documents, limit=5",
  "output_summary": null,
  "error_message": "Only read-only SELECT/WITH queries are allowed."
}
```

`rejected` is used for safety policy blocks such as path traversal or
destructive SQL.

## Failed Trace

```json
{
  "step_no": 3,
  "tool_name": "rag_search",
  "status": "failed",
  "input_summary": "query=trace registry, top_k=3",
  "output_summary": null,
  "error_message": "RAG index not found, run scripts/build_rag_index.py first."
}
```

`failed` is used for operational or argument failures that are visible to the
user but not classified as safety rejections.

## Field Guide

- `tool_name`: tool selected by the plan or direct execution endpoint.
- `step_no`: deterministic plan step number.
- `input_summary`: compact argument summary for fast debugging.
- `output_summary`: compact success result summary.
- `error_message`: human-readable failure reason.
