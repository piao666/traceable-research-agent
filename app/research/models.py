"""Persisted research-scope and research-tree models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchScope(Base):
    """One DB-authoritative Deep Research execution scope."""

    __tablename__ = "research_scopes"
    __table_args__ = (
        Index("ix_research_scopes_root_run_id", "root_run_id", unique=True),
        Index("ix_research_scopes_status", "status"),
    )

    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    research_contract_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ResearchNode(Base):
    """A persisted node in a Research Scope topic tree."""

    __tablename__ = "research_nodes"
    __table_args__ = (
        Index("ix_research_nodes_scope_id", "scope_id"),
        Index("ix_research_nodes_parent_node_id", "parent_node_id"),
        Index("ix_research_nodes_run_id", "run_id", unique=True),
        Index("ix_research_nodes_status", "status"),
    )

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("research_scopes.scope_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_node_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("research_nodes.node_id", ondelete="CASCADE"),
        nullable=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="SET NULL"),
        nullable=True,
    )
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    research_goal: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ResearchPlanRevision(Base):
    """Immutable, auditable contract revision for a root research run."""

    __tablename__ = "research_plan_revisions"
    __table_args__ = (
        UniqueConstraint("root_run_id", "revision_number", name="uq_research_plan_revisions_root_revision"),
        Index("ix_research_plan_revisions_root_run_id", "root_run_id"),
        Index("ix_research_plan_revisions_status", "status"),
    )

    revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchQuestion(Base):
    """A stable question belonging to one immutable plan revision."""

    __tablename__ = "research_questions"
    __table_args__ = (
        UniqueConstraint("revision_id", "ordinal", name="uq_research_questions_revision_ordinal"),
        Index("ix_research_questions_revision_id", "revision_id"),
    )

    question_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_plan_revisions.revision_id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceRequirement(Base):
    """Durable requirement state; it is never inferred from claim text alone."""

    __tablename__ = "evidence_requirements"
    __table_args__ = (
        Index("ix_evidence_requirements_question_id", "question_id"),
        Index("ix_evidence_requirements_status", "status"),
        Index("ix_evidence_requirements_revision_id", "revision_id"),
    )

    requirement_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_plan_revisions.revision_id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("research_questions.question_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    predicate: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimension: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_reliability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    min_independent_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    acceptable_content_basis_json: Mapped[str] = mapped_column(Text, nullable=False, default='["full_text"]')
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uncovered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RequirementClaimLink(Base):
    """Many-to-many mapping from a requirement to final claim/scope evidence."""

    __tablename__ = "requirement_claim_links"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id", "claim_occurrence_id", "scope_group_id",
            name="uq_requirement_claim_links_mapping",
        ),
        Index("ix_requirement_claim_links_requirement_id", "requirement_id"),
        Index("ix_requirement_claim_links_claim_occurrence", "claim_occurrence_id"),
        Index("ix_requirement_claim_links_scope_group", "scope_group_id"),
    )

    link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("evidence_requirements.requirement_id", ondelete="CASCADE"), nullable=False
    )
    claim_occurrence_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("report_claim_occurrences.claim_occurrence_id", ondelete="CASCADE"), nullable=True
    )
    scope_group_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("scope_claim_groups.group_id", ondelete="CASCADE"), nullable=True
    )
    mapping_source: Mapped[str] = mapped_column(String(32), nullable=False, default="assessor")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceDiscoveryLink(Base):
    """Traceable discovery of a source before it becomes independent evidence."""

    __tablename__ = "source_discovery_links"
    __table_args__ = (
        UniqueConstraint("requirement_id", "source_identity", name="uq_source_discovery_requirement_source"),
        Index("ix_source_discovery_links_requirement_id", "requirement_id"),
        Index("ix_source_discovery_links_source_identity", "source_identity"),
    )

    discovery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("evidence_requirements.requirement_id", ondelete="CASCADE"), nullable=False
    )
    source_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_type: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchOperation(Base):
    """Operation journal row reserved before any external tool invocation."""

    __tablename__ = "research_operations"
    __table_args__ = (
        UniqueConstraint("root_run_id", "logical_key", "attempt", name="uq_research_operations_attempt"),
        Index("ix_research_operations_root_run_id", "root_run_id"),
        Index("ix_research_operations_run_id", "run_id"),
        Index("ix_research_operations_status", "status"),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    node_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_nodes.node_id", ondelete="SET NULL"), nullable=True
    )
    plan_revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_plan_revisions.revision_id", ondelete="SET NULL"), nullable=True
    )
    parent_operation_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_operations.operation_id", ondelete="SET NULL"), nullable=True
    )
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    logical_key: Mapped[str] = mapped_column(String(256), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    arguments_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CoverageSnapshot(Base):
    """Versioned, read-only assessment output for a plan revision."""

    __tablename__ = "coverage_snapshots"
    __table_args__ = (
        Index("ix_coverage_snapshots_root_run_id", "root_run_id"),
        Index("ix_coverage_snapshots_revision_id", "plan_revision_id"),
        Index("ix_coverage_snapshots_status", "status"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    scope_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_scopes.scope_id", ondelete="SET NULL"), nullable=True
    )
    plan_revision_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("research_plan_revisions.revision_id", ondelete="SET NULL"), nullable=True
    )
    assessor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requirements_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    gaps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceGapRecord(Base):
    """Persisted unresolved requirement gap emitted by an assessor."""

    __tablename__ = "evidence_gaps"
    __table_args__ = (
        Index("ix_evidence_gaps_requirement_id", "requirement_id"),
        Index("ix_evidence_gaps_snapshot_id", "snapshot_id"),
        Index("ix_evidence_gaps_status", "status"),
    )

    gap_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("coverage_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("evidence_requirements.requirement_id", ondelete="CASCADE"), nullable=False
    )
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False)
    missing_dimensions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    related_claims_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    related_groups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NodeExecutionResult(Base):
    """Persisted controller-neutral result owned by a research node."""

    __tablename__ = "node_execution_results"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_node_execution_results_operation"),
        Index("ix_node_execution_results_node_id", "node_id"),
        Index("ix_node_execution_results_run_id", "run_id"),
    )

    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_operations.operation_id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
