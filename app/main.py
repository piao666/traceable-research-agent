"""FastAPI entrypoint for Traceable Research Agent."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import events, memory, reports, sessions, tasks, tools
from app.config import settings
from app.database import init_db
from app.mcp import server as mcp_server
from app.mcp.client import register_remote_mcp_tools_from_settings, remote_mcp_servers_configured
from app.schemas import HealthResponse
from app.tools.defaults import register_default_tools


async def _register_remote_mcp_tools_with_retry() -> None:
    if not remote_mcp_servers_configured(settings):
        return

    attempts = max(1, int(settings.mcp_remote_registration_attempts or 1))
    retry_seconds = max(0, int(settings.mcp_remote_registration_retry_seconds or 0))
    for attempt in range(1, attempts + 1):
        registered = register_remote_mcp_tools_from_settings(settings)
        if registered:
            return
        if attempt < attempts and retry_seconds:
            await asyncio.sleep(retry_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize local SQLite tables and default tool metadata."""

    logging.getLogger(__name__).info(
        "Runtime configuration: %s",
        json.dumps(settings.get_safe_runtime_config_summary(), sort_keys=True),
    )
    init_db()
    register_default_tools()
    await _register_remote_mcp_tools_with_retry()
    yield


app = FastAPI(
    title="Traceable Research Agent",
    version="0.1.0",
    description="Traceable Agent API with planned and optional ReAct execution.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service readiness for the current phase."""

    return HealthResponse(
        status="ok",
        service=settings.service_name,
        phase=settings.phase,
        execution_mode=settings.execution_mode,
        react_enabled=settings.react_enabled,
    )


app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
app.include_router(sessions.router, prefix=settings.api_prefix)
app.include_router(memory.router, prefix=settings.api_prefix)
app.include_router(mcp_server.router)
