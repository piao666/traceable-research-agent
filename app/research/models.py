"""Persisted research-scope and research-tree models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
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
