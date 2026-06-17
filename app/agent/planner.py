"""Deterministic JSON planner for task runs."""

from __future__ import annotations

from typing import Any


DEFAULT_TOOL_ORDER = ["file_reader", "sql_query", "rag_search", "report_writer"]

FILE_KEYWORDS = {
    "file",
    "document",
    "doc",
    "markdown",
    "local docs",
    "docs",
    "\u6587\u4ef6",
    "\u6587\u6863",
    "\u8bfb\u53d6",
}
SQL_KEYWORDS = {
    "sql",
    "database",
    "db",
    "table",
    "rows",
    "query",
    "metrics",
    "\u6570\u636e\u5e93",
    "\u67e5\u8be2",
    "\u8868",
}
RAG_KEYWORDS = {
    "rag",
    "retrieve",
    "retrieval",
    "search",
    "chunks",
    "evidence",
    "trace",
    "registry",
    "\u68c0\u7d22",
    "\u8bc1\u636e",
}
REPORT_KEYWORDS = {
    "report",
    "summary",
    "summarize",
    "markdown",
    "\u62a5\u544a",
    "\u603b\u7ed3",
}
HUMAN_CONFIRM_KEYWORDS = {
    "human approval",
    "human confirm",
    "requires confirmation",
    "\u4eba\u5de5\u786e\u8ba4",
    "\u4eba\u5de5\u5ba1\u6279",
}


def _matches(task_lower: str, keywords: set[str]) -> bool:
    return any(keyword.lower() in task_lower for keyword in keywords)


def _step_template(
    tool_name: str,
    task: str,
    requires_human_confirmation: bool = False,
) -> dict[str, Any]:
    templates: dict[str, dict[str, Any]] = {
        "file_reader": {
            "goal": "Read the local demo research note from the approved docs workspace.",
            "arguments": {"path": "demo_research_note.md", "max_chars": 4000},
            "expected_output": "Local document content and a concise read summary.",
            "completion_criteria": "The file is read from workspace/docs without path safety violations.",
            "risk_level": "low",
            "requires_confirmation": False,
        },
        "sql_query": {
            "goal": "Query the local demo database for document metadata.",
            "arguments": {"query": "SELECT id, title, category FROM documents", "limit": 5},
            "expected_output": "Read-only SQL rows from the demo documents table.",
            "completion_criteria": "The query returns columns and up to five rows without unsafe SQL.",
            "risk_level": "medium",
            "requires_confirmation": False,
        },
        "rag_search": {
            "goal": "Retrieve relevant chunks from the local RAG index.",
            "arguments": {"query": task, "top_k": 3},
            "expected_output": "Top-k chunks with source, chunk id, score, and text.",
            "completion_criteria": "The local index returns relevant chunks or a stable empty result.",
            "risk_level": "low",
            "requires_confirmation": False,
        },
        "report_writer": {
            "goal": "Generate a Markdown report from the plan, observations, and trace summaries.",
            "arguments": {},
            "expected_output": "A saved Markdown report under workspace/reports.",
            "completion_criteria": "The report includes task, plan, evidence, trace summary, and limitations.",
            "risk_level": "low",
            "requires_confirmation": False,
        },
    }
    step = templates[tool_name].copy()
    if tool_name == "report_writer" and requires_human_confirmation:
        step["risk_level"] = "high"
        step["requires_confirmation"] = True
        step["completion_criteria"] = (
            "Human confirmation is recorded before the final Markdown report is generated."
        )
    return step


def _append_step(
    steps: list[dict[str, Any]],
    notes: list[str],
    tool_name: str,
    task: str,
    allowed_set: set[str] | None,
    requires_human_confirmation: bool = False,
) -> None:
    if any(step["tool_name"] == tool_name for step in steps):
        return
    if allowed_set is not None and tool_name not in allowed_set:
        notes.append(f"Skipped {tool_name}: not included in allowed_tools.")
        return
    step = _step_template(tool_name, task, requires_human_confirmation)
    step["step_no"] = len(steps) + 1
    step["tool_name"] = tool_name
    steps.append(step)


def plan_task(
    task: str,
    allowed_tools: list[str] | None = None,
    source_mode: str = "mock",
) -> dict[str, Any]:
    """Create a stable deterministic plan from task keywords.

    The same inputs produce the same JSON-compatible output. No LLM, network, or
    tool execution path is used here.
    """

    task_text = task.strip()
    task_lower = task_text.lower()
    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    requires_human_confirmation = _matches(task_lower, HUMAN_CONFIRM_KEYWORDS)
    notes: list[str] = []
    steps: list[dict[str, Any]] = []

    if _matches(task_lower, FILE_KEYWORDS):
        _append_step(steps, notes, "file_reader", task_text, allowed_set)
    if _matches(task_lower, SQL_KEYWORDS):
        _append_step(steps, notes, "sql_query", task_text, allowed_set)
    if _matches(task_lower, RAG_KEYWORDS):
        _append_step(steps, notes, "rag_search", task_text, allowed_set)
    if _matches(task_lower, REPORT_KEYWORDS):
        _append_step(
            steps,
            notes,
            "report_writer",
            task_text,
            allowed_set,
            requires_human_confirmation,
        )

    if not steps and not notes:
        _append_step(steps, notes, "rag_search", task_text, allowed_set)
        _append_step(
            steps,
            notes,
            "report_writer",
            task_text,
            allowed_set,
            requires_human_confirmation,
        )

    if not steps:
        notes.append("No executable planning step due to allowed_tools restriction.")

    for index, step in enumerate(steps, start=1):
        step["step_no"] = index

    return {
        "version": "deterministic-v1",
        "task": task_text,
        "source_mode": source_mode,
        "allowed_tools": allowed_tools if allowed_tools is not None else DEFAULT_TOOL_ORDER.copy(),
        "steps": steps,
        "notes": notes,
        "confirmation": None,
    }
