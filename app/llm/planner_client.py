"""Prompt construction and LLM planner invocation."""

from __future__ import annotations

import json

from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.llm.schema import known_tool_names
from app.tools.registry import get_tool


def _tool_description(name: str) -> str:
    spec = get_tool(name)
    if spec is None:
        return name
    return f"{name}: {spec.description}"


def build_planner_messages(
    task: str,
    allowed_tools: list[str] | None,
    source_mode: str,
) -> list[LLMMessage]:
    """Build strict JSON-only planner messages."""

    allowed = allowed_tools if allowed_tools is not None else sorted(known_tool_names())
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
        "tavily_search": {
            "query": task,
            "max_results": 5,
            "search_depth": "advanced",
            "include_answer": True,
            "include_raw_content": False,
        },
        "report_writer": {},
    }
    for tool_name in allowed:
        tool_defaults.setdefault(tool_name, {})
    known_tool_text = "; ".join(_tool_description(name) for name in allowed)
    system = (
        "You are the Planner for Traceable Research Agent. Output only one JSON object. "
        "Do not output markdown. Do not explain. Do not execute tools. Do not invent tools. "
        "Use only allowed_tools. The plan must be executable by the later Executor. "
        "Use the exact default_arguments unless the user gives a more specific valid value. "
        "For file_reader, prefer a relative filename under workspace/docs; do not prefix "
        "workspace/docs and do not invent filenames unless the user explicitly provides a path. "
        "A path outside configured FILE_READER_ALLOWED_ROOTS will require per-file human "
        "confirmation before execution. Valid workspace/docs examples include "
        "demo_research_note.md, streamlit_demo_notes.md, sql_safety_notes.md, "
        "github_mcp_readonly_notes.md, rag_retrieval_notes.md, react_execution_notes.md, "
        "traceable_agent_architecture.md, and evaluation_notes.md. "
        "For sql_query, the demo SQLite schema is documents(id,title,source,category,created_at) "
        "and metrics(id,name,value,unit); generate only one SELECT or WITH statement over those "
        "columns. For GitHub search, keep query short plain text, repo must be owner/name or null, "
        "search_type must be issues or repositories, and the tool is read-only. "
        "Each step must include step_no, goal, tool_name, arguments, expected_output, "
        "completion_criteria, risk_level, and requires_confirmation. Available known tools: "
        f"{known_tool_text}. "
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
        "tool_boundaries": {
            "file_reader": {
                "default_root": "workspace/docs",
                "allowed_roots_env": "FILE_READER_ALLOWED_ROOTS",
                "outside_allowed_roots_rule": "requires per-file HITL approval",
                "path_rule": "prefer relative existing file path under workspace/docs; preserve explicit user paths for HITL",
            },
            "sql_query": {
                "schema": {
                    "documents": ["id", "title", "source", "category", "created_at"],
                    "metrics": ["id", "name", "value", "unit"],
                },
                "rule": "single read-only SELECT or WITH statement",
            },
            "mcp_github_search": {
                "query_rule": "short plain text query",
                "repo_rule": "owner/name or null",
                "search_type": ["issues", "repositories"],
                "mode": "public_api when source_mode=real, mock when source_mode=mock",
            },
            "tavily_search": {
                "query_rule": "plain text web search query",
                "max_results": "1 to 20",
                "search_depth": ["basic", "advanced"],
            },
        },
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
