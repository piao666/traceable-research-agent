"""SQLAlchemy models for immutable sources, claims, and citations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvidencePipelineRun(Base):
    __tablename__ = "evidence_pipeline_runs"

    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        Index("ix_source_documents_run_id", "run_id"),
        Index("ix_source_documents_canonical_uri", "canonical_uri"),
    )

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        Index("ix_source_snapshots_document_id", "document_id"),
        Index("ix_source_snapshots_trace_id", "trace_id"),
        Index("ix_source_snapshots_content_hash", "content_hash"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("source_documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("tool_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidencePassage(Base):
    __tablename__ = "evidence_passages"
    __table_args__ = (
        Index("ix_evidence_passages_snapshot_id", "snapshot_id"),
        Index("ix_evidence_passages_trace_id", "trace_id"),
    )

    passage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("source_snapshots.snapshot_id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("tool_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="snippet_only")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceAssertion(Base):
    __tablename__ = "evidence_assertions"
    __table_args__ = (Index("ix_evidence_assertions_passage_id", "passage_id"),)

    assertion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    passage_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evidence_passages.passage_id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("tool_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
    )
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicate: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_scope: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualifier_json: Mapped[str] = mapped_column(Text, nullable=False)
    polarity: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchClaim(Base):
    __tablename__ = "research_claims"
    __table_args__ = (Index("ix_research_claims_run_id", "run_id"),)

    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_predicate: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_object: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_scope: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualifier_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimEvidenceEdge(Base):
    __tablename__ = "claim_evidence_edges"
    __table_args__ = (
        Index("ix_claim_evidence_edges_claim_id", "claim_id"),
        Index("ix_claim_evidence_edges_assertion_id", "assertion_id"),
    )

    edge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("research_claims.claim_id", ondelete="CASCADE"),
        nullable=False,
    )
    assertion_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evidence_assertions.assertion_id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReportClaim(Base):
    __tablename__ = "report_claims"
    __table_args__ = (Index("ix_report_claims_run_id", "run_id"),)

    report_claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("research_claims.claim_id", ondelete="SET NULL"),
        nullable=True,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        Index("ix_citations_report_claim_id", "report_claim_id"),
        Index("ix_citations_passage_id", "passage_id"),
    )

    citation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_claim_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("report_claims.report_claim_id", ondelete="CASCADE"),
        nullable=False,
    )
    passage_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evidence_passages.passage_id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("claim_evidence_edges.edge_id", ondelete="SET NULL"),
        nullable=True,
    )
    citation_label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceReasoningRun(Base):
    __tablename__ = "evidence_reasoning_runs"
    __table_args__ = (
        Index("ix_evidence_reasoning_runs_run_id", "run_id"),
        Index("ix_evidence_reasoning_runs_policy_version", "policy_version"),
    )

    reasoning_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class EvidenceReliabilityScore(Base):
    __tablename__ = "evidence_reliability_scores"
    __table_args__ = (
        Index("ix_evidence_reliability_scores_reasoning_run", "reasoning_run_id"),
        Index("ix_evidence_reliability_scores_edge_id", "edge_id"),
        Index("ix_evidence_reliability_scores_cluster", "source_cluster_id"),
    )

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reasoning_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evidence_reasoning_runs.reasoning_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("claim_evidence_edges.edge_id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_class: Mapped[str] = mapped_column(String(64), nullable=False)
    source_cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    authority: Mapped[float] = mapped_column(Float, nullable=False)
    traceability: Mapped[float] = mapped_column(Float, nullable=False)
    freshness: Mapped[float] = mapped_column(Float, nullable=False)
    relevance: Mapped[float] = mapped_column(Float, nullable=False)
    independence: Mapped[float] = mapped_column(Float, nullable=False)
    extraction_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale_json: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimResolution(Base):
    __tablename__ = "claim_resolutions"
    __table_args__ = (
        Index("ix_claim_resolutions_reasoning_run", "reasoning_run_id"),
        Index("ix_claim_resolutions_claim_id", "claim_id"),
        Index("ix_claim_resolutions_status", "status"),
    )

    resolution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reasoning_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evidence_reasoning_runs.reasoning_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("research_claims.claim_id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    support_quality: Mapped[float] = mapped_column(Float, nullable=False)
    refute_quality: Mapped[float] = mapped_column(Float, nullable=False)
    independent_support_count: Mapped[int] = mapped_column(Integer, nullable=False)
    independent_refute_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale_json: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
