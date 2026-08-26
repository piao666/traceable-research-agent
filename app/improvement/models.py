"""Improvement log ORM model — one row per completed run."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ImprovementLog(Base):
    """Records auto-evaluation results for every completed run."""

    __tablename__ = "improvement_logs"

    run_id: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )
    question_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_composition: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)

    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    factual_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    auditability_score: Mapped[float] = mapped_column(Float, default=0.0)

    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    tier_t0: Mapped[int] = mapped_column(Integer, default=0)
    tier_t1: Mapped[int] = mapped_column(Integer, default=0)
    tier_t2: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )