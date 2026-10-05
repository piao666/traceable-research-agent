"""Deterministic ReAct decision client used only by eval and smoke scripts."""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMClient, LLMMessage, LLMResponse


class FakeReActLLMClient(LLMClient):
    """Return a fixed decision sequence without network or provider credentials."""

    def __init__(self, decisions: list[dict[str, Any] | str]):
        self._decisions = list(decisions)

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "day34_fake",
            "model": "deterministic-react-policy",
            "available": True,
        }

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        del messages, temperature, max_tokens
        decision: dict[str, Any] | str
        if self._decisions:
            decision = self._decisions.pop(0)
        else:
            decision = {
                "thought": "The deterministic evaluation policy has no remaining actions.",
                "action": "finish",
                "args": {"summary": "Evaluation policy finished."},
                "finish_reason": "policy_complete",
            }
        content = decision if isinstance(decision, str) else json.dumps(decision)
        return LLMResponse(
            success=True,
            content=content,
            provider="day34_fake",
            model="deterministic-react-policy",
        )


def validate_fake_decisions(decisions: list[dict[str, Any] | str]) -> bool:
    """Return whether structured fake decisions have the required action shape."""

    return bool(decisions) and all(
        isinstance(decision, str)
        or (
            isinstance(decision, dict)
            and isinstance(decision.get("action"), str)
            and isinstance(decision.get("args", {}), dict)
        )
        for decision in decisions
    )
