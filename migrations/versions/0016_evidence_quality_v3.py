"""Add claim-centric evidence quality metrics to improvement logs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_evidence_quality_v3"
down_revision = "0015_report_claim_scope_lineage"
branch_labels = None
depends_on = None


_COLUMNS = (
    sa.Column("quality_schema_version", sa.String(length=64), nullable=True),
    sa.Column("evidence_quality_score", sa.Float(), nullable=False, server_default="0"),
    sa.Column("claim_support_coverage", sa.Float(), nullable=False, server_default="0"),
    sa.Column("strong_claim_coverage", sa.Float(), nullable=False, server_default="0"),
    sa.Column("independent_claim_coverage", sa.Float(), nullable=False, server_default="0"),
    sa.Column("mean_cited_reliability", sa.Float(), nullable=False, server_default="0"),
    sa.Column("p25_cited_reliability", sa.Float(), nullable=False, server_default="0"),
    sa.Column("independent_source_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("unique_resource_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("unresolved_conflict_count", sa.Integer(), nullable=False, server_default="0"),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("improvement_logs", column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("improvement_logs", column.name)
