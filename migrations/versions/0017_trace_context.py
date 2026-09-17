"""Add phase and lineage context to persisted traces."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_trace_context"
down_revision = "0016_evidence_quality_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_traces", sa.Column("phase", sa.String(length=64), nullable=True))
    op.add_column("tool_traces", sa.Column("parent_trace_id", sa.String(), nullable=True))
    op.add_column("tool_traces", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_tool_traces_phase", "tool_traces", ["phase"])
    op.create_index("ix_tool_traces_parent_trace_id", "tool_traces", ["parent_trace_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_traces_parent_trace_id", table_name="tool_traces")
    op.drop_index("ix_tool_traces_phase", table_name="tool_traces")
    op.drop_column("tool_traces", "attempt")
    op.drop_column("tool_traces", "parent_trace_id")
    op.drop_column("tool_traces", "phase")
