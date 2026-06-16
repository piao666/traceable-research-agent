# Traceable Research Agent Demo Note

## Project Goal

Traceable Research Agent is a task-oriented research backend that executes
controlled tool calls and records every step as structured evidence.

## Tool Registry

The Tool Registry exposes a stable catalog of available tools. Each tool has a
name, description, input schema, output schema, risk level, timeout, and enabled
state. The registry keeps tool execution separate from API routing.

## Trace Persistence

Every tool call should create a `tool_traces` row. Successful calls, failed
calls, and safety rejections all need visible trace records so an interviewer
can inspect what happened during a run.

## File, SQL, And RAG Tools

The first real tools are `file_reader`, `sql_query`, and a local RAG foundation.
File access is restricted to `workspace/docs`. SQL access is read-only and uses
a demo SQLite database. RAG starts with local documents, simple chunks, and
deterministic lightweight embeddings.

## Safety Rules

Tools are read-only by default. File paths are resolved before reading. SQL
queries must start with `SELECT` or `WITH`, and destructive keywords are
rejected. Generated databases and indexes are runtime artifacts and are not
committed.
