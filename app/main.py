"""FastAPI entrypoint for Traceable Research Agent."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import events, reports, tasks, tools
from app.config import settings
from app.database import init_db
from app.mcp import server as mcp_server
from app.mcp.client import register_remote_mcp_tools_from_settings
from app.schemas import HealthResponse
from app.tools.defaults import register_default_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize local SQLite tables and default tool metadata."""

    init_db()
    register_default_tools()
    register_remote_mcp_tools_from_settings(settings)
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
app.include_router(mcp_server.router)
