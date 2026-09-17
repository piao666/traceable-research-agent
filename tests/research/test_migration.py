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
        assert revision == "0018_pear_contract_entities"
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
        "report_claim_scope_group_links",
        "research_plan_revisions",
        "research_questions",
        "evidence_requirements",
        "requirement_claim_links",
        "source_discovery_links",
        "research_operations",
        "coverage_snapshots",
        "evidence_gaps",
        "node_execution_results",
    }.issubset(inspect(engine).get_table_names())
    assert "provider_attempts" in {
        column["name"] for column in inspect(engine).get_columns("run_budgets")
    }
    engine.dispose()


def test_migration_0013_nulls_dirty_scope_ids_and_adds_foreign_key(tmp_path):
    root = Path(__file__).resolve().parents[2]
    engine = create_engine(f"sqlite:///{tmp_path / 'r12-scope-fk.sqlite'}")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "0011_run_budgets")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(run_id, task, report_type, source_mode, status, current_step, total_steps, "
                "plan_json, total_tool_calls, total_latency_ms, estimated_cost, created_at, updated_at) "
                "VALUES ('root', 'root', 'summary', 'real', 'completed', 0, 0, '{}', "
                "0, 0, 0, :now, :now)"
            ),
            {"now": now},
        )
    command.upgrade(config, "0012_research_scope_and_lineage")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE agent_runs SET research_scope_id='missing-scope' "
                "WHERE run_id=(SELECT run_id FROM agent_runs LIMIT 1)"
            )
        )
    command.upgrade(config, "head")

    foreign_keys = inspect(engine).get_foreign_keys("agent_runs")
    assert any(
        item["constrained_columns"] == ["research_scope_id"]
        and item["referred_table"] == "research_scopes"
        for item in foreign_keys
    )
    with engine.connect() as connection:
        dirty_count = connection.scalar(text(
            "SELECT COUNT(*) FROM agent_runs WHERE research_scope_id='missing-scope'"
        ))
    assert dirty_count == 0
    command.downgrade(config, "0012_research_scope_and_lineage")
    engine.dispose()


def test_migration_0015_upgrades_existing_report_data_and_is_idempotent(tmp_path):
    root = Path(__file__).resolve().parents[2]
    engine = create_engine(f"sqlite:///{tmp_path / 'r15-lineage.sqlite'}")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "0014_budget_provider_attempts")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO agent_runs (run_id, task, report_type, source_mode, status, current_step, total_steps, "
            "plan_json, total_tool_calls, total_latency_ms, estimated_cost, created_at, updated_at, root_run_id, run_role, engine_version) "
            "VALUES ('root', 'root', 'summary', 'real', 'completed', 0, 0, '{}', 0, 0, 0, :now, :now, 'root', 'root', 'v2')"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO research_scopes (scope_id, root_run_id, engine_version, status, research_contract_json, created_at, updated_at) "
            "VALUES ('scope', 'root', 'v2', 'completed', '{}', :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO scope_reasoning_runs (reasoning_run_id, scope_id, policy_version, policy_hash, evidence_fingerprint, engine_version, status, created_at, updated_at) "
            "VALUES ('reason', 'scope', 'test', 'policy', 'evidence', 'v1', 'complete', :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO scope_claim_groups (group_id, reasoning_run_id, normalized_key, representative_claim_text, unit, time_scope, created_at) "
            "VALUES ('group', 'reason', 'claim|unit:<none>', 'Claim.', NULL, NULL, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO report_revisions (report_revision_id, root_run_id, scope_id, content_hash, final_answer_hash, report_path, status, created_at) "
            "VALUES ('revision', 'root', 'scope', 'content', 'answer', 'report.md', 'complete', :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO report_claim_occurrences (claim_occurrence_id, report_revision_id, section, claim_text, sentence_start, sentence_end, normalized_claim_text, created_at) "
            "VALUES ('occurrence', 'revision', '3. 最终回答', 'Claim.', 0, 6, 'claim.', :now)"
        ), {"now": now})
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert "report_claim_scope_group_links" in inspector.get_table_names()
    assert {
        "ix_report_claim_scope_group_links_claim_occurrence",
        "ix_report_claim_scope_group_links_scope_group",
    }.issubset({item["name"] for item in inspector.get_indexes("report_claim_scope_group_links")})
    assert {
        (tuple(item["constrained_columns"]), item["referred_table"], item["options"].get("ondelete"))
        for item in inspector.get_foreign_keys("report_claim_scope_group_links")
    } == {
        (("claim_occurrence_id",), "report_claim_occurrences", "CASCADE"),
        (("scope_group_id",), "scope_claim_groups", "CASCADE"),
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM report_revisions")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM report_claim_occurrences")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0018_pear_contract_entities"
    engine.dispose()
