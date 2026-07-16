"""Resolve report synthesis dependencies from explicit runtime policy."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMClient
from app.llm.providers import create_llm_client


def resolve_report_llm_client(
    settings: Settings,
    candidate: LLMClient | None = None,
) -> LLMClient | None:
    """Return an LLM client only when report synthesis is explicitly enabled."""

    if settings.report_generation_mode != "llm":
        return None
    return candidate or create_llm_client(settings)
