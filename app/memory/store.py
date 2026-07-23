"""Persistence helpers for session and user memory records.

Aligns with app/trace/store.py style: plain functions taking db: Session.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.memory.models import ChatTurn, ConversationSession, UserMemory


# ── ConversationSession ──────────────────────────────────────────────

def create_session(
    db: Session,
    tenant_id: str,
    user_id: str,
    title: str | None = None,
) -> ConversationSession:
    """Create a new conversation session."""

    session = ConversationSession(
        session_id=uuid4().hex,
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: str) -> ConversationSession | None:
    """Fetch one session by id."""

    return db.get(ConversationSession, session_id)


def list_sessions(
    db: Session,
    tenant_id: str,
    user_id: str,
) -> list[ConversationSession]:
    """Return all sessions for a (tenant, user) pair, newest first."""

    stmt = (
        select(ConversationSession)
        .where(
            ConversationSession.tenant_id == tenant_id,
            ConversationSession.user_id == user_id,
        )
        .order_by(ConversationSession.created_at.desc())
    )
    return list(db.scalars(stmt).all())


def update_session_title(
    db: Session,
    session_id: str,
    title: str,
) -> ConversationSession:
    """Set or update session title."""

    session = db.get(ConversationSession, session_id)
    if session is None:
        raise ValueError("Session not found")
    session.title = title
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


# ── ChatTurn ──────────────────────────────────────────────────────────

def create_chat_turn(
    db: Session,
    session_id: str,
    role: str,
    content: str,
    run_id: str | None = None,
) -> ChatTurn:
    """Record one user or agent message in a session."""

    turn = ChatTurn(
        turn_id=uuid4().hex,
        session_id=session_id,
        role=role,
        content=content,
        run_id=run_id,
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn


def list_chat_turns(
    db: Session,
    session_id: str,
) -> list[ChatTurn]:
    """Return turns for a session in chronological order."""

    stmt = (
        select(ChatTurn)
        .where(ChatTurn.session_id == session_id)
        .order_by(ChatTurn.created_at.asc())
    )
    return list(db.scalars(stmt).all())


# ── UserMemory ────────────────────────────────────────────────────────

def create_user_memory(
    db: Session,
    tenant_id: str,
    user_id: str,
    kind: str,
    extraction_method: str,
    content: str,
    confidence: float = 0.5,
    source_session_id: str | None = None,
    source_run_id: str | None = None,
    valid_until: datetime | None = None,
) -> UserMemory:
    """Create a user memory record (defaults to pending status)."""

    memory = UserMemory(
        memory_id=uuid4().hex,
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind,
        extraction_method=extraction_method,
        content=content,
        confidence=confidence,
        status="pending",
        source_session_id=source_session_id,
        source_run_id=source_run_id,
        valid_until=valid_until,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_user_memory(db: Session, memory_id: str) -> UserMemory | None:
    """Fetch one memory by id."""

    return db.get(UserMemory, memory_id)


def list_user_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
    status: str | None = None,
) -> list[UserMemory]:
    """Return memories for a (tenant, user) pair, optionally filtered by status."""

    stmt = select(UserMemory).where(
        UserMemory.tenant_id == tenant_id,
        UserMemory.user_id == user_id,
    )
    if status is not None:
        stmt = stmt.where(UserMemory.status == status)
    stmt = stmt.order_by(UserMemory.created_at.desc())
    return list(db.scalars(stmt).all())


def update_memory_status(
    db: Session,
    memory_id: str,
    status: str,
) -> UserMemory:
    """Transition a memory to a new status."""

    memory = db.get(UserMemory, memory_id)
    if memory is None:
        raise ValueError("Memory not found")
    memory.status = status
    memory.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(memory)
    return memory


def supersede_memory(db: Session, memory_id: str) -> UserMemory:
    """Mark a memory as superseded (preserves history)."""

    return update_memory_status(db, memory_id, "superseded")


def delete_user_memory(db: Session, memory_id: str) -> None:
    """Hard-delete one memory."""

    memory = db.get(UserMemory, memory_id)
    if memory is None:
        raise ValueError("Memory not found")
    db.delete(memory)
    db.commit()


def delete_all_user_memories(
    db: Session,
    tenant_id: str,
    user_id: str,
) -> int:
    """Delete all memories for a (tenant, user) pair. Returns count."""

    stmt = delete(UserMemory).where(
        UserMemory.tenant_id == tenant_id,
        UserMemory.user_id == user_id,
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


def expire_memories(db: Session) -> int:
    """Transition past-valid_until active memories to expired. Returns count."""

    now = datetime.now(timezone.utc)
    stmt = (
        select(UserMemory)
        .where(
            UserMemory.status == "active",
            UserMemory.valid_until.isnot(None),
            UserMemory.valid_until < now,
        )
    )
    expired = list(db.scalars(stmt).all())
    for memory in expired:
        memory.status = "expired"
        memory.updated_at = now
    if expired:
        db.commit()
    return len(expired)


def count_turns_for_session(db: Session, session_id: str) -> int:
    """Return the number of chat turns in a session."""

    stmt = select(ChatTurn).where(ChatTurn.session_id == session_id)
    return len(list(db.scalars(stmt).all()))
