"""FastAPI entrypoint for Traceable Research Agent."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import reports, tasks, tools
from app.config import settings
from app.database import init_db
from app.schemas import HealthResponse
from app.tools.defaults import register_default_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize local SQLite tables and default tool metadata."""

    init_db()
    register_default_tools()
    yield


app = FastAPI(
    title="Traceable Research Agent",
    version="0.1.0",
    description="Day 15 API with manual e2e flow, exception visibility, and minimal HITL.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service readiness for the current phase."""

    return HealthResponse(
        status="ok",
        service=settings.service_name,
        phase=settings.phase,
    )


app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
