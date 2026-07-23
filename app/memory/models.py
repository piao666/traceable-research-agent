"""SQLAlchemy ORM models for session and user memory."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationSession(Base):
    """A single conversation window, scoped to (tenant_id, user_id)."""

    __tablename__ = "conversation_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


class ChatTurn(Base):
    """One user/agent message within a conversation session."""

    __tablename__ = "chat_turns"

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("conversation_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )


class UserMemory(Base):
    """Cross-session user memory, distilled from conversation runs."""

    __tablename__ = "user_memories"

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    source_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
