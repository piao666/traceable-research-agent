"""Create claim-level provenance and citation tables.

Revision ID: 0002_claim_provenance_schema
Revises: 0001_initial_trace_schema
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_claim_provenance_schema"
down_revision: str | None = "0001_initial_trace_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_pipeline_runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "source_documents",
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_uri", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("organization", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_source_documents_canonical_uri",
        "source_documents",
        ["canonical_uri"],
        unique=False,
    )
    op.create_index(
        "ix_source_documents_run_id",
        "source_documents",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "source_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.document_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["tool_traces.trace_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(
        "ix_source_snapshots_content_hash",
        "source_snapshots",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_source_snapshots_document_id",
        "source_snapshots",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_snapshots_trace_id",
        "source_snapshots",
        ["trace_id"],
        unique=False,
    )
    op.create_table(
        "evidence_passages",
        sa.Column("passage_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.snapshot_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["tool_traces.trace_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("passage_id"),
    )
    op.create_index(
        "ix_evidence_passages_snapshot_id",
        "evidence_passages",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_passages_trace_id",
        "evidence_passages",
        ["trace_id"],
        unique=False,
    )
    op.create_table(
        "evidence_assertions",
        sa.Column("assertion_id", sa.String(length=64), nullable=False),
        sa.Column("passage_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("predicate", sa.Text(), nullable=True),
        sa.Column("object_text", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("time_scope", sa.String(length=128), nullable=True),
        sa.Column("qualifier_json", sa.Text(), nullable=False),
        sa.Column("polarity", sa.String(length=32), nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["passage_id"], ["evidence_passages.passage_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["tool_traces.trace_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("assertion_id"),
    )
    op.create_index(
        "ix_evidence_assertions_passage_id",
        "evidence_assertions",
        ["passage_id"],
        unique=False,
    )
    op.create_table(
        "research_claims",
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("normalized_subject", sa.Text(), nullable=True),
        sa.Column("normalized_predicate", sa.Text(), nullable=True),
        sa.Column("normalized_object", sa.Text(), nullable=True),
        sa.Column("value_json", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("time_scope", sa.String(length=128), nullable=True),
        sa.Column("qualifier_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("claim_id"),
    )
    op.create_index(
        "ix_research_claims_run_id",
        "research_claims",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "claim_evidence_edges",
        sa.Column("edge_id", sa.String(length=64), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("assertion_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assertion_id"], ["evidence_assertions.assertion_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["research_claims.claim_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("edge_id"),
    )
    op.create_index(
        "ix_claim_evidence_edges_assertion_id",
        "claim_evidence_edges",
        ["assertion_id"],
        unique=False,
    )
    op.create_index(
        "ix_claim_evidence_edges_claim_id",
        "claim_evidence_edges",
        ["claim_id"],
        unique=False,
    )
    op.create_table(
        "report_claims",
        sa.Column("report_claim_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["research_claims.claim_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("report_claim_id"),
    )
    op.create_index(
        "ix_report_claims_run_id",
        "report_claims",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "citations",
        sa.Column("citation_id", sa.String(length=64), nullable=False),
        sa.Column("report_claim_id", sa.String(length=64), nullable=False),
        sa.Column("passage_id", sa.String(length=64), nullable=False),
        sa.Column("edge_id", sa.String(length=64), nullable=True),
        sa.Column("citation_label", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["edge_id"], ["claim_evidence_edges.edge_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["passage_id"], ["evidence_passages.passage_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_claim_id"], ["report_claims.report_claim_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("citation_id"),
    )
    op.create_index(
        "ix_citations_passage_id",
        "citations",
        ["passage_id"],
        unique=False,
    )
    op.create_index(
        "ix_citations_report_claim_id",
        "citations",
        ["report_claim_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_citations_report_claim_id", table_name="citations")
    op.drop_index("ix_citations_passage_id", table_name="citations")
    op.drop_table("citations")
    op.drop_index("ix_report_claims_run_id", table_name="report_claims")
    op.drop_table("report_claims")
    op.drop_index("ix_claim_evidence_edges_claim_id", table_name="claim_evidence_edges")
    op.drop_index("ix_claim_evidence_edges_assertion_id", table_name="claim_evidence_edges")
    op.drop_table("claim_evidence_edges")
    op.drop_index("ix_research_claims_run_id", table_name="research_claims")
    op.drop_table("research_claims")
    op.drop_index("ix_evidence_assertions_passage_id", table_name="evidence_assertions")
    op.drop_table("evidence_assertions")
    op.drop_index("ix_evidence_passages_trace_id", table_name="evidence_passages")
    op.drop_index("ix_evidence_passages_snapshot_id", table_name="evidence_passages")
    op.drop_table("evidence_passages")
    op.drop_index("ix_source_snapshots_trace_id", table_name="source_snapshots")
    op.drop_index("ix_source_snapshots_document_id", table_name="source_snapshots")
    op.drop_index("ix_source_snapshots_content_hash", table_name="source_snapshots")
    op.drop_table("source_snapshots")
    op.drop_index("ix_source_documents_run_id", table_name="source_documents")
    op.drop_index("ix_source_documents_canonical_uri", table_name="source_documents")
    op.drop_table("source_documents")
    op.drop_table("evidence_pipeline_runs")
