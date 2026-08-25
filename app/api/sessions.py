"""Session and chat-turn endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.memory import store as memory_store
from app.schemas import (
    ChatTurnResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionResponse,
    SessionUpdateRequest,
)
from app.security import require_api_key

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_api_key)],
)


def _session_response(session_obj, turn_count: int = 0) -> SessionResponse:
    return SessionResponse(
        session_id=session_obj.session_id,
        title=session_obj.title,
        turn_count=turn_count,
        created_at=session_obj.created_at,
        updated_at=session_obj.updated_at,
    )


def _turn_response(turn) -> ChatTurnResponse:
    return ChatTurnResponse(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        role=turn.role,
        content=turn.content,
        run_id=turn.run_id,
        created_at=turn.created_at,
    )


@router.post("", response_model=SessionResponse)
async def create_session_endpoint(
    request_body: SessionCreateRequest,
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Create a new local conversation session."""

    session_obj = memory_store.create_session(
        db=db,
        title=request_body.title,
    )
    return _session_response(session_obj, turn_count=0)


@router.get("", response_model=list[SessionResponse])
async def list_sessions_endpoint(
    db: Session = Depends(get_db),
) -> list[SessionResponse]:
    """List all local sessions."""

    sessions = memory_store.list_sessions(db)
    return [
        _session_response(
            s,
            turn_count=memory_store.count_turns_for_session(db, s.session_id),
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
) -> SessionDetailResponse:
    """Get a session with all its chat turns."""

    session_obj = memory_store.get_session(db, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = memory_store.list_chat_turns(db, session_id)
    return SessionDetailResponse(
        session_id=session_obj.session_id,
        title=session_obj.title,
        turns=[_turn_response(t) for t in turns],
        created_at=session_obj.created_at,
        updated_at=session_obj.updated_at,
    )


@router.get("/{session_id}/turns", response_model=list[ChatTurnResponse])
async def list_turns_endpoint(
    session_id: str,
    db: Session = Depends(get_db),
) -> list[ChatTurnResponse]:
    """List chat turns for a session."""

    session_obj = memory_store.get_session(db, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")
    turns = memory_store.list_chat_turns(db, session_id)
    return [_turn_response(t) for t in turns]


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session_endpoint(
    session_id: str,
    request: SessionUpdateRequest,
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Update session metadata (e.g. title)."""
    session_obj = memory_store.get_session(db, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if request.title is not None:
        session_obj = memory_store.update_session_title(db, session_id, request.title)
    return SessionResponse(
        session_id=session_obj.session_id,
        title=session_obj.title,
        created_at=session_obj.created_at,
        updated_at=session_obj.updated_at,
    )
