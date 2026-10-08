"""Add durable research work and source-attested entity identities."""
from alembic import op
import sqlalchemy as sa

revision = "0019_research_work_state"
down_revision = "0018_pear_contract_entities"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_entities",
        sa.Column("entity_id", sa.String(64), primary_key=True),
        sa.Column("root_run_id", sa.String(), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    op.create_index("ix_research_entities_root_run_id", "research_entities", ["root_run_id"])
    op.create_table("research_work_items",
        sa.Column("work_item_id", sa.String(64), primary_key=True),
        sa.Column("root_run_id", sa.String(), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.String(160), nullable=False),
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("research_entities.entity_id"), nullable=True),
        sa.Column("facet", sa.String(64), nullable=False),
        sa.Column("acquisition_status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("answer_status", sa.String(32), nullable=False, server_default="unanswered"),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default="answer_content_missing"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_research_work_items_root_run_id", "research_work_items", ["root_run_id"])
    op.add_column("research_operations", sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("research_operations", "payload_json")
    op.drop_table("research_work_items")
    op.drop_table("research_entities")
