"""Realtime task event streaming endpoints."""

from __future__ import annotations

import asyncio
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.database import SessionLocal, get_db
from app.security import require_api_key, require_request_context
from app.trace import store
from app.trace.events import (
    TraceEventCursor,
    build_incremental_events,
    format_sse,
    heartbeat_event,
)

router = APIRouter(
    prefix="/tasks",
    tags=["task-events"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)


@router.get("/{run_id}/events")
async def stream_task_events(
    run_id: str,
    request: Request,
    poll_interval_seconds: float = Query(0.5, ge=0.1, le=5.0),
    heartbeat_seconds: float = Query(15.0, ge=1.0, le=60.0),
    max_duration_seconds: float = Query(300.0, ge=1.0, le=3600.0),
    replay_existing: bool = Query(True),
    close_on_terminal: bool = Query(True),
    db=Depends(get_db),
) -> StreamingResponse:
    """Stream run status and trace updates as Server-Sent Events."""

    if store.get_agent_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="Task run not found")

    async def event_generator():
        cursor = TraceEventCursor()
        started_at = monotonic()
        last_heartbeat_at = 0.0

        while True:
            if await request.is_disconnected():
                break

            with SessionLocal() as stream_db:
                events, should_close = build_incremental_events(
                    stream_db,
                    run_id,
                    cursor,
                    replay_existing=replay_existing,
                )

            for index, event in enumerate(events):
                event_id = event.get("trace_id") or f"{event.get('event_type')}-{index}"
                yield format_sse(event, str(event_id))

            now = monotonic()
            if close_on_terminal and should_close:
                break

            if now - last_heartbeat_at >= heartbeat_seconds:
                yield format_sse(heartbeat_event(run_id), "heartbeat")
                last_heartbeat_at = now

            if now - started_at >= max_duration_seconds:
                yield format_sse(
                    {
                        **heartbeat_event(run_id),
                        "event_type": "done",
                        "status": "stream_timeout",
                        "output_summary": "SSE stream reached max_duration_seconds.",
                    },
                    "stream-timeout",
                )
                break

            await asyncio.sleep(poll_interval_seconds)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
