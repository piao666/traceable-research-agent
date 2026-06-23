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

    # Build a strict allowed_tools constraint string for injection
    tools_str = ", ".join(allowed_tools) if allowed_tools else "none"
    system = (
        "You are a traceable research agent. "
        f"CRITICAL: You MUST select your action ONLY from this exact list: [{tools_str}]. "
        "Choosing ANY other tool name (e.g. tavily_search, web_search, browser) is a fatal "
        "error and will abort the task. If none of the allowed tools can answer the question, "
        "use action=finish immediately and put your best answer in args.summary. "
        "For general knowledge questions (e.g. 'what is X'), prefer action=finish with a "
        "direct answer rather than forcing an inappropriate tool call. "
        "The thought field must contain only a short decision rationale. "
        "Output one strict JSON object only, no Markdown. Required schema: "
        '{"thought":"short rationale","action":"MUST be from allowed list or finish",'
        '"args":{},"finish_reason":null}. '
        "If complete, use action=finish and put a concise answer in args.summary. "
        "Do not invent tools, write files directly, bypass human confirmation, "
        "use SQL writes, or request GitHub writes."
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