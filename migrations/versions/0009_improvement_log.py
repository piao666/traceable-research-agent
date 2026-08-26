"""Create improvement_logs table for self-improvement feedback loop.

Revision ID: 0009_improvement_log
Revises: 0008_citation_validation_metrics
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_improvement_log"
down_revision: str | None = "0008_citation_validation_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "improvement_logs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("question_category", sa.String(64), nullable=True),
        sa.Column("skill_composition", sa.Text(), nullable=True),
        sa.Column("execution_mode", sa.String(32), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("factual_accuracy", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("coverage_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("source_quality_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("auditability_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_t0", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_t1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_t2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("improvement_logs")