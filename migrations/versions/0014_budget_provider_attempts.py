"""Record physical provider attempts separately from logical LLM calls."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_budget_provider_attempts"
down_revision = "0013_research_result_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_budgets",
        sa.Column(
            "provider_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_budgets", "provider_attempts")
