"""Read-only SQL query tool for the local demo SQLite database."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from app.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "workspace" / "demo.sqlite"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
DANGEROUS_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "TRUNCATE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "VACUUM",
}


def _failure(message: str, *, error_type: str, query: str | None = None) -> ToolResult:
    return ToolResult(
        success=False,
        error_message=message,
        metadata={
            "error_type": error_type,
            "readonly_check": False,
            "db_path": str(DEFAULT_DB_PATH),
            "query": query,
        },
    )


def _coerce_limit(value: Any) -> int:
    try:
        limit = int(value) if value is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _strip_sql_comments(query: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE).strip()


def _validate_readonly(query: str) -> ToolResult | None:
    normalized = _strip_sql_comments(query)
    if not normalized:
        return _failure("Missing SQL query.", error_type="invalid_args", query=query)
    if ";" in normalized.rstrip(";"):
        return _failure(
            "Multiple SQL statements are not allowed.",
            error_type="safety_rejected",
            query=query,
        )

    first = normalized.lstrip().split(None, 1)[0].upper()
    if first not in {"SELECT", "WITH"}:
        return _failure(
            "Only SELECT or WITH read-only queries are allowed.",
            error_type="safety_rejected",
            query=query,
        )

    tokens = set(re.findall(r"\b[A-Z_]+\b", normalized.upper()))
    blocked = sorted(tokens & DANGEROUS_KEYWORDS)
    if blocked:
        return _failure(
            f"SQL rejected because it contains dangerous keyword(s): {', '.join(blocked)}.",
            error_type="safety_rejected",
            query=query,
        )
    return None


def _query_with_limit(query: str, limit: int) -> str:
    clean = query.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", clean, flags=re.IGNORECASE):
        return clean
    return f"{clean} LIMIT {limit}"


def run_query(arguments: dict[str, Any]) -> ToolResult:
    """Run a read-only query against workspace/demo.sqlite."""

    query = str(arguments.get("query") or "").strip()
    limit = _coerce_limit(arguments.get("limit"))
    failure = _validate_readonly(query)
    if failure is not None:
        failure.metadata["limit"] = limit
        return failure

    if not DEFAULT_DB_PATH.exists():
        return ToolResult(
            success=False,
            error_message="Demo database does not exist. Run scripts/init_demo_db.py first.",
            metadata={
                "error_type": "db_not_found",
                "readonly_check": True,
                "limit": limit,
                "db_path": str(DEFAULT_DB_PATH),
            },
        )

    final_query = _query_with_limit(query, limit)
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
            "limit": limit,
            "db_path": str(DEFAULT_DB_PATH),
        },
    )
