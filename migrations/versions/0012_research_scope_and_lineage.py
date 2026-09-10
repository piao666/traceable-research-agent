"""Add explicit Deep Research scope, tree, and AgentRun lineage."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0012_research_scope_and_lineage"
down_revision = "0011_run_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("parent_run_id", sa.String(), nullable=True))
    op.add_column("agent_runs", sa.Column("root_run_id", sa.String(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("run_role", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("research_scope_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("engine_version", sa.String(length=32), nullable=True)
    )

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT run_id, plan_json FROM agent_runs")).fetchall()
    run_ids = {str(row[0]) for row in rows}
    parent_by_run: dict[str, str | None] = {}
    role_by_run: dict[str, str] = {}
    for raw_run_id, plan_json in rows:
        run_id = str(raw_run_id)
        parent_run_id: str | None = None
        run_role = "root"
        try:
            plan = json.loads(plan_json or "{}")
            candidate = plan.get("parent_run_id")
            if isinstance(candidate, str) and candidate in run_ids and candidate != run_id:
                parent_run_id = candidate
                run_role = (
                    "legacy_deepening_child"
                    if plan.get("version") == "deepening-v1"
                    else str(plan.get("run_role") or "research_branch")
                )
        except (TypeError, json.JSONDecodeError):
            pass
        parent_by_run[run_id] = parent_run_id
        role_by_run[run_id] = run_role

    def root_for(run_id: str) -> str:
        current = run_id
        visited = {run_id}
        while parent_by_run.get(current):
            parent = str(parent_by_run[current])
            if parent in visited:
                return run_id
            visited.add(parent)
            current = parent
        return current

    for run_id in run_ids:
        parent_run_id = parent_by_run[run_id]
        connection.execute(
            sa.text(
                "UPDATE agent_runs SET parent_run_id=:parent, root_run_id=:root, "
                "run_role=:role, engine_version='legacy' WHERE run_id=:run_id"
            ),
            {
                "parent": parent_run_id,
                "root": root_for(run_id),
                "role": role_by_run[run_id],
                "run_id": run_id,
            },
        )

    op.create_table(
        "research_scopes",
        sa.Column("scope_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "root_run_id",
            sa.String(),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("research_contract_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_scopes_root_run_id", "research_scopes", ["root_run_id"], unique=True)
    op.create_index("ix_research_scopes_status", "research_scopes", ["status"])

    with op.batch_alter_table("agent_runs") as batch:
        batch.alter_column("root_run_id", nullable=False)
        batch.alter_column("run_role", nullable=False)
        batch.alter_column("engine_version", nullable=False)
        batch.create_foreign_key(
            "fk_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"], ["run_id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_agent_runs_root_run_id", "agent_runs", ["root_run_id"], ["run_id"], ondelete="RESTRICT"
        )
    op.create_index("ix_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"])
    op.create_index("ix_agent_runs_root_run_id", "agent_runs", ["root_run_id"])
    op.create_index("ix_agent_runs_run_role", "agent_runs", ["run_role"])
    op.create_index("ix_agent_runs_research_scope_id", "agent_runs", ["research_scope_id"])
    op.create_index("ix_agent_runs_engine_version", "agent_runs", ["engine_version"])

    op.create_table(
        "research_nodes",
        sa.Column("node_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "scope_id",
            sa.String(length=64),
            sa.ForeignKey("research_scopes.scope_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_node_id",
            sa.String(length=64),
            sa.ForeignKey("research_nodes.node_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("agent_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("research_goal", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_nodes_scope_id", "research_nodes", ["scope_id"])
    op.create_index("ix_research_nodes_parent_node_id", "research_nodes", ["parent_node_id"])
    op.create_index("ix_research_nodes_run_id", "research_nodes", ["run_id"], unique=True)
    op.create_index("ix_research_nodes_status", "research_nodes", ["status"])


def downgrade() -> None:
    op.drop_table("research_nodes")
    for name in ("engine_version", "research_scope_id", "run_role", "root_run_id", "parent_run_id"):
        op.drop_index(f"ix_agent_runs_{name}", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("fk_agent_runs_root_run_id", type_="foreignkey")
        batch.drop_constraint("fk_agent_runs_parent_run_id", type_="foreignkey")
        batch.drop_column("engine_version")
        batch.drop_column("research_scope_id")
        batch.drop_column("run_role")
        batch.drop_column("root_run_id")
        batch.drop_column("parent_run_id")
    op.drop_table("research_scopes")
