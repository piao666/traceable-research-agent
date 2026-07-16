"""Upgrade the application database, including legacy unversioned demo files."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import engine


V1_TABLES = {"agent_runs", "tool_traces"}
V2_TABLES = {
    "evidence_pipeline_runs",
    "source_documents",
    "source_snapshots",
    "evidence_passages",
    "evidence_assertions",
    "research_claims",
    "claim_evidence_edges",
    "report_claims",
    "citations",
}
P2_TABLES = {
    "evidence_reasoning_runs",
    "evidence_reliability_scores",
    "claim_resolutions",
}


def bootstrap_revision_for_tables(
    table_names: set[str],
    current_revision: str | None = None,
) -> str | None:
    """Return the revision to stamp for a legacy database, or None for a fresh DB."""
    if not (table_names & V1_TABLES):
        if current_revision is not None:
            raise RuntimeError("Alembic revision exists but the V1 trace schema is missing")
        return None
    if not V1_TABLES.issubset(table_names):
        raise RuntimeError("Legacy database has a partial V1 trace schema")
    present_v2 = table_names & V2_TABLES
    if not present_v2:
        schema_revision = "0001_initial_trace_schema"
        return _required_stamp(current_revision, schema_revision)
    if not V2_TABLES.issubset(table_names):
        missing = ", ".join(sorted(V2_TABLES - present_v2))
        raise RuntimeError(f"Legacy database has a partial V2 evidence schema; missing: {missing}")
    present_p2 = table_names & P2_TABLES
    if not present_p2:
        schema_revision = "0002_claim_provenance_schema"
        return _required_stamp(current_revision, schema_revision)
    if P2_TABLES.issubset(table_names):
        return _required_stamp(current_revision, "0003_evidence_reasoning")
    missing = ", ".join(sorted(P2_TABLES - present_p2))
    raise RuntimeError(f"Legacy database has a partial P2 reasoning schema; missing: {missing}")


def _required_stamp(current_revision: str | None, schema_revision: str) -> str | None:
    revisions = {
        "0001_initial_trace_schema": 1,
        "0002_claim_provenance_schema": 2,
        "0003_evidence_reasoning": 3,
    }
    if current_revision is None:
        return schema_revision
    if current_revision not in revisions:
        raise RuntimeError(f"Unsupported Alembic revision: {current_revision}")
    if revisions[current_revision] == revisions[schema_revision]:
        return None
    if revisions[current_revision] < revisions[schema_revision]:
        return schema_revision
    raise RuntimeError(
        f"Alembic revision {current_revision} is ahead of detected schema {schema_revision}"
    )


def migrate_database() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    tables = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()
    bootstrap_revision = bootstrap_revision_for_tables(tables, current_revision)
    if bootstrap_revision is not None:
        print(
            f"[database-migration] stamping legacy schema at {bootstrap_revision}",
            flush=True,
        )
        command.stamp(config, bootstrap_revision)
    command.upgrade(config, "head")
    print("[database-migration] database is at Alembic head", flush=True)


def main() -> None:
    migrate_database()


if __name__ == "__main__":
    main()
