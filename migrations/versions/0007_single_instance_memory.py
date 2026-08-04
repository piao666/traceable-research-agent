"""Remove tenant and user scope from local memory tables.

Revision ID: 0007_single_instance_memory
Revises: 0006_content_basis
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_single_instance_memory"
down_revision: str | None = "0006_content_basis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.drop_index("ix_conversation_sessions_tenant_user")
        batch_op.drop_column("tenant_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("user_memories") as batch_op:
        batch_op.drop_index("ix_user_memories_tenant_user")
        batch_op.drop_column("tenant_id")
        batch_op.drop_column("user_id")


def downgrade() -> None:
    with op.batch_alter_table("user_memories") as batch_op:
        batch_op.add_column(
            sa.Column("user_id", sa.String(length=80), nullable=False, server_default="local")
        )
        batch_op.add_column(
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="local")
        )
        batch_op.create_index(
            "ix_user_memories_tenant_user", ["tenant_id", "user_id"], unique=False
        )

    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("user_id", sa.String(length=80), nullable=False, server_default="local")
        )
        batch_op.add_column(
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="local")
        )
        batch_op.create_index(
            "ix_conversation_sessions_tenant_user", ["tenant_id", "user_id"], unique=False
        )
