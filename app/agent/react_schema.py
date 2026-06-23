"""Structured ReAct decisions and safe JSON parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field


FINISH_ACTIONS = {"finish", "done", "complete", "completed", "stop"}


class ReActDecision(BaseModel):
    thought: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None


class ReActStepObservation(BaseModel):
    step_no: int
    thought: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    observation_summary: str
    success: bool
    error_message: str | None = None
    tool_result_metadata: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None
    output: Any | None = None


class ReActDecisionError(ValueError):
    """Stable validation error consumed by the executor."""

    def __init__(self, message: str, error_type: str = "invalid_decision"):
        super().__init__(message)
        self.error_type = error_type


def extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Extract the first complete JSON object from plain, fenced, or mixed text."""

    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    stripped = raw_text.strip()
    candidates = [stripped]
    fenced = re.findall(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates = fenced + candidates
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def normalize_action(action: str) -> str:
    normalized = str(action or "").strip().lower().replace("-", "_")
    return "finish" if normalized in FINISH_ACTIONS else normalized


def is_finish_action(action: str) -> bool:
    return normalize_action(action) == "finish"


def validate_react_decision(
    raw: dict[str, Any],
    allowed_tools: list[str],
    available_tools: list[str],
) -> ReActDecision:
    """Normalize a decision and reject unknown, disabled, or disallowed actions."""

    if not isinstance(raw, dict):
        raise ReActDecisionError("ReAct decision must be a JSON object.")
    action = normalize_action(str(raw.get("action") or ""))
    if not action:
        raise ReActDecisionError("ReAct decision is missing action.")
    allowed = set(allowed_tools)
    available = set(available_tools)
    if not is_finish_action(action):
        if action not in allowed:
            raise ReActDecisionError(
                f"Action '{action}' is not in allowed_tools.",
                "disallowed_tool",
            )
        if action not in available:
            raise ReActDecisionError(
                f"Action '{action}' is not an enabled registered tool.",
                "unknown_tool",
            )
    args = raw.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ReActDecisionError("ReAct decision args must be an object.", "invalid_args")
    thought = str(raw.get("thought") or "Selected the next safe action.").strip()
    finish_reason = raw.get("finish_reason")
    if finish_reason is not None:
        finish_reason = str(finish_reason).strip() or None
    return ReActDecision(
        thought=thought[:500],
        action=action,
        args=args,
        finish_reason=finish_reason,
    )