"""FastAPI entrypoint for Traceable Research Agent."""

from fastapi import FastAPI

from app.api import reports, tasks, tools
from app.config import settings
from app.schemas import HealthResponse

app = FastAPI(
    title="Traceable Research Agent",
    version="0.1.0",
    description="Day 1-3 mock API skeleton for a traceable research agent.",
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service readiness for the Day 1-3 skeleton."""

    return HealthResponse(
        status="ok",
        service=settings.service_name,
        phase=settings.phase,
    )


app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
