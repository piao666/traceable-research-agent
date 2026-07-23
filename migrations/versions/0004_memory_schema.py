"""Add conversation sessions, chat turns, user memories tables;
extend agent_runs with session_id and run_config_snapshot columns.

Revision ID: 0004_memory_schema
Revises: 0003_evidence_reasoning
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_memory_schema"
down_revision: str | None = "0003_evidence_reasoning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "ix_conversation_sessions_tenant_user",
        "conversation_sessions",
        ["tenant_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "chat_turns",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
    )
    op.create_index(
        "ix_chat_turns_session_id",
        "chat_turns",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_turns_run_id",
        "chat_turns",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "user_memories",
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("extraction_method", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="'pending'"),
        sa.Column("source_session_id", sa.String(length=64), nullable=True),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("memory_id"),
    )
    op.create_index(
        "ix_user_memories_tenant_user",
        "user_memories",
        ["tenant_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_memories_status",
        "user_memories",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_user_memories_source_run",
        "user_memories",
        ["source_run_id"],
        unique=False,
    )

    # Extend agent_runs
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column("session_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("run_config_snapshot", sa.Text(), nullable=True)
        )
        batch_op.create_index(
            "ix_agent_runs_session_id",
            ["session_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_session_id")
        batch_op.drop_column("run_config_snapshot")
        batch_op.drop_column("session_id")

    op.drop_index("ix_user_memories_source_run", table_name="user_memories")
    op.drop_index("ix_user_memories_status", table_name="user_memories")
    op.drop_index("ix_user_memories_tenant_user", table_name="user_memories")
    op.drop_table("user_memories")

    op.drop_index("ix_chat_turns_run_id", table_name="chat_turns")
    op.drop_index("ix_chat_turns_session_id", table_name="chat_turns")
    op.drop_table("chat_turns")

    op.drop_index("ix_conversation_sessions_tenant_user", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
