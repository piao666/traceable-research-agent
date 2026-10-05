"""Normalize planned tool arguments to local executable boundaries."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
QUERY_REQUIRED_TOOLS = {"tavily_search", "arxiv_search", "crossref_search", "openalex_search", "semantic_scholar_search", "mcp_github_search"}
ACADEMIC_QUERY_TOOLS = {"arxiv_search", "crossref_search", "openalex_search", "semantic_scholar_search"}
# The final-plan validator can run before the application lifespan has
# registered the default catalog.  Keep only this read-only structural view
# here: it never registers a handler or replaces a configured ToolSpec.
_BUILTIN_TOOL_INPUT_SCHEMAS: dict[str, dict[str, str]] = {
    "file_reader": {"path": "string", "max_chars": "integer"},
    "sql_query": {"query": "string", "limit": "integer"},
    "mcp_github_search": {"query": "string", "repo": "string|null", "limit": "integer", "mode": "mock|public_api", "search_type": "issues|repositories", "sort": "stars|updated|best_match", "order": "asc|desc"},
    "tavily_search": {"query": "string", "max_results": "integer", "search_depth": "basic|advanced", "include_answer": "boolean", "include_raw_content": "boolean"},
    "memory_search": {"query": "string", "top_k": "integer"},
    "web_fetcher": {"urls": "list[string]", "source_id": "string (optional, stored source read instead of urls)", "offset": "integer (optional stored text offset)", "max_chars": "integer", "timeout_seconds": "integer", "batch_timeout_seconds": "integer"},
    "pdf_reader": {"paths": "list[string]", "max_chars": "integer", "max_pages": "integer"},
    "report_writer": {"run_id": "string", "observations": "array"},
    "arxiv_search": {"query": "string", "max_results": "integer", "sort_by": "string", "sort_order": "string"},
    "semantic_scholar_search": {"query": "string", "limit": "integer", "fields": "string"},
    "openalex_search": {"query": "string", "max_results": "integer", "sort": "string"},
    "crossref_search": {"query": "string", "max_results": "integer"},
}


@dataclass(frozen=True)
class PlanIssue:
    """A non-secret, non-network explanation of why a plan cannot execute."""

    step_no: int | None
    tool_name: str
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s)>\]}\"']+", text)
    return match.group(0) if match else None


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.") or None


def _site_search_query(task: str) -> str:
    url = _first_url(task)
    domain = _domain_from_url(url)
    if not domain:
        return task
    stem = re.sub(r"[^A-Za-z0-9]+", " ", domain.rsplit(".", 1)[0]).strip()
    return f"site:{domain} {stem or domain} docs pricing api features"


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
            _normalize_file_reader(step, arguments, task, notes, source_mode)
        elif tool_name == "sql_query":
            _normalize_sql_query(arguments, task, notes, source_mode)
        elif tool_name == "mcp_github_search":
            _normalize_github_search(arguments, task, source_mode, notes)
        elif tool_name == "tavily_search":
            _normalize_tavily_search(arguments, task, notes)
        elif tool_name in ACADEMIC_QUERY_TOOLS:
            if not str(arguments.get("query") or "").strip():
                # A broad task string is not a valid substitute for a planned
                # academic retrieval query. Leave the defect visible so the
                # final validator can request the one bounded planner repair.
                notes.append(f"Planner guardrail left {tool_name}.query unset; academic retrieval requires an explicit query.")
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
    source_mode: str,
) -> None:
    original = str(arguments.get("path") or "").strip()
    real = str(source_mode or "real").casefold() == "real"
    # A real web task must never acquire an unrelated local document merely
    # because file_reader happened to be available.  Explicit paths keep the
    # existing safety/HITL behavior below.
    if not original or (real and original == DEFAULT_FILE_PATH and DEFAULT_FILE_PATH not in task):
        if real:
            arguments.pop("path", None)
            notes.append("Planner guardrail left file_reader.path unset: real tasks require an explicit local path.")
            return
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
            if real:
                arguments.pop("path", None)
                notes.append("Planner guardrail rejected a missing file_reader.path in real mode; no demo file was substituted.")
                return
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


def _normalize_sql_query(arguments: dict[str, Any], task: str, notes: list[str], source_mode: str) -> None:
    original = str(arguments.get("query") or "").strip()
    limit = _bounded_int(arguments.get("limit"), 5, 1, 100)
    read_only, _, parser_metadata = validate_read_only_sql(original)
    normalized = str(parser_metadata.get("normalized_sql") or original).strip()
    is_demo_default = original in {DEFAULT_DOCUMENT_QUERY, DEFAULT_METRICS_QUERY, "SELECT id, title, category FROM documents"}
    if not original or not read_only or not _query_is_executable(normalized) or (str(source_mode or "real").casefold() == "real" and is_demo_default and original not in task):
        if str(source_mode or "real").casefold() == "real":
            arguments.pop("query", None)
            notes.append("Planner guardrail rejected sql_query.query in real mode; no demo query was substituted.")
            return
        arguments["query"] = _choose_safe_sql(task, original)
        notes.append(
            "Planner guardrail replaced sql_query.query with a schema-valid read-only demo query."
        )
    else:
        arguments["query"] = normalized
    arguments["limit"] = limit


def validate_plan_for_execution(
    plan: dict[str, Any],
    task_contract: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
) -> list[PlanIssue]:
    """Validate final plan arguments without executing tools or guessing input.

    This intentionally runs after every plan-expansion/approval path. Dynamic
    arguments are checked structurally here and concretely by the governed
    operation path once their dependency has resolved.
    """
    issues: list[PlanIssue] = []
    # `None` preserves the legacy no-allowlist path.  An explicitly persisted
    # empty list is an explicit no-tools authorization, not an unrestricted
    # scope.
    allowed_values = allowed_tools if allowed_tools is not None else plan.get("allowed_tools")
    allowed = set(allowed_values) if allowed_values is not None else None
    steps = list(plan.get("steps") or [])
    step_numbers = {
        int(step["step_no"])
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("step_no"), int)
    }
    seen_numbers: set[int] = set()
    report_position: int | None = None
    fetch_position: int | None = None
    for position, raw_step in enumerate(steps, 1):
        if not isinstance(raw_step, dict):
            issues.append(PlanIssue(None, "", "step", "invalid_step", "Plan step must be an object."))
            continue
        step_no = raw_step.get("step_no") if isinstance(raw_step.get("step_no"), int) else None
        if step_no is None or step_no <= 0 or step_no in seen_numbers:
            issues.append(PlanIssue(step_no, str(raw_step.get("tool_name") or ""), "step_no", "invalid_step_order", "Every plan step needs a unique positive step_no."))
        elif step_no is not None:
            seen_numbers.add(step_no)
        tool_name = str(raw_step.get("tool_name") or "")
        args = raw_step.get("arguments")
        if not isinstance(args, dict):
            issues.append(PlanIssue(step_no, tool_name, "arguments", "invalid_arguments", "Tool arguments must be an object."))
            continue
        if allowed is not None and tool_name not in allowed:
            issues.append(PlanIssue(step_no, tool_name, "tool_name", "disallowed_tool", "Tool is outside the persisted allowed_tools scope."))
        spec = __import__("app.tools.registry", fromlist=["get_tool"]).get_tool(tool_name)
        input_schema = dict(spec.input_schema or {}) if spec is not None else _BUILTIN_TOOL_INPUT_SCHEMAS.get(tool_name)
        if input_schema is None:
            issues.append(PlanIssue(step_no, tool_name, "tool_name", "unknown_tool", "Tool is not registered."))
            continue
        for field, expected in input_schema.items():
            if field in args and not _schema_value_matches(args[field], str(expected)):
                issues.append(PlanIssue(step_no, tool_name, field, "invalid_type", f"Expected {expected}."))
        dynamic = isinstance(raw_step.get("arguments_from"), dict)
        if dynamic:
            dependency = raw_step["arguments_from"]
            dependency_step = dependency.get("step_no")
            if not isinstance(dependency_step, int) or dependency_step not in step_numbers or dependency_step >= (step_no or 0):
                issues.append(PlanIssue(step_no, tool_name, "arguments_from.step_no", "invalid_dependency", "arguments_from must reference an earlier plan step."))
        if tool_name in QUERY_REQUIRED_TOOLS and not str(args.get("query") or "").strip():
            issues.append(PlanIssue(step_no, tool_name, "query", "missing_required_argument", "Search query must be non-empty."))
        if tool_name == "file_reader" and not str(args.get("path") or "").strip():
            issues.append(PlanIssue(step_no, tool_name, "path", "missing_required_argument", "file_reader requires an explicit user-scoped path."))
        if tool_name == "sql_query" and not str(args.get("query") or "").strip():
            issues.append(PlanIssue(step_no, tool_name, "query", "missing_required_argument", "sql_query requires an explicit read-only query."))
        if tool_name == "web_fetcher" and not dynamic and not args.get("urls") and not args.get("source_id"):
            issues.append(PlanIssue(step_no, tool_name, "urls", "missing_required_argument", "web_fetcher requires URLs or arguments_from."))
        if tool_name == "web_fetcher" and fetch_position is None:
            fetch_position = position
        if tool_name == "report_writer" and report_position is None:
            report_position = position
    if fetch_position is not None and report_position is not None and fetch_position > report_position:
        issues.append(PlanIssue(None, "report_writer", "step_order", "report_before_fetch", "Content fetching must occur before report_writer."))
    return issues


def validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> list[PlanIssue]:
    """Concrete action validation used after ReAct/dependency resolution."""
    return validate_plan_for_execution(
        # Runtime actions are not plan steps, but they still need a valid
        # synthetic position so the shared structural validator does not
        # reject every governed call before it reaches the Registry.
        {"steps": [{"step_no": 1, "tool_name": tool_name, "arguments": arguments}]},
        allowed_tools=[tool_name],
    )


def _schema_value_matches(value: Any, expected: str) -> bool:
    normalized = expected.strip().casefold()
    simple_checks = {
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "array": lambda: isinstance(value, list),
        "object": lambda: isinstance(value, dict),
        "null": lambda: value is None,
    }
    if normalized in simple_checks:
        return simple_checks[normalized]()
    if normalized.startswith("list["):
        return isinstance(value, list)
    if normalized.startswith("string "):
        return isinstance(value, str)
    if "|" not in normalized:
        # Unknown legacy descriptors remain compatible; concrete schemas above
        # and registered handlers provide the authoritative runtime check.
        return True
    alternatives = [item.strip() for item in normalized.split("|")]
    if all(item in simple_checks for item in alternatives):
        return any(simple_checks[item]() for item in alternatives)
    # A pipe-delimited descriptor that is not a union of primitive types is a
    # string enum (for example basic|advanced).  Do not silently accept an
    # arbitrary string at the final-plan gate.
    return isinstance(value, str) and value.casefold() in alternatives


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


def _normalize_tavily_search(arguments: dict[str, Any], task: str, notes: list[str]) -> None:
    if not str(arguments.get("query") or "").strip():
        arguments["query"] = _site_search_query(task)
        notes.append("Planner guardrail filled missing tavily_search.query from task.")
    elif _first_url(task) and str(arguments.get("query") or "").strip() == task.strip():
        arguments["query"] = _site_search_query(task)
        notes.append("Planner guardrail scoped tavily_search.query to the task URL domain.")
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
