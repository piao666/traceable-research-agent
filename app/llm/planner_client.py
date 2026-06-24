"""Prompt construction and LLM planner invocation."""

from __future__ import annotations

import json

from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.llm.schema import KNOWN_TOOLS


def build_planner_messages(
    task: str,
    allowed_tools: list[str] | None,
    source_mode: str,
) -> list[LLMMessage]:
    """Build strict JSON-only planner messages."""

    allowed = allowed_tools if allowed_tools is not None else sorted(KNOWN_TOOLS)
    tool_defaults = {
        "file_reader": {"path": "demo_research_note.md", "max_chars": 4000},
        "sql_query": {"query": "SELECT id, title, category FROM documents", "limit": 5},
        "rag_search": {"query": task, "top_k": 3},
        "mcp_github_search": {
            "query": task,
            "repo": "piao666/traceable-research-agent",
            "limit": 5,
            "mode": "public_api" if source_mode == "real" else "mock",
        },
        "report_writer": {},
    }
    system = (
        "You are the Planner for Traceable Research Agent. Output only one JSON object. "
        "Do not output markdown. Do not explain. Do not execute tools. Do not invent tools. "
        "Use only allowed_tools. The plan must be executable by the later Executor. "
        "Each step must include step_no, goal, tool_name, arguments, expected_output, "
        "completion_criteria, risk_level, and requires_confirmation. Known tools: "
        "file_reader reads workspace/docs demo files; sql_query runs read-only SQL; "
        "rag_search performs local RAG retrieval; mcp_github_search performs read-only "
        "GitHub mock/API search; report_writer creates the final Markdown report. "
        "IMPORTANT: Write the 'goal', 'completion_criteria', 'expected_output', and 'notes' "
        "fields in Simplified Chinese (简体中文). Keep JSON keys and tool names in English. "
        f"The source_mode is '{source_mode}'. If source_mode is 'real', the notes MUST say "
        "'工具将通过真实 API 访问外部数据' and NOT mention mock or simulation. "
        "If source_mode is 'mock', the notes should say '工具使用本地离线数据（mock模式）'."    )
    user = {
        "task": task,
        "source_mode": source_mode,
        "allowed_tools": allowed,
        "required_top_level_fields": [
            "version",
            "task",
            "source_mode",
            "allowed_tools",
            "steps",
            "notes",
            "confirmation",
        ],
        "default_arguments": tool_defaults,
    }
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=json.dumps(user, ensure_ascii=False)),
    ]


def call_llm_for_plan(
    client: LLMClient,
    task: str,
    allowed_tools: list[str] | None,
    source_mode: str,
) -> LLMResponse:
    """Call an available LLM client for planning."""

    if not client.is_available():
        description = client.describe()
        return LLMResponse(
            success=False,
            provider=str(description.get("provider") or "unknown"),
            model=description.get("model"),
            error_message=str(description.get("reason") or "LLM client unavailable."),
            metadata={"available": False, "error_type": "unavailable"},
        )
    messages = build_planner_messages(task, allowed_tools, source_mode)
    return client.complete(messages, temperature=0.0, max_tokens=2000)