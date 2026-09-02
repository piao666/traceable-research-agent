"""Add content-free memory action audit without rewriting existing records."""
from alembic import op
import sqlalchemy as sa

revision = "0010_memory_audit"
down_revision = "0009_improvement_log"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_audit_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("memory_id", sa.String(64), nullable=True),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("memory_audit_events")
