"""Smoke checks for planner argument guardrails."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.plan_guardrails import normalize_plan_arguments
from app.tools.file_reader import read_file
from app.tools.mcp_github import github_search
from app.tools.sql_query import run_query
from scripts.init_demo_db import init_demo_db


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    init_demo_db()
    task = (
        "Read Streamlit docs, query metric_name from database, search GitHub issues "
        "for a long free-form audit question, and generate a markdown report"
    )
    plan = {
        "version": "llm-v1",
        "task": task,
        "source_mode": "mock",
        "allowed_tools": ["file_reader", "sql_query", "mcp_github_search", "rag_search", "report_writer"],
        "steps": [
            {
                "step_no": 1,
                "tool_name": "file_reader",
                "arguments": {"path": "workspace/docs/streamlit_demo_notes.md", "max_chars": 999999},
            },
            {
                "step_no": 2,
                "tool_name": "sql_query",
                "arguments": {"query": "SELECT metric_name, value FROM metrics", "limit": "500"},
            },
            {
                "step_no": 3,
                "tool_name": "mcp_github_search",
                "arguments": {
                    "query": "traceable research agent\n" * 30,
                    "repo": "not a valid repo",
                    "limit": 999,
                    "mode": "public_api",
                    "search_type": "issue_search",
                    "sort": "stars",
                },
            },
            {
                "step_no": 4,
                "tool_name": "rag_search",
                "arguments": {"query": "", "top_k": 999, "retrieval_mode": "invalid"},
            },
            {"step_no": 5, "tool_name": "report_writer", "arguments": {"path": "bad.md"}},
        ],
        "notes": [],
        "confirmation": None,
    }

    normalized = normalize_plan_arguments(plan, task, "mock")
    steps = {step["tool_name"]: step for step in normalized["steps"]}

    file_args = steps["file_reader"]["arguments"]
    _assert(file_args["path"] == "streamlit_demo_notes.md", f"bad file path: {file_args}")
    _assert(file_args["max_chars"] == 20000, f"bad file max_chars: {file_args}")
    file_result = read_file(file_args)
    _assert(file_result.success, file_result.error_message or "file_reader failed")

    sql_args = steps["sql_query"]["arguments"]
    _assert(sql_args["query"] == "SELECT id, name, value, unit FROM metrics", f"bad sql query: {sql_args}")
    _assert(sql_args["limit"] == 100, f"bad sql limit: {sql_args}")
    sql_result = run_query(sql_args)
    _assert(sql_result.success, sql_result.error_message or "sql_query failed")

    github_args = steps["mcp_github_search"]["arguments"]
    _assert(github_args["repo"] is None, f"bad github repo: {github_args}")
    _assert(github_args["mode"] == "mock", f"bad github mode: {github_args}")
    _assert(github_args["search_type"] == "issues", f"bad github search_type: {github_args}")
    _assert(github_args["limit"] == 10, f"bad github limit: {github_args}")
    _assert(len(github_args["query"]) <= 120, f"github query too long: {github_args}")
    github_result = github_search(github_args)
    _assert(github_result.success, github_result.error_message or "github_search failed")

    rag_args = steps["rag_search"]["arguments"]
    _assert(rag_args["query"] == task, f"bad rag query: {rag_args}")
    _assert(rag_args["top_k"] == 10, f"bad rag top_k: {rag_args}")
    _assert(rag_args["retrieval_mode"] in {"dense", "bm25", "hybrid"}, f"bad rag mode: {rag_args}")
    _assert(steps["report_writer"]["arguments"] == {}, "report_writer arguments were not cleared")

    notes = "\n".join(normalized.get("notes") or [])
    _assert("Planner guardrail" in notes, "guardrail notes missing")
    _assert("api_key" not in notes.lower() and "token" not in notes.lower(), "secret-like terms leaked into notes")

    print(
        json.dumps(
            {
                "planner_guardrails": "ok",
                "file_path": file_args["path"],
                "sql_query": sql_args["query"],
                "github_query_length": len(github_args["query"]),
                "notes": normalized.get("notes"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
