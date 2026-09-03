"""Add shared execution budgets without rewriting existing task data."""
from alembic import op
import sqlalchemy as sa

revision = "0011_run_budgets"
down_revision = "0010_memory_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("run_budgets",
        sa.Column("run_id", sa.String(), sa.ForeignKey("agent_runs.run_id"), primary_key=True),
        sa.Column("root_run_id", sa.String(), sa.ForeignKey("agent_runs.run_id"), nullable=False),
        sa.Column("limits_json", sa.Text(), nullable=False),
        sa.Column("deadline", sa.Float(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("stop_reason", sa.String(), nullable=True))


def downgrade():
    op.drop_table("run_budgets")
