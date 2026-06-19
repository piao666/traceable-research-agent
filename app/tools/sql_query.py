"""Read-only SQL query tool for the local demo SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.tools.base import ToolResult
from app.tools.sql_safety import validate_read_only_sql


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "workspace" / "demo.sqlite"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _failure(
    message: str,
    *,
    error_type: str,
    query: str | None = None,
    parser_metadata: dict[str, Any] | None = None,
) -> ToolResult:
    metadata = {
        "error_type": error_type,
        "readonly_check": False,
        "db_path": str(DEFAULT_DB_PATH),
        "query": query,
    }
    metadata.update(parser_metadata or {})
    return ToolResult(
        success=False,
        error_message=message,
        metadata=metadata,
    )


def _coerce_limit(value: Any) -> int:
    try:
        limit = int(value) if value is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _query_with_limit(query: str, limit: int) -> str:
    clean = query.strip().rstrip(";")
    if "LIMIT" in clean.upper().split():
        return clean
    return f"{clean} LIMIT {limit}"


def run_query(arguments: dict[str, Any]) -> ToolResult:
    """Run a read-only query against workspace/demo.sqlite."""

    query = str(arguments.get("query") or "").strip()
    limit = _coerce_limit(arguments.get("limit"))
    is_read_only, blocked_reason, parser_metadata = validate_read_only_sql(query)
    if not is_read_only:
        error_type = parser_metadata.get("error_type") or "safety_rejected"
        message = (
            "SQL query is invalid and could not be parsed."
            if error_type == "invalid_sql"
            else f"SQL query was rejected by read-only safety validation: {blocked_reason}."
        )
        failure = _failure(
            message,
            error_type=error_type,
            query=query,
            parser_metadata=parser_metadata,
        )
        failure.metadata["limit"] = limit
        return failure

    if not DEFAULT_DB_PATH.exists():
        return ToolResult(
            success=False,
            error_message="Demo database does not exist. Run scripts/init_demo_db.py first.",
            metadata={
                "error_type": "db_not_found",
                "readonly_check": True,
                "parser": parser_metadata["parser"],
                "statement_type": parser_metadata["statement_type"],
                "read_only": True,
                "blocked_reason": None,
                "limit": limit,
                "db_path": str(DEFAULT_DB_PATH),
            },
        )

    final_query = _query_with_limit(parser_metadata["normalized_sql"], limit)
    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(final_query).fetchmany(limit)
    except sqlite3.Error as exc:
        return ToolResult(
            success=False,
            error_message=f"SQL execution failed: {exc}",
            metadata={
                "error_type": "sql_error",
                "readonly_check": True,
                "parser": parser_metadata["parser"],
                "statement_type": parser_metadata["statement_type"],
                "read_only": True,
                "blocked_reason": None,
                "limit": limit,
                "db_path": str(DEFAULT_DB_PATH),
                "query": final_query,
            },
        )

    row_dicts = [dict(row) for row in rows]
    columns = list(row_dicts[0].keys()) if row_dicts else []
    return ToolResult(
        success=True,
        output={
            "columns": columns,
            "rows": row_dicts,
            "row_count": len(row_dicts),
            "query": final_query,
        },
        output_summary=f"Returned {len(row_dicts)} row(s) with columns: {', '.join(columns) or '<none>'}.",
        metadata={
            "error_type": None,
            "readonly_check": True,
            "parser": parser_metadata["parser"],
            "statement_type": parser_metadata["statement_type"],
            "read_only": True,
            "blocked_reason": None,
            "limit": limit,
            "db_path": str(DEFAULT_DB_PATH),
        },
    )
