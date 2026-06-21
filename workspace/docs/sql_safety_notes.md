# SQL Safety Notes

## Read-only Contract

The `sql_query` tool is designed for evidence retrieval from the demo SQLite
database, not database mutation. SQLGlot parses the input into an AST. Exactly
one statement is allowed, and its final operation must be a read-only SELECT or
WITH query. Whitespace, comments, and letter case do not change this contract.

## Rejected Operations

INSERT, UPDATE, DELETE, MERGE, REPLACE, CREATE, ALTER, DROP, and TRUNCATE are
DDL or DML operations and are rejected. SQLite control operations such as
PRAGMA, ATTACH, DETACH, and VACUUM are also rejected. A conservative keyword
guard remains after AST validation as a second defense.

Multi-statement input is never accepted. `SELECT 1; DROP TABLE documents` and
`SELECT * FROM documents; SELECT * FROM documents` are rejected even if their
first statement is read-only. Comments or extra semicolons cannot be used to
hide another statement.

## Failure Visibility

Parser failures return `invalid_sql`. Parsed but unsafe statements return
`safety_rejected` with a blocked reason. The API returns a structured tool
result instead of raising an unhandled exception. A safety-rejected query is
written to `tool_traces` with rejected status, input summary, and error
message, so a report can explain that the requested operation was intentionally
not executed.

The parser does not grant broader file or database permissions. Query limits,
the configured demo database, tool registration, and `allowed_tools` remain
independent controls. ReAct receives the rejection as an observation and may
choose a safe SELECT, but it cannot override the SQL parser.
