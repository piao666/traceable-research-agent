"""Parser-backed read-only validation for SQL tool input."""

from __future__ import annotations

import re
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError


DANGEROUS_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "TRUNCATE",
    "MERGE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "VACUUM",
}
DANGEROUS_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Merge,
    exp.Into,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Command,
)


def _metadata(**values: Any) -> dict[str, Any]:
    return {
        "parser": "sqlglot",
        "statement_type": values.get("statement_type"),
        "read_only": values.get("read_only", False),
        "blocked_reason": values.get("blocked_reason"),
        "error_type": values.get("error_type"),
        "normalized_sql": values.get("normalized_sql"),
    }


def _strip_comments(query: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE).strip()


def _strip_quoted_content(query: str) -> str:
    pattern = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^\]]*\]"
    unquoted = re.sub(pattern, " ", _strip_comments(query))
    return re.sub(r"\bREPLACE\s*(?=\()", " ", unquoted, flags=re.IGNORECASE)


def validate_read_only_sql(query: str) -> tuple[bool, str | None, dict[str, Any]]:
    """Validate one SQLite SELECT/WITH statement and return audit metadata."""

    if not isinstance(query, str) or not query.strip():
        reason = "empty_query"
        return False, reason, _metadata(blocked_reason=reason, error_type="invalid_sql")

    try:
        statements = [
            statement
            for statement in parse(query, read="sqlite")
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
    except (ParseError, ValueError):
        reason = "parse_error"
        return False, reason, _metadata(blocked_reason=reason, error_type="invalid_sql")

    if len(statements) != 1:
        reason = "multiple_statements"
        return False, reason, _metadata(blocked_reason=reason, error_type="safety_rejected")

    statement = statements[0]
    statement_type = statement.key.upper()
    normalized_sql = statement.sql(dialect="sqlite")
    dangerous_node = next(statement.find_all(DANGEROUS_EXPRESSIONS), None)
    if dangerous_node is not None:
        reason = f"dangerous_statement:{dangerous_node.key.upper()}"
        return False, reason, _metadata(
            statement_type=statement_type,
            blocked_reason=reason,
            error_type="safety_rejected",
            normalized_sql=normalized_sql,
        )

    leading = _strip_comments(query).split(None, 1)[0].upper()
    if leading not in {"SELECT", "WITH"} or not isinstance(statement, exp.Query):
        reason = "statement_not_select"
        return False, reason, _metadata(
            statement_type=statement_type,
            blocked_reason=reason,
            error_type="safety_rejected",
            normalized_sql=normalized_sql,
        )

    tokens = set(re.findall(r"\b[A-Z_]+\b", _strip_quoted_content(query).upper()))
    blocked = sorted(tokens & DANGEROUS_KEYWORDS)
    if blocked:
        reason = f"dangerous_keyword:{','.join(blocked)}"
        return False, reason, _metadata(
            statement_type=statement_type,
            blocked_reason=reason,
            error_type="safety_rejected",
            normalized_sql=normalized_sql,
        )

    return True, None, _metadata(
        statement_type=statement_type,
        read_only=True,
        normalized_sql=normalized_sql,
    )
