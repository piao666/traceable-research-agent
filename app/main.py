"""FastAPI entrypoint for Traceable Research Agent."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import reports, tasks, tools
from app.config import settings
from app.database import init_db
from app.schemas import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize local SQLite tables when the app starts."""

    init_db()
    yield


app = FastAPI(
    title="Traceable Research Agent",
    version="0.1.0",
    description="Day 4 API skeleton with SQLite-backed task records.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service readiness for the Day 4 skeleton."""

    return HealthResponse(
        status="ok",
        service=settings.service_name,
        phase=settings.phase,
    )


app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
