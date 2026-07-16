"""Smoke-check the initial Alembic migration and SQL read-only parser."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config

from app.tools.sql_query import run_query


TEMP_DB_PATH = ROOT / "workspace" / "tmp" / "alembic_smoke.sqlite"
REQUIRED_FILES = (
    ROOT / "alembic.ini",
    ROOT / "migrations" / "env.py",
    ROOT / "migrations" / "versions" / "0001_initial_trace_schema.py",
    ROOT / "migrations" / "versions" / "0002_claim_provenance_schema.py",
    ROOT / "migrations" / "versions" / "0003_evidence_reasoning.py",
)

ALLOWED_QUERIES = (
    "SELECT id, title FROM documents",
    "select id, title from documents limit 2",
    "WITH recent AS (SELECT id, title FROM documents) SELECT * FROM recent",
)
REJECTED_QUERIES = (
    "DELETE FROM documents",
    "DROP TABLE documents",
    "INSERT INTO documents (title) VALUES ('x')",
    "UPDATE documents SET title='x'",
    "PRAGMA database_list",
    "ATTACH DATABASE 'x.db' AS x",
    "VACUUM",
    "SELECT 1; DROP TABLE documents",
    "SELECT * FROM documents; SELECT * FROM documents",
    "CREATE TABLE x(id int)",
)


def _migration_smoke() -> list[str]:
    missing = [str(path) for path in REQUIRED_FILES if not path.exists()]
    assert not missing, f"Missing Alembic files: {missing}"

    TEMP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DB_PATH.unlink(missing_ok=True)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{TEMP_DB_PATH.as_posix()}")
    try:
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)
        connection = sqlite3.connect(TEMP_DB_PATH)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
        required_tables = {
            "agent_runs",
            "tool_traces",
            "evidence_pipeline_runs",
            "source_documents",
            "source_snapshots",
            "evidence_passages",
            "evidence_assertions",
            "research_claims",
            "claim_evidence_edges",
            "report_claims",
            "citations",
            "evidence_reasoning_runs",
            "evidence_reliability_scores",
            "claim_resolutions",
        }
        tables = sorted(name for (name,) in rows if name in required_tables)
        assert set(tables) == required_tables, tables
        return tables
    finally:
        TEMP_DB_PATH.unlink(missing_ok=True)


def _parser_smoke() -> None:
    for query in ALLOWED_QUERIES:
        result = run_query({"query": query, "limit": 5})
        assert result.success, (query, result.error_message, result.metadata)
        assert result.metadata.get("parser") == "sqlglot", result.metadata
        assert result.metadata.get("read_only") is True, result.metadata

    for query in REJECTED_QUERIES:
        result = run_query({"query": query, "limit": 5})
        assert not result.success, query
        assert result.metadata.get("error_type") in {"safety_rejected", "invalid_sql"}, (
            query,
            result.metadata,
        )
        assert result.metadata.get("parser") == "sqlglot", result.metadata
        assert result.metadata.get("blocked_reason"), result.metadata


def main() -> None:
    tables = _migration_smoke()
    _parser_smoke()
    print(
        json.dumps(
            {
                "alembic": "ok",
                "migration_tables": tables,
                "sql_parser": "ok",
                "allowed_cases": len(ALLOWED_QUERIES),
                "rejected_cases": len(REJECTED_QUERIES),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
