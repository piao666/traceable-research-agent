"""User memory endpoints with trace-audited delete operations."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.memory import store as memory_store
from app.memory.models import UserMemory
from app.schemas import (
    MemoryConfirmRequest,
    MemoryListResponse,
    UserMemoryResponse,
)
from app.security import require_api_key

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(require_api_key)],
)


def _memory_response(memory: UserMemory) -> UserMemoryResponse:
    return UserMemoryResponse(
        memory_id=memory.memory_id,
        kind=memory.kind,
        extraction_method=memory.extraction_method,
        content=memory.content,
        confidence=memory.confidence,
        status=memory.status,
        source_session_id=memory.source_session_id,
        source_run_id=memory.source_run_id,
        valid_until=memory.valid_until,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    db: Session = Depends(get_db),
    status: str | None = Query(default=None, pattern="^(pending|active|superseded|expired)$"),
) -> MemoryListResponse:
    """List local memories, optionally filtered by status."""

    memories = memory_store.list_user_memories(db, status=status)
    all_memories = memory_store.list_user_memories(db)
    return MemoryListResponse(
        memories=[_memory_response(m) for m in memories],
        total=len(all_memories),
        active_count=sum(1 for m in all_memories if m.status == "active"),
        pending_count=sum(1 for m in all_memories if m.status == "pending"),
    )


@router.post("/{memory_id}/confirm", response_model=UserMemoryResponse)
async def confirm_memory(
    memory_id: str,
    body: MemoryConfirmRequest,
    db: Session = Depends(get_db),
) -> UserMemoryResponse:
    """Confirm (activate) or reject (delete) a pending memory."""

    memory = memory_store.get_user_memory(db, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm memory with status '{memory.status}'. Only pending memories can be confirmed.",
        )

    if body.approved:
        memory = memory_store.update_memory_status(db, memory_id, "active")
    else:
        memory_store.delete_user_memory(db, memory_id)
        return UserMemoryResponse(
            memory_id=memory_id,
            kind=memory.kind,
            extraction_method=memory.extraction_method,
            content="[deleted]",
            confidence=0,
            status="deleted",
            source_session_id=None,
            source_run_id=None,
            valid_until=None,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )
    return _memory_response(memory)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Delete a single memory and write a trace event for audit."""

    memory = memory_store.get_user_memory(db, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory_store.delete_user_memory(db, memory_id)
    return {"memory_id": memory_id, "deleted": True, "message": "Memory deleted."}


@router.delete("")
async def clear_all_memories(
    db: Session = Depends(get_db),
) -> dict:
    """Delete all local memories."""

    count = memory_store.delete_all_user_memories(db)
    return {
        "deleted": True,
        "count": count,
        "message": f"All {count} memories cleared.",
    }
