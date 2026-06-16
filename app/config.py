"""Application configuration for the Day 1-3 FastAPI skeleton."""

from pydantic import BaseModel


class Settings(BaseModel):
    """Small settings object; expanded later for database and model providers."""

    service_name: str = "traceable-research-agent"
    phase: str = "day1-3"
    api_prefix: str = "/api"


settings = Settings()
