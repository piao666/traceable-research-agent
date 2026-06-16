"""Application configuration for the FastAPI skeleton."""

from pydantic import BaseModel


class Settings(BaseModel):
    """Small settings object; expanded later for database and model providers."""

    service_name: str = "traceable-research-agent"
    phase: str = "day4"
    api_prefix: str = "/api"


settings = Settings()
