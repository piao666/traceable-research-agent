"""Prompt builder for concise ReAct decisions."""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMMessage
from app.tools.base import ToolSpec


def _tool_description(spec: ToolSpec) -> dict[str, Any]:
    description = spec.description
    if spec.name == "sql_query":
        description += " Only one read-only SELECT or WITH statement is allowed."
    if spec.name == "mcp_github_search":
        description += " GitHub access is GET-only and write operations are forbidden."
    if spec.name == "report_writer":
        description += " Human confirmation remains mandatory when the persisted plan requires it."
    return {
        "name": spec.name,
        "description": description,
        "input_schema": spec.input_schema,
        "risk_level": spec.risk_level.value,
        "requires_confirmation": spec.requires_confirmation,
    }


def build_react_messages(
    task: str,
    run_id: str,
    allowed_tools: list[str],
    available_tool_specs: list[ToolSpec],
    observation_history: list[dict[str, Any]],
) -> list[LLMMessage]:
    """Build a JSON-only next-action prompt without requesting hidden reasoning."""

    system = (
        "You are a traceable research agent. Choose one safe next action from the "
        "allowed tools based on the task and previous observations. The thought field "
        "must contain only a short decision rationale, not a detailed chain of thought. "
        "Output one strict JSON object only, with no Markdown. Required schema: "
        '{"thought":"short rationale","action":"tool name or finish",'
        '"args":{},"finish_reason":null}. '
        "If complete, use action=finish and put a concise summary in args.summary. "
        "If a tool failed or returned no evidence, choose another useful allowed tool or "
        "finish with a limitation. Do not invent tools, write files directly, bypass human "
        "confirmation, use SQL writes, or request GitHub writes."
    )
    payload = {
        "task": task,
        "run_id": run_id,
        "allowed_tools": allowed_tools,
        "available_tools": [_tool_description(spec) for spec in available_tool_specs],
        "observation_history": observation_history,
        "safety_boundaries": [
            "Only allowed and enabled registered tools may be selected.",
            "SQL is limited to a single SELECT/WITH statement.",
            "File reads remain inside workspace/docs.",
            "GitHub operations are read-only.",
            "Human confirmation cannot be bypassed.",
        ],
    }
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
