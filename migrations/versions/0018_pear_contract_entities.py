"""Add durable PEAR contract, operation, and assessment entities."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018_pear_contract_entities"
down_revision = "0017_trace_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_plan_revisions",
        sa.Column("revision_id", sa.String(length=64), primary_key=True),
        sa.Column("root_run_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("contract_hash", sa.String(length=128), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["root_run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("root_run_id", "revision_number", name="uq_research_plan_revisions_root_revision"),
    )
    op.create_index("ix_research_plan_revisions_root_run_id", "research_plan_revisions", ["root_run_id"])
    op.create_index("ix_research_plan_revisions_status", "research_plan_revisions", ["status"])

    op.create_table(
        "research_questions",
        sa.Column("question_id", sa.String(length=160), primary_key=True),
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["research_plan_revisions.revision_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("revision_id", "ordinal", name="uq_research_questions_revision_ordinal"),
    )
    op.create_index("ix_research_questions_revision_id", "research_questions", ["revision_id"])

    op.create_table(
        "evidence_requirements",
        sa.Column("requirement_id", sa.String(length=160), primary_key=True),
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=True),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("dimension", sa.Text(), nullable=True),
        sa.Column("time_scope", sa.Text(), nullable=True),
        sa.Column("min_reliability", sa.Float(), nullable=False),
        sa.Column("min_independent_sources", sa.Integer(), nullable=False),
        sa.Column("acceptable_content_basis_json", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["research_plan_revisions.revision_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["research_questions.question_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_evidence_requirements_question_id", "evidence_requirements", ["question_id"])
    op.create_index("ix_evidence_requirements_status", "evidence_requirements", ["status"])
    op.create_index("ix_evidence_requirements_revision_id", "evidence_requirements", ["revision_id"])

    op.create_table(
        "requirement_claim_links",
        sa.Column("link_id", sa.String(length=64), primary_key=True),
        sa.Column("requirement_id", sa.String(length=160), nullable=False),
        sa.Column("claim_occurrence_id", sa.String(length=64), nullable=True),
        sa.Column("scope_group_id", sa.String(length=64), nullable=True),
        sa.Column("mapping_source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["evidence_requirements.requirement_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_occurrence_id"], ["report_claim_occurrences.claim_occurrence_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_group_id"], ["scope_claim_groups.group_id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "requirement_id", "claim_occurrence_id", "scope_group_id",
            name="uq_requirement_claim_links_mapping",
        ),
    )
    op.create_index("ix_requirement_claim_links_requirement_id", "requirement_claim_links", ["requirement_id"])
    op.create_index("ix_requirement_claim_links_claim_occurrence", "requirement_claim_links", ["claim_occurrence_id"])
    op.create_index("ix_requirement_claim_links_scope_group", "requirement_claim_links", ["scope_group_id"])

    op.create_table(
        "source_discovery_links",
        sa.Column("discovery_id", sa.String(length=64), primary_key=True),
        sa.Column("requirement_id", sa.String(length=160), nullable=False),
        sa.Column("source_identity", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("link_type", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["evidence_requirements.requirement_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("requirement_id", "source_identity", name="uq_source_discovery_requirement_source"),
    )
    op.create_index("ix_source_discovery_links_requirement_id", "source_discovery_links", ["requirement_id"])
    op.create_index("ix_source_discovery_links_source_identity", "source_discovery_links", ["source_identity"])

    op.create_table(
        "research_operations",
        sa.Column("operation_id", sa.String(length=64), primary_key=True),
        sa.Column("root_run_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("plan_revision_id", sa.String(length=64), nullable=True),
        sa.Column("parent_operation_id", sa.String(length=64), nullable=True),
        sa.Column("operation_kind", sa.String(length=32), nullable=False),
        sa.Column("logical_key", sa.String(length=256), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("arguments_hash", sa.String(length=128), nullable=True),
        sa.Column("result_revision", sa.String(length=128), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["root_run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["node_id"], ["research_nodes.node_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["research_plan_revisions.revision_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_operation_id"], ["research_operations.operation_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("root_run_id", "logical_key", "attempt", name="uq_research_operations_attempt"),
    )
    op.create_index("ix_research_operations_root_run_id", "research_operations", ["root_run_id"])
    op.create_index("ix_research_operations_run_id", "research_operations", ["run_id"])
    op.create_index("ix_research_operations_status", "research_operations", ["status"])

    op.create_table(
        "coverage_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("root_run_id", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=True),
        sa.Column("plan_revision_id", sa.String(length=64), nullable=True),
        sa.Column("assessor_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requirements_json", sa.Text(), nullable=False),
        sa.Column("gaps_json", sa.Text(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["root_run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_id"], ["research_scopes.scope_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["research_plan_revisions.revision_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_coverage_snapshots_root_run_id", "coverage_snapshots", ["root_run_id"])
    op.create_index("ix_coverage_snapshots_revision_id", "coverage_snapshots", ["plan_revision_id"])
    op.create_index("ix_coverage_snapshots_status", "coverage_snapshots", ["status"])

    op.create_table(
        "evidence_gaps",
        sa.Column("gap_id", sa.String(length=160), primary_key=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("requirement_id", sa.String(length=160), nullable=False),
        sa.Column("gap_type", sa.String(length=32), nullable=False),
        sa.Column("missing_dimensions_json", sa.Text(), nullable=False),
        sa.Column("related_claims_json", sa.Text(), nullable=False),
        sa.Column("related_groups_json", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["coverage_snapshots.snapshot_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requirement_id"], ["evidence_requirements.requirement_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_evidence_gaps_requirement_id", "evidence_gaps", ["requirement_id"])
    op.create_index("ix_evidence_gaps_snapshot_id", "evidence_gaps", ["snapshot_id"])
    op.create_index("ix_evidence_gaps_status", "evidence_gaps", ["status"])

    op.create_table(
        "node_execution_results",
        sa.Column("result_id", sa.String(length=64), primary_key=True),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("waiting_reason", sa.Text(), nullable=True),
        sa.Column("evidence_revision", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["research_operations.operation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["research_nodes.node_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("operation_id", name="uq_node_execution_results_operation"),
    )
    op.create_index("ix_node_execution_results_node_id", "node_execution_results", ["node_id"])
    op.create_index("ix_node_execution_results_run_id", "node_execution_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_node_execution_results_run_id", table_name="node_execution_results")
    op.drop_index("ix_node_execution_results_node_id", table_name="node_execution_results")
    op.drop_table("node_execution_results")
    op.drop_index("ix_evidence_gaps_status", table_name="evidence_gaps")
    op.drop_index("ix_evidence_gaps_snapshot_id", table_name="evidence_gaps")
    op.drop_index("ix_evidence_gaps_requirement_id", table_name="evidence_gaps")
    op.drop_table("evidence_gaps")
    op.drop_index("ix_coverage_snapshots_status", table_name="coverage_snapshots")
    op.drop_index("ix_coverage_snapshots_revision_id", table_name="coverage_snapshots")
    op.drop_index("ix_coverage_snapshots_root_run_id", table_name="coverage_snapshots")
    op.drop_table("coverage_snapshots")
    op.drop_index("ix_research_operations_status", table_name="research_operations")
    op.drop_index("ix_research_operations_run_id", table_name="research_operations")
    op.drop_index("ix_research_operations_root_run_id", table_name="research_operations")
    op.drop_table("research_operations")
    op.drop_index("ix_source_discovery_links_source_identity", table_name="source_discovery_links")
    op.drop_index("ix_source_discovery_links_requirement_id", table_name="source_discovery_links")
    op.drop_table("source_discovery_links")
    op.drop_index("ix_requirement_claim_links_scope_group", table_name="requirement_claim_links")
    op.drop_index("ix_requirement_claim_links_claim_occurrence", table_name="requirement_claim_links")
    op.drop_index("ix_requirement_claim_links_requirement_id", table_name="requirement_claim_links")
    op.drop_table("requirement_claim_links")
    op.drop_index("ix_evidence_requirements_revision_id", table_name="evidence_requirements")
    op.drop_index("ix_evidence_requirements_status", table_name="evidence_requirements")
    op.drop_index("ix_evidence_requirements_question_id", table_name="evidence_requirements")
    op.drop_table("evidence_requirements")
    op.drop_index("ix_research_questions_revision_id", table_name="research_questions")
    op.drop_table("research_questions")
    op.drop_index("ix_research_plan_revisions_status", table_name="research_plan_revisions")
    op.drop_index("ix_research_plan_revisions_root_run_id", table_name="research_plan_revisions")
    op.drop_table("research_plan_revisions")
