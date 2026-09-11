"""Add research result governance records.

Revision ID: 0013_research_result_governance
Revises: 0012_research_scope_and_lineage
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_research_result_governance"
down_revision: str | None = "0012_research_scope_and_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scope_reasoning_runs",
        sa.Column("reasoning_run_id", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scope_id"], ["research_scopes.scope_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("reasoning_run_id"),
    )
    op.create_index(
        "ix_scope_reasoning_runs_scope_id",
        "scope_reasoning_runs",
        ["scope_id"],
    )
    op.create_index(
        "ix_scope_reasoning_runs_fingerprint",
        "scope_reasoning_runs",
        ["evidence_fingerprint"],
    )
    op.create_table(
        "scope_claim_groups",
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("reasoning_run_id", sa.String(length=64), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("representative_claim_text", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("time_scope", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reasoning_run_id"],
            ["scope_reasoning_runs.reasoning_run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id"),
    )
    op.create_index(
        "ix_scope_claim_groups_reasoning_run",
        "scope_claim_groups",
        ["reasoning_run_id"],
    )
    op.create_index(
        "ix_scope_claim_groups_normalized_key",
        "scope_claim_groups",
        ["normalized_key"],
    )
    op.create_table(
        "scope_claim_members",
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("origin_run_id", sa.String(), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["scope_claim_groups.group_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_run_id"], ["agent_runs.run_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["research_claims.claim_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("member_id"),
    )
    op.create_index(
        "ix_scope_claim_members_group_id",
        "scope_claim_members",
        ["group_id"],
    )
    op.create_index(
        "ix_scope_claim_members_origin_run_id",
        "scope_claim_members",
        ["origin_run_id"],
    )
    op.create_index(
        "ix_scope_claim_members_claim_id",
        "scope_claim_members",
        ["claim_id"],
    )
    op.create_table(
        "scope_claim_resolutions",
        sa.Column("resolution_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("support_quality", sa.Float(), nullable=False),
        sa.Column("refute_quality", sa.Float(), nullable=False),
        sa.Column("independent_support_count", sa.Integer(), nullable=False),
        sa.Column("independent_refute_count", sa.Integer(), nullable=False),
        sa.Column("rationale_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["scope_claim_groups.group_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("resolution_id"),
    )
    op.create_index(
        "ix_scope_claim_resolutions_group_id",
        "scope_claim_resolutions",
        ["group_id"],
    )
    op.create_index(
        "ix_scope_claim_resolutions_status",
        "scope_claim_resolutions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scope_claim_resolutions_status",
        table_name="scope_claim_resolutions",
    )
    op.drop_index(
        "ix_scope_claim_resolutions_group_id",
        table_name="scope_claim_resolutions",
    )
    op.drop_table("scope_claim_resolutions")
    op.drop_index("ix_scope_claim_members_claim_id", table_name="scope_claim_members")
    op.drop_index(
        "ix_scope_claim_members_origin_run_id",
        table_name="scope_claim_members",
    )
    op.drop_index("ix_scope_claim_members_group_id", table_name="scope_claim_members")
    op.drop_table("scope_claim_members")
    op.drop_index(
        "ix_scope_claim_groups_normalized_key",
        table_name="scope_claim_groups",
    )
    op.drop_index(
        "ix_scope_claim_groups_reasoning_run",
        table_name="scope_claim_groups",
    )
    op.drop_table("scope_claim_groups")
    op.drop_index(
        "ix_scope_reasoning_runs_fingerprint",
        table_name="scope_reasoning_runs",
    )
    op.drop_index(
        "ix_scope_reasoning_runs_scope_id",
        table_name="scope_reasoning_runs",
    )
    op.drop_table("scope_reasoning_runs")
