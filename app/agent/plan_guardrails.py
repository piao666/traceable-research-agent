"""Normalize planned tool arguments to local executable boundaries."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from app.agent.file_access_policy import (
    CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS,
    DOCS_ROOT,
    confirmation_details_for_path,
    find_allowed_root,
    resolve_file_reader_path,
)
from app.config import settings
from app.tools.file_reader import DEFAULT_MAX_CHARS, MAX_CHARS_LIMIT
from app.tools.mcp_github import MAX_LIMIT as GITHUB_MAX_LIMIT
from app.tools.mcp_github import REPO_PATTERN
from app.tools.sql_query import DEFAULT_DB_PATH
from app.tools.sql_safety import validate_read_only_sql


DEFAULT_FILE_PATH = "demo_research_note.md"
DEFAULT_DOCUMENT_QUERY = "SELECT id, title, source, category, created_at FROM documents"
DEFAULT_METRICS_QUERY = "SELECT id, name, value, unit FROM metrics"
DEFAULT_GITHUB_QUERY = "traceable research agent"
GITHUB_QUERY_MAX_CHARS = 120


def normalize_plan_arguments(
    plan: dict[str, Any],
    task: str,
    source_mode: str,
) -> dict[str, Any]:
    """Return a plan whose local tool arguments are safe and likely executable."""

    notes: list[str] = [str(note) for note in plan.get("notes") or []]
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name") or "")
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
            step["arguments"] = arguments

        if tool_name == "file_reader":
            _normalize_file_reader(step, arguments, task, notes)
        elif tool_name == "sql_query":
            _normalize_sql_query(arguments, task, notes)
        elif tool_name == "mcp_github_search":
            _normalize_github_search(arguments, task, source_mode, notes)
        elif tool_name == "rag_search":
            _normalize_rag_search(arguments, task, notes)
        elif tool_name == "tavily_search":
            _normalize_tavily_search(arguments, task, notes)
        elif tool_name == "report_writer":
            step["arguments"] = {}
    plan["notes"] = _dedupe_notes(notes)
    return plan


def _dedupe_notes(notes: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for note in notes:
        text = str(note).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _candidate_file_path(raw_path: str) -> str:
    normalized = raw_path.strip().replace("\\", "/")
    for prefix in ("workspace/docs/", "./workspace/docs/", "docs/", "./docs/"):
        if normalized.lower().startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _resolve_docs_relative(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    candidate = _candidate_file_path(raw_path)
    path = Path(candidate)
    resolved = (DOCS_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(DOCS_ROOT)
    except ValueError:
        return None
    if resolved.exists() and resolved.is_file():
        return resolved
    return None


def _score_doc(path: Path, text: str) -> int:
    haystack = f"{path.stem} {path.name}".replace("_", " ").replace("-", " ").lower()
    words = {word for word in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(word) >= 3}
    return sum(1 for word in words if word in haystack)


def _best_local_doc(task: str) -> Path:
    files = [path for path in DOCS_ROOT.glob("*") if path.is_file()]
    if not files:
        return DOCS_ROOT / DEFAULT_FILE_PATH
    preferred = DOCS_ROOT / DEFAULT_FILE_PATH
    ranked = sorted(files, key=lambda path: (_score_doc(path, task), path.name), reverse=True)
    if ranked and _score_doc(ranked[0], task) > 0:
        return ranked[0]
    return preferred if preferred.exists() else sorted(files, key=lambda path: path.name)[0]


def _docs_relative_path(path: Path) -> str:
    return path.relative_to(DOCS_ROOT).as_posix()


def _allowed_path_argument(path: Path) -> str:
    allowed_root = find_allowed_root(path)
    if allowed_root == DOCS_ROOT:
        return _docs_relative_path(path)
    return str(path)


def _normalize_file_reader(
    step: dict[str, Any],
    arguments: dict[str, Any],
    task: str,
    notes: list[str],
) -> None:
    original = str(arguments.get("path") or "").strip()
    if not original:
        fallback = _best_local_doc(task)
        arguments["path"] = _docs_relative_path(fallback)
        notes.append(
            "Planner guardrail normalized file_reader.path to an existing file under workspace/docs."
        )
    else:
        resolved = resolve_file_reader_path(original)
        allowed_root = find_allowed_root(resolved)
        if allowed_root is None:
            details = confirmation_details_for_path(original)
            arguments["path"] = original
            step["risk_level"] = "high"
            step["requires_confirmation"] = bool(details["requires_confirmation"])
            step["confirmation_reason"] = CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS
            step["confirmation_details"] = details
            step["completion_criteria"] = (
                "Human confirmation must approve this exact file path before file_reader reads it."
            )
            notes.append(
                "Planner guardrail marked file_reader.path for HITL because it is outside configured allowed roots."
            )
        elif resolved.exists() and resolved.is_file():
            normalized = _allowed_path_argument(resolved)
            if normalized != original:
                notes.append("Planner guardrail normalized file_reader.path inside configured allowed roots.")
            arguments["path"] = normalized
            step.pop("confirmation_reason", None)
            step.pop("confirmation_details", None)
            if step.get("requires_confirmation") and step.get("tool_name") == "file_reader":
                step["requires_confirmation"] = False
        else:
            fallback = _best_local_doc(task)
            arguments["path"] = _docs_relative_path(fallback)
            step.pop("confirmation_reason", None)
            step.pop("confirmation_details", None)
            if step.get("requires_confirmation") and step.get("tool_name") == "file_reader":
                step["requires_confirmation"] = False
            notes.append(
                "Planner guardrail normalized missing file_reader.path to an existing file under workspace/docs."
            )
    arguments["max_chars"] = _bounded_int(
        arguments.get("max_chars"),
        DEFAULT_MAX_CHARS,
        1,
        MAX_CHARS_LIMIT,
    )


def _query_is_executable(query: str) -> bool:
    if not DEFAULT_DB_PATH.exists():
        return True
    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as conn:
            conn.execute(f"EXPLAIN QUERY PLAN {query.strip().rstrip(';')}")
        return True
    except sqlite3.Error:
        return False


def _choose_safe_sql(task: str, query: str) -> str:
    text = f"{task} {query}".lower()
    if "metric" in text or "metrics" in text or "指标" in text:
        return DEFAULT_METRICS_QUERY
    return DEFAULT_DOCUMENT_QUERY


def _normalize_sql_query(arguments: dict[str, Any], task: str, notes: list[str]) -> None:
    original = str(arguments.get("query") or "").strip()
    limit = _bounded_int(arguments.get("limit"), 5, 1, 100)
    read_only, _, parser_metadata = validate_read_only_sql(original)
    normalized = str(parser_metadata.get("normalized_sql") or original).strip()
    if not original or not read_only or not _query_is_executable(normalized):
        arguments["query"] = _choose_safe_sql(task, original)
        notes.append(
            "Planner guardrail replaced sql_query.query with a schema-valid read-only demo query."
        )
    else:
        arguments["query"] = normalized
    arguments["limit"] = limit


def _clean_github_query(value: str) -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", value)
    cleaned = re.sub(r"[^\w\s./:#@+-]", " ", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > GITHUB_QUERY_MAX_CHARS:
        cleaned = cleaned[:GITHUB_QUERY_MAX_CHARS].rsplit(" ", 1)[0].strip()
    return cleaned or DEFAULT_GITHUB_QUERY


def _normalize_github_search(
    arguments: dict[str, Any],
    task: str,
    source_mode: str,
    notes: list[str],
) -> None:
    original_query = str(arguments.get("query") or task or DEFAULT_GITHUB_QUERY)
    query = _clean_github_query(original_query)
    if query != original_query.strip():
        notes.append("Planner guardrail shortened mcp_github_search.query for GitHub Search API.")
    arguments["query"] = query

    repo = arguments.get("repo")
    if repo is None or repo == "":
        arguments["repo"] = None
    elif not isinstance(repo, str) or not REPO_PATTERN.fullmatch(repo.strip()):
        arguments["repo"] = None
        notes.append("Planner guardrail removed invalid mcp_github_search.repo.")
    else:
        arguments["repo"] = repo.strip()

    mode = str(arguments.get("mode") or "").strip().lower()
    use_mock = str(source_mode or "real").strip().lower() in {"mock", "offline"} or settings.offline_mode
    arguments["mode"] = "mock" if use_mock else "public_api"
    if mode and mode != arguments["mode"]:
        notes.append("Planner guardrail aligned mcp_github_search.mode with source_mode.")

    search_type = str(arguments.get("search_type") or "issues").strip().lower()
    if search_type == "repository":
        search_type = "repositories"
    if search_type not in {"issues", "repositories"}:
        search_type = "issues"
        notes.append("Planner guardrail reset invalid mcp_github_search.search_type.")
    arguments["search_type"] = search_type

    sort = str(arguments.get("sort") or "best_match").strip().lower()
    if sort not in {"best_match", "stars", "updated"}:
        sort = "best_match"
    if search_type == "issues" and sort == "stars":
        sort = "best_match"
    arguments["sort"] = sort

    order = str(arguments.get("order") or "desc").strip().lower()
    arguments["order"] = order if order in {"asc", "desc"} else "desc"
    arguments["limit"] = _bounded_int(arguments.get("limit"), 5, 1, GITHUB_MAX_LIMIT)


def _normalize_rag_search(arguments: dict[str, Any], task: str, notes: list[str]) -> None:
    if not str(arguments.get("query") or "").strip():
        arguments["query"] = task
        notes.append("Planner guardrail filled missing rag_search.query from task.")
    arguments["top_k"] = _bounded_int(arguments.get("top_k"), 3, 1, 10)
    retrieval_mode = str(arguments.get("retrieval_mode") or settings.rag_retrieval_mode).strip().lower()
    arguments["retrieval_mode"] = retrieval_mode if retrieval_mode in {"dense", "bm25", "hybrid"} else settings.rag_retrieval_mode


def _normalize_tavily_search(arguments: dict[str, Any], task: str, notes: list[str]) -> None:
    if not str(arguments.get("query") or "").strip():
        arguments["query"] = task
        notes.append("Planner guardrail filled missing tavily_search.query from task.")
    arguments["max_results"] = _bounded_int(
        arguments.get("max_results"),
        settings.tavily_default_max_results,
        1,
        20,
    )
    search_depth = str(arguments.get("search_depth") or "advanced").strip().lower()
    arguments["search_depth"] = search_depth if search_depth in {"basic", "advanced"} else "advanced"
    arguments["include_answer"] = bool(arguments.get("include_answer", True))
    arguments["include_raw_content"] = bool(arguments.get("include_raw_content", False))
