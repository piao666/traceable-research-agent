"""LLM provider implementations using OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.config import Settings
from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.llm.errors import classify_http_error, classify_transport_error, retry_delay


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
            metadata={"available": False, "error_type": "provider_unavailable"},
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
            "base_url_configured": bool(self.base_url),
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
        last_metadata: dict[str, Any] = {"available": True, "error_type": "provider_unavailable"}
        for attempt in range(self.max_retries + 1):
            retryable = False
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                content = response_payload["choices"][0]["message"]["content"]
                usage = self.normalize_usage(response_payload.get("usage"))
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
                    usage=usage,
                )
            except HTTPError as exc:
                try:
                    response_body = exc.read(4096).decode("utf-8", errors="replace")
                except Exception:
                    response_body = ""
                info = classify_http_error(exc.code, exc.headers, response_body)
                last_error = info.message
                retryable = info.retryable
                last_metadata = {
                    "available": True,
                    "error_type": info.error_type,
                    "http_status": info.http_status,
                    "retry_after_seconds": info.retry_after_seconds,
                    "retry_count": attempt,
                }
            except (URLError, TimeoutError) as exc:
                reason = exc.reason if isinstance(exc, URLError) and isinstance(exc.reason, BaseException) else exc
                info = classify_transport_error(reason)
                last_error = info.message
                retryable = True
                last_metadata = {
                    "available": True,
                    "error_type": info.error_type,
                    "retry_count": attempt,
                }
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                last_error = "LLM provider returned a malformed response."
                retryable = True
                last_metadata = {
                    "available": True,
                    "error_type": "malformed_response",
                    "retry_count": attempt,
                }
            except Exception:
                last_error = "LLM provider request failed (provider_unavailable)."
                retryable = True
                last_metadata = {
                    "available": True,
                    "error_type": "provider_unavailable",
                    "retry_count": attempt,
                }
            if retryable and attempt < self.max_retries:
                sleep(retry_delay(attempt, last_metadata.get("retry_after_seconds")))
                continue
            break

        return LLMResponse(
            success=False,
            provider=self.provider,
            model=self.model,
            error_message=last_error or f"LLM request failed for {self.provider}.",
            metadata=last_metadata,
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
    if selected not in {"openai_compatible", "deepseek", "qwen"}:
        return UnavailableLLMClient(
            provider=selected,
            model=None,
            reason=f"unknown LLM provider: {selected}",
        )

    provider_config = settings.get_llm_provider_config(selected)
    api_key = settings.get_llm_api_key(selected)
    missing: list[str] = []
    if not api_key:
        missing.append(str(provider_config["api_key_env_name"] or "LLM_API_KEY"))
    base_url = str(provider_config.get("base_url") or "")
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        missing.append("LLM_BASE_URL")
    selected_model = model or provider_config.get("model")
    if not selected_model:
        missing.append("LLM_MODEL")
    if missing:
        return UnavailableLLMClient(
            provider=selected,
            model=selected_model,
            reason=f"{', '.join(dict.fromkeys(missing))} is not configured",
        )

    from app.agent.budget import budget_client
    return budget_client(OpenAICompatibleLLMClient(
        provider=selected,
        model=selected_model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    ))
