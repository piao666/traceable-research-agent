"""Persist citation validation metrics on agent runs.

Revision ID: 0008_citation_validation_metrics
Revises: 0007_single_instance_memory
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_citation_validation_metrics"
down_revision: str | None = "0007_single_instance_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column("citation_total", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("citation_supported", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "citation_weakly_supported",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("citation_unsupported", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("citation_accuracy", sa.Float(), nullable=False, server_default="1.0")
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("citation_accuracy")
        batch_op.drop_column("citation_unsupported")
        batch_op.drop_column("citation_weakly_supported")
        batch_op.drop_column("citation_supported")
        batch_op.drop_column("citation_total")
