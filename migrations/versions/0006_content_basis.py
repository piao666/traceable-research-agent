"""Add content_basis column to evidence_passages.

Revision ID: 0006_content_basis
Revises: 0005_subquery_trace
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_content_basis"
down_revision: str | None = "0005_subquery_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evidence_passages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "content_basis",
                sa.String(length=32),
                nullable=False,
                server_default="snippet_only",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("evidence_passages") as batch_op:
        batch_op.drop_column("content_basis")
