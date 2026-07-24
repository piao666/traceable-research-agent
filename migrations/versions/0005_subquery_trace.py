"""Add sub_query column to tool_traces for fan-out grouping.

Revision ID: 0005_subquery_trace
Revises: 0004_memory_schema
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_subquery_trace"
down_revision: str | None = "0004_memory_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_traces") as batch_op:
        batch_op.add_column(
            sa.Column("sub_query", sa.Text(), nullable=True)
        )
        batch_op.create_index(
            "ix_tool_traces_sub_query",
            ["sub_query"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_traces") as batch_op:
        batch_op.drop_index("ix_tool_traces_sub_query")
        batch_op.drop_column("sub_query")
