import json
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_migration_0012_backfills_nested_legacy_lineage(tmp_path):
    root = Path(__file__).resolve().parents[2]
    engine = create_engine(f"sqlite:///{tmp_path / 'r12.sqlite'}")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "0011_run_budgets")
    now = datetime.now(timezone.utc)
    statement = text(
        "INSERT INTO agent_runs "
        "(run_id, task, report_type, source_mode, status, current_step, total_steps, "
        "plan_json, total_tool_calls, total_latency_ms, estimated_cost, created_at, updated_at) "
        "VALUES (:run_id, :task, 'summary', 'real', 'completed', 0, 0, :plan, 0, 0, 0, :now, :now)"
    )
    with engine.begin() as connection:
        connection.execute(statement, {"run_id": "root", "task": "root", "plan": "{}", "now": now})
        connection.execute(statement, {
            "run_id": "child", "task": "child",
            "plan": json.dumps({"version": "deepening-v1", "parent_run_id": "root"}), "now": now,
        })
        connection.execute(statement, {
            "run_id": "grandchild", "task": "grandchild",
            "plan": json.dumps({"version": "deepening-v1", "parent_run_id": "child"}), "now": now,
        })
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT run_id, parent_run_id, root_run_id, run_role, engine_version "
            "FROM agent_runs ORDER BY run_id"
        )).fetchall()
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0013_research_result_governance"
    assert {row[0]: row[2] for row in rows} == {
        "root": "root", "child": "root", "grandchild": "root"
    }
    assert next(row for row in rows if row[0] == "child")[3] == "legacy_deepening_child"
    assert {
        "research_scopes",
        "research_nodes",
        "scope_reasoning_runs",
        "scope_claim_groups",
        "scope_claim_members",
        "scope_claim_resolutions",
        "report_revisions",
        "report_claim_occurrences",
        "citation_occurrences",
    }.issubset(inspect(engine).get_table_names())
    engine.dispose()
