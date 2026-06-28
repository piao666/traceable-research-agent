"""Strict plan JSON extraction and normalization."""

from __future__ import annotations

import json
import re
from typing import Any


KNOWN_TOOLS = {
    "file_reader",
    "sql_query",
    "rag_search",
    "mcp_github_search",
    "tavily_search",
    "report_writer",
}
VALID_RISK_LEVELS = {"low", "medium", "high"}


def known_tool_names() -> set[str]:
    """Return local constants plus any dynamically registered remote tools."""

    try:
        from app.tools.registry import list_tools

        registered = {spec.name for spec in list_tools()}
    except Exception:
        registered = set()
    return set(KNOWN_TOOLS) | registered


def extract_json_object(text: str) -> dict | None:
    """Extract a JSON object from raw LLM text."""

    stripped = text.strip()
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def validate_and_normalize_plan(
    raw_plan: dict,
    task: str,
    allowed_tools: list[str] | None,
    source_mode: str,
) -> tuple[bool, dict | None, list[str]]:
    """Validate and normalize a raw plan object."""

    notes: list[str] = []
    if not isinstance(raw_plan, dict):
        return False, None, ["LLM plan is not a JSON object."]

    known_tools = known_tool_names()
    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    raw_steps = raw_plan.get("steps", [])
    if not isinstance(raw_steps, list):
        return False, None, ["LLM plan steps must be a list."]

    normalized_steps = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            notes.append("Skipped non-object step from LLM plan.")
            continue
        tool_name = str(raw_step.get("tool_name") or "").strip()
        if tool_name not in known_tools:
            notes.append(f"Skipped unknown tool from LLM plan: {tool_name or '<missing>'}.")
            continue
        if allowed_set is not None and tool_name not in allowed_set:
            notes.append(f"Skipped {tool_name}: not included in allowed_tools.")
            continue
        arguments = raw_step.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
            notes.append(f"Corrected non-object arguments for {tool_name}.")
        risk_level = str(raw_step.get("risk_level") or "medium").lower()
        if risk_level not in VALID_RISK_LEVELS:
            risk_level = "medium"
            notes.append(f"Corrected invalid risk_level for {tool_name}.")

        normalized_steps.append(
            {
                "step_no": len(normalized_steps) + 1,
                "goal": str(raw_step.get("goal") or f"Run {tool_name}."),
                "tool_name": tool_name,
                "arguments": arguments,
                "expected_output": str(raw_step.get("expected_output") or "Tool output."),
                "completion_criteria": str(
                    raw_step.get("completion_criteria") or "Step completes without system error."
                ),
                "risk_level": risk_level,
                "requires_confirmation": bool(raw_step.get("requires_confirmation", False)),
            }
        )

    raw_notes = raw_plan.get("notes", [])
    if isinstance(raw_notes, list):
        notes = [str(note) for note in raw_notes] + notes
    elif raw_notes:
        notes.insert(0, str(raw_notes))

    if not normalized_steps:
        notes.append("No executable planning step after schema validation and tool filtering.")

    normalized_plan = {
        "version": str(raw_plan.get("version") or "llm-v1"),
        "task": task.strip(),
        "source_mode": source_mode,
        "allowed_tools": allowed_tools if allowed_tools is not None else sorted(known_tools),
        "steps": normalized_steps,
        "notes": notes,
        "confirmation": raw_plan.get("confirmation") if isinstance(raw_plan.get("confirmation"), dict) else None,
    }
    return True, normalized_plan, notes
