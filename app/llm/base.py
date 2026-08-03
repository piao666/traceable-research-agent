"""Base structures for optional LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    """One chat message passed to an LLM provider."""

    role: str
    content: str


class LLMUsage(BaseModel):
    """Token usage statistics from an LLM provider response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """Provider response shape that never exposes API keys."""

    success: bool
    content: str | None = None
    provider: str
    model: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    usage: LLMUsage | None = None


class LLMClient(ABC):
    """Small provider interface independent of FastAPI and database state."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether the client can make a provider call."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Return non-secret client metadata."""

    @abstractmethod
    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Complete a chat request."""
