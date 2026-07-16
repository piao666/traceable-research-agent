"""Add reliability scoring and claim conflict resolution audit tables.

Revision ID: 0003_evidence_reasoning
Revises: 0002_claim_provenance_schema
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_evidence_reasoning"
down_revision: str | None = "0002_claim_provenance_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_reasoning_runs",
        sa.Column("reasoning_run_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("reasoning_run_id"),
    )
    op.create_index(
        "ix_evidence_reasoning_runs_run_id",
        "evidence_reasoning_runs",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_reasoning_runs_policy_version",
        "evidence_reasoning_runs",
        ["policy_version"],
        unique=False,
    )
    op.create_table(
        "evidence_reliability_scores",
        sa.Column("score_id", sa.String(length=64), nullable=False),
        sa.Column("reasoning_run_id", sa.String(length=64), nullable=False),
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("source_class", sa.String(length=64), nullable=False),
        sa.Column("source_cluster_id", sa.String(length=64), nullable=False),
        sa.Column("authority", sa.Float(), nullable=False),
        sa.Column("traceability", sa.Float(), nullable=False),
        sa.Column("freshness", sa.Float(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("independence", sa.Float(), nullable=False),
        sa.Column("extraction_completeness", sa.Float(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("rationale_json", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reasoning_run_id"],
            ["evidence_reasoning_runs.reasoning_run_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["edge_id"], ["claim_evidence_edges.edge_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("score_id"),
    )
    op.create_index(
        "ix_evidence_reliability_scores_reasoning_run",
        "evidence_reliability_scores",
        ["reasoning_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_reliability_scores_edge_id",
        "evidence_reliability_scores",
        ["edge_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_reliability_scores_cluster",
        "evidence_reliability_scores",
        ["source_cluster_id"],
        unique=False,
    )
    op.create_table(
        "claim_resolutions",
        sa.Column("resolution_id", sa.String(length=64), nullable=False),
        sa.Column("reasoning_run_id", sa.String(length=64), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("support_quality", sa.Float(), nullable=False),
        sa.Column("refute_quality", sa.Float(), nullable=False),
        sa.Column("independent_support_count", sa.Integer(), nullable=False),
        sa.Column("independent_refute_count", sa.Integer(), nullable=False),
        sa.Column("rationale_json", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reasoning_run_id"],
            ["evidence_reasoning_runs.reasoning_run_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["research_claims.claim_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("resolution_id"),
    )
    op.create_index(
        "ix_claim_resolutions_reasoning_run",
        "claim_resolutions",
        ["reasoning_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_claim_resolutions_claim_id",
        "claim_resolutions",
        ["claim_id"],
        unique=False,
    )
    op.create_index(
        "ix_claim_resolutions_status",
        "claim_resolutions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_claim_resolutions_status", table_name="claim_resolutions")
    op.drop_index("ix_claim_resolutions_claim_id", table_name="claim_resolutions")
    op.drop_index("ix_claim_resolutions_reasoning_run", table_name="claim_resolutions")
    op.drop_table("claim_resolutions")
    op.drop_index("ix_evidence_reliability_scores_cluster", table_name="evidence_reliability_scores")
    op.drop_index("ix_evidence_reliability_scores_edge_id", table_name="evidence_reliability_scores")
    op.drop_index(
        "ix_evidence_reliability_scores_reasoning_run",
        table_name="evidence_reliability_scores",
    )
    op.drop_table("evidence_reliability_scores")
    op.drop_index("ix_evidence_reasoning_runs_policy_version", table_name="evidence_reasoning_runs")
    op.drop_index("ix_evidence_reasoning_runs_run_id", table_name="evidence_reasoning_runs")
    op.drop_table("evidence_reasoning_runs")
