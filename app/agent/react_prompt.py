"""Prompt builder for concise ReAct decisions."""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMMessage
from app.mcp.policy import tool_channel
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
        "channel": tool_channel(spec),
        "tool_source": (spec.metadata or {}).get("tool_source", "local"),
        "remote_server": (spec.metadata or {}).get("remote_server"),
    }


def build_react_messages(
    task: str,
    run_id: str,
    allowed_tools: list[str],
    available_tool_specs: list[ToolSpec],
    observation_history: list[dict[str, Any]],
    scenario_template: str | None = None,
    recovery_context: dict[str, Any] | None = None,
    research_context: dict[str, Any] | None = None,
) -> list[LLMMessage]:
    """Build a JSON-only next-action prompt without requesting hidden reasoning."""

    # Build a strict allowed_tools constraint string for injection
    tools_str = ", ".join(allowed_tools) if allowed_tools else "none"
    scenario = str(scenario_template or "standard").strip() or "standard"
    scenario_guidance = ""
    if scenario in {"deep_web_research", "technical_docs_research"}:
        scenario_guidance = (
            " GitHub and remote MCP are optional sources, not mandatory stops in deep Web research. "
            "Use permitted search and page-reading tools to collect auditable source text from "
            "official sites, papers and technical documentation. If one source fails, choose "
            "another available tool or source; never switch real research to mock data."
        )
    system = (
        "You are a traceable research agent. "
        f"CRITICAL: Select a tool ONLY from this exact list: [{tools_str}], or action=finish. "
        "Execution constraints describe disabled, cooling-down or exhausted tools. "
        "Do not repeat unavailable tools or rejected inputs; choose another permitted route. "
        "If no feasible route remains, finish with an explicit limitation summary. "
        "Finishing does not bypass evidence requirements or guarantee a completed research report. "
        "The thought field must contain only a short decision rationale. "
        "Output one strict JSON object only, no Markdown. Required schema: "
        '{"thought":"short rationale","action":"MUST be from allowed list or finish",'
        '"args":{},"finish_reason":null}. '
        "If complete, use action=finish and put a concise answer in args.summary. "
        "Do not invent tools, write files directly, bypass human confirmation, "
        "use SQL writes, or request GitHub writes."
        + scenario_guidance
    )
    payload = {
        "task": task,
        "run_id": run_id,
        "scenario_template": scenario,
        "allowed_tools": allowed_tools,
        "available_tools": [_tool_description(spec) for spec in available_tool_specs
                            if spec.enabled and spec.name in allowed_tools],
        "observation_history": observation_history,
        "execution_constraints": recovery_context or {},
        "research_context": research_context or {},
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
