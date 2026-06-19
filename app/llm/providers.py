"""LLM provider implementations using OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings
from app.llm.base import LLMClient, LLMMessage, LLMResponse


class UnavailableLLMClient(LLMClient):
    """Client used when a provider is disabled or unavailable."""

    def __init__(self, provider: str, model: str | None, reason: str):
        self.provider = provider
        self.model = model
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "available": False,
            "reason": self.reason,
        }

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        return LLMResponse(
            success=False,
            provider=self.provider,
            model=self.model,
            error_message=self.reason,
            metadata={"available": False, "error_type": "unavailable"},
        )


class OpenAICompatibleLLMClient(LLMClient):
    """Minimal OpenAI-compatible chat client implemented with urllib."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 20,
        max_retries: int = 1,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "available": self.is_available(),
        }

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                content = response_payload["choices"][0]["message"]["content"]
                return LLMResponse(
                    success=True,
                    content=str(content),
                    provider=self.provider,
                    model=self.model,
                    metadata={
                        "attempt": attempt + 1,
                        "available": True,
                        "finish_reason": response_payload["choices"][0].get("finish_reason"),
                    },
                )
            except HTTPError as exc:
                last_error = f"HTTP error from {self.provider}: {exc.code}"
            except (URLError, TimeoutError) as exc:
                last_error = f"Network error from {self.provider}: {exc}"
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                last_error = f"Invalid response from {self.provider}: {exc}"
            except Exception as exc:
                last_error = f"LLM request failed for {self.provider}: {exc}"
            if attempt < self.max_retries:
                sleep(0.2)

        return LLMResponse(
            success=False,
            provider=self.provider,
            model=self.model,
            error_message=last_error or f"LLM request failed for {self.provider}.",
            metadata={"available": True, "error_type": "provider_error"},
        )


def create_llm_client(
    settings: Settings,
    provider: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """Create a non-secret LLM client for the selected provider."""

    selected = (provider or settings.llm_provider or "qwen").lower()
    if selected == "deterministic":
        return UnavailableLLMClient(
            provider="deterministic",
            model=None,
            reason="deterministic planner selected; no external LLM required",
        )
    if selected not in {"deepseek", "qwen"}:
        return UnavailableLLMClient(
            provider=selected,
            model=None,
            reason=f"unknown LLM provider: {selected}",
        )

    provider_config = settings.get_llm_provider_config(selected)
    api_key = settings.get_llm_api_key(selected)
    if not api_key:
        return UnavailableLLMClient(
            provider=selected,
            model=model or provider_config["model"],
            reason=f"{provider_config['api_key_env_name']} is not configured",
        )

    return OpenAICompatibleLLMClient(
        provider=selected,
        model=model or provider_config["model"],
        base_url=provider_config["base_url"],
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
