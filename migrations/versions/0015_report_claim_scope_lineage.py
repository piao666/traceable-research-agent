"""Persist final-report claim to ScopeClaimGroup lineage."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_report_claim_scope_lineage"
down_revision = "0014_budget_provider_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_claim_scope_group_links",
        sa.Column("link_id", sa.String(length=64), nullable=False),
        sa.Column("claim_occurrence_id", sa.String(length=64), nullable=False),
        sa.Column("scope_group_id", sa.String(length=64), nullable=False),
        sa.Column("mapping_source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mapping_source IN ('citation_lineage', 'claim_member_lineage', 'text_fallback')",
            name="ck_report_claim_scope_group_links_mapping_source",
        ),
        sa.ForeignKeyConstraint(
            ["claim_occurrence_id"],
            ["report_claim_occurrences.claim_occurrence_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_group_id"],
            ["scope_claim_groups.group_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("link_id"),
        sa.UniqueConstraint(
            "claim_occurrence_id",
            "scope_group_id",
            name="uq_report_claim_scope_group_links_claim_group",
        ),
    )
    op.create_index(
        "ix_report_claim_scope_group_links_claim_occurrence",
        "report_claim_scope_group_links",
        ["claim_occurrence_id"],
    )
    op.create_index(
        "ix_report_claim_scope_group_links_scope_group",
        "report_claim_scope_group_links",
        ["scope_group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_claim_scope_group_links_scope_group",
        table_name="report_claim_scope_group_links",
    )
    op.drop_index(
        "ix_report_claim_scope_group_links_claim_occurrence",
        table_name="report_claim_scope_group_links",
    )
    op.drop_table("report_claim_scope_group_links")
