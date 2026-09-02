"""Persistence helpers for session and user memory records.

Aligns with app/trace/store.py style: plain functions taking db: Session.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select, update, func
from sqlalchemy.orm import Session

from app.memory.models import ChatTurn, ConversationSession, UserMemory, MemoryAuditEvent


# ── ConversationSession ──────────────────────────────────────────────

def create_session(
    db: Session,
    title: str | None = None,
) -> ConversationSession:
    """Create a new conversation session."""

    session = ConversationSession(
        session_id=uuid4().hex,
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
) -> list[ConversationSession]:
    """Return all local sessions, newest first."""

    stmt = select(ConversationSession).order_by(ConversationSession.updated_at.desc(), ConversationSession.session_id)
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
    db.execute(update(ConversationSession).where(ConversationSession.session_id == session_id)
               .values(updated_at=datetime.now(timezone.utc)))
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
    status: str | None = None,
) -> list[UserMemory]:
    """Return local memories, optionally filtered by status."""

    stmt = select(UserMemory)
    stmt = stmt.order_by(UserMemory.created_at.desc())
    memories = list(db.scalars(stmt).all())
    return [m for m in memories if status is None or effective_memory_status(m) == status]


def effective_memory_status(memory: UserMemory) -> str:
    """Read-only expiry interpretation; do not rewrite historical records on GET."""
    until = memory.valid_until
    if until and memory.status in {"active", "pending"}:
        until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
        if until <= datetime.now(timezone.utc):
            return "expired"
    return memory.status


def _audit(db: Session, action: str, memory_id: str | None, count: int) -> None:
    db.add(MemoryAuditEvent(event_id=uuid4().hex, action=action,
                           memory_id=memory_id, affected_count=count))


def decide_pending_memory(db: Session, memory_id: str, approved: bool) -> bool:
    """Atomic pending-state claim and audit; expired memories cannot be activated."""
    now = datetime.now(timezone.utc)
    predicate = (UserMemory.memory_id == memory_id, UserMemory.status == "pending",
                 (UserMemory.valid_until.is_(None) | (UserMemory.valid_until > now)))
    statement = (update(UserMemory).where(*predicate).values(status="active", updated_at=now)
                 if approved else delete(UserMemory).where(*predicate))
    try:
        result = db.execute(statement.execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            return False
        _audit(db, "confirm" if approved else "reject", memory_id, 1)
        db.commit()
        db.expire_all()
        return True
    except Exception:
        db.rollback()
        raise


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
    try:
        result = db.execute(delete(UserMemory).where(UserMemory.memory_id == memory_id))
        if result.rowcount != 1:
            raise ValueError("Memory not found")
        _audit(db, "delete", memory_id, 1)
        db.commit()
    except Exception:
        db.rollback()
        raise


def delete_all_user_memories(
    db: Session,
) -> int:
    """Delete all local memories. Returns count."""

    stmt = delete(UserMemory)
    try:
        result = db.execute(stmt)
        _audit(db, "clear", None, result.rowcount)
        db.commit()
        return result.rowcount
    except Exception:
        db.rollback()
        raise


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

    stmt = select(func.count()).select_from(ChatTurn).where(ChatTurn.session_id == session_id)
    return db.scalar(stmt) or 0
