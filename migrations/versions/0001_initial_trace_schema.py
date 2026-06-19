"""Create the initial trace persistence schema.

Revision ID: 0001_initial_trace_schema
Revises: None
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_trace_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("report_type", sa.String(), nullable=False),
        sa.Column("source_mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("allowed_tools_json", sa.Text(), nullable=True),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_tool_calls", sa.Integer(), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "tool_traces",
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("token_in", sa.Integer(), nullable=False),
        sa.Column("token_out", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_tool_traces_run_id", "tool_traces", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_traces_run_id", table_name="tool_traces")
    op.drop_table("tool_traces")
    op.drop_table("agent_runs")
