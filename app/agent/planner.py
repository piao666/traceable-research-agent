"""Deterministic JSON planner for task runs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.agent.plan_guardrails import normalize_plan_arguments
from app.llm.planner_client import call_llm_for_plan
from app.llm.providers import create_llm_client
from app.llm.schema import extract_json_object, validate_and_normalize_plan
from app.mcp.policy import requires_interactive_confirmation, tool_channel
from app.tools.registry import get_tool, list_tools


DEFAULT_TOOL_ORDER = [
    "file_reader",
    "sql_query",
    "rag_search",
    "mcp_github_search",
    "tavily_search",
    "report_writer",
]

FULL_PLANNER_REQUIRED_TOOLS = [
    "file_reader",
    "sql_query",
    "rag_search",
    "mcp_github_search",
    "tavily_search",
    "report_writer",
]

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
GITHUB_KEYWORDS = {
    "github",
    "repo",
    "repository",
    "issue",
    "pr",
    "pull request",
    "code search",
    "\u4ed3\u5e93",
    "\u4ee3\u7801\u4ed3\u5e93",
}
WEB_SEARCH_KEYWORDS = {
    "latest",
    "web search",
    "web sources",
    "current web",
    "current web sources",
    "internet search",
    "external sources",
    "tavily",
    "online research",
    "最新资料",
    "全网",
    "网上",
    "联网",
    "学习资料",
    "课程",
    "教程",
    "资料搜集",
    "网络搜索",
    "外部资料",
    "互联网搜索",
    "联网搜索",
}
REMOTE_MCP_KEYWORDS = {
    "remote",
    "mcp remote",
    "remote mcp",
    "remote tool",
    "firecrawl",
    "exa",
    "context7",
    "远端",
    "远程",
}
DEEP_RESEARCH_KEYWORDS = {
    "deep research",
    "deep web research",
    "source pack",
    "scrape",
    "crawl",
    "extract",
    "page content",
    "read page",
    "web evidence",
    "深入调研",
    "深度调研",
    "深度网页调研",
    "网页正文",
    "正文抓取",
    "站点展开",
    "可验证证据",
}
TECH_DOCS_RESEARCH_KEYWORDS = {
    "technical docs",
    "technical documentation",
    "api docs",
    "library docs",
    "framework docs",
    "sdk docs",
    "context7",
    "fastapi",
    "streamlit",
    "comfyui",
    "stable diffusion",
    "mcp sdk",
    "技术文档",
    "官方文档",
    "接口文档",
    "库文档",
    "框架文档",
}
DEEP_RESEARCH_REMOTE_PRIORITY = (
    ("exa", "web_search_exa"),
    ("exa", "web_search_advanced_exa"),
    ("firecrawl", "search"),
    ("firecrawl", "map"),
    ("firecrawl", "scrape"),
    ("firecrawl", "extract"),
    ("exa", "web_fetch_exa"),
)
TECH_DOCS_REMOTE_PRIORITY = (
    ("context7", "resolve-library-id"),
    ("context7", "query-docs"),
    ("exa", "web_search_exa"),
    ("exa", "web_fetch_exa"),
    ("firecrawl", "search"),
    ("firecrawl", "scrape"),
)
GITHUB_REPOSITORY_RANKING_KEYWORDS = {
    "stars",
    "star ranking",
    "top repositories",
    "most starred",
    "仓库排名",
    "star 排名",
    "最受欢迎仓库",
}
GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
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


def _scenario_marker(scenario_template: str | None) -> str:
    return str(scenario_template or "").strip().lower()


def _is_deep_research_scenario(task_lower: str, scenario_template: str | None) -> bool:
    marker = _scenario_marker(scenario_template)
    return (
        "deep_web_research" in marker
        or "deep research" in marker
        or "深度网页调研" in marker
        or _matches(task_lower, DEEP_RESEARCH_KEYWORDS)
    )


def _is_technical_docs_scenario(task_lower: str, scenario_template: str | None) -> bool:
    marker = _scenario_marker(scenario_template)
    return (
        "technical_docs_research" in marker
        or "technical docs" in marker
        or "技术文档调研" in marker
        or _matches(task_lower, TECH_DOCS_RESEARCH_KEYWORDS)
    )


def _schema_field_names(schema: dict[str, Any]) -> set[str]:
    fields = {str(key) for key in schema.keys()}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        fields.update(str(key) for key in properties.keys())
    return fields


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s)>\]}\"']+", text)
    return match.group(0) if match else None


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.") or None


def _requested_result_count(task: str, default: int | None = None) -> int | None:
    patterns = (
        r"(?:top|前)\s*(\d{1,2})",
        r"(?:输出|返回|列出|找出|检索|显示|要)\s*(\d{1,2})\s*(?:个|条|项)?",
        r"(\d{1,2})\s*(?:个|条|项|篇|款)\s*(?:项目|仓库|结果|来源)?",
        r"(\d{1,2})\s*(?:projects|repositories|repos|results|sources)",
    )
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return max(1, min(int(match.group(1)), 20))
        except (TypeError, ValueError):
            continue
    return default


def _is_github_trending_task(task_lower: str) -> bool:
    if "github" not in task_lower or "star" not in task_lower:
        return False
    time_terms = ("today", "daily", "今日", "今天", "当天", "日榜")
    growth_terms = ("growth", "growing", "trending", "增长", "增量", "增长量", "飙升")
    ranking_terms = ("top", "最大", "最多", "排名", "排行", "榜")
    return (
        any(term in task_lower for term in time_terms)
        and any(term in task_lower for term in growth_terms)
        and any(term in task_lower for term in ranking_terms)
    )


def _site_search_query(task: str) -> str:
    """Turn URL-crawl prompts into site-scoped discovery queries."""

    url = _first_url(task)
    domain = _domain_from_url(url)
    if not domain:
        return task
    stem = re.sub(r"[^A-Za-z0-9]+", " ", domain.rsplit(".", 1)[0]).strip()
    return f"site:{domain} {stem or domain} docs pricing api features"


def _remote_default_limit(tool_name: str) -> int:
    normalized = tool_name.lower()
    if normalized.endswith(".firecrawl.map") or normalized.endswith("firecrawl.map"):
        return 20
    if normalized.endswith(".firecrawl.extract") or normalized.endswith("firecrawl.extract"):
        return 10
    return 5


def _remote_result_limit(tool_name: str, task: str) -> int:
    return _requested_result_count(task, _remote_default_limit(tool_name)) or _remote_default_limit(tool_name)


def _guess_library_name(task: str) -> str:
    known = (
        "FastAPI",
        "Streamlit",
        "ComfyUI",
        "Stable Diffusion",
        "MCP SDK",
        "LangChain",
        "LlamaIndex",
        "Chroma",
        "FAISS",
    )
    task_lower = task.lower()
    for name in known:
        if name.lower() in task_lower:
            return name
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", task)
    return words[0] if words else task[:80]


def _remote_tool_identity(spec: Any) -> tuple[str, str, str, str]:
    metadata = spec.metadata or {}
    server = str(metadata.get("remote_server") or "").lower()
    remote_name = str(metadata.get("remote_tool_name") or spec.name).lower()
    registry_name = str(metadata.get("remote_registry_name") or spec.name).lower()
    return spec.name.lower(), server, remote_name, registry_name


def _remote_tool_matches(spec: Any, provider: str, remote_tool_name: str) -> bool:
    spec_name, server, remote_name, registry_name = _remote_tool_identity(spec)
    provider = provider.lower()
    target = remote_tool_name.lower()
    provider_match = (
        provider in server
        or registry_name.startswith(f"{provider}.")
        or spec_name.startswith(f"{provider}.")
        or provider in spec_name
    )
    tool_match = remote_name == target or registry_name.endswith(f".{target}") or spec_name.endswith(f".{target}")
    return provider_match and tool_match


def _append_preferred_remote_steps(
    steps: list[dict[str, Any]],
    notes: list[str],
    task: str,
    allowed_set: set[str] | None,
    priority: tuple[tuple[str, str], ...],
) -> list[str]:
    inserted: list[str] = []
    remote_specs = [
        spec
        for spec in list_tools()
        if spec.enabled and "mcp_remote" in spec.tags and spec.name != "report_writer"
        and (
            tool_channel(spec) == "readonly"
            or (allowed_set is not None and spec.name in allowed_set)
        )
    ]
    for provider, remote_tool_name in priority:
        spec = next(
            (
                candidate
                for candidate in remote_specs
                if _remote_tool_matches(candidate, provider, remote_tool_name)
            ),
            None,
        )
        if spec is None:
            continue
        before = len(steps)
        _append_step(steps, notes, spec.name, task, allowed_set)
        if len(steps) > before:
            inserted.append(spec.name)
    return inserted


def _url_dependent_remote_tool(provider: str, remote_tool_name: str) -> bool:
    return remote_tool_name.lower() in {
        "scrape",
        "extract",
        "map",
        "web_fetch_exa",
    }


def _remote_step_requires_task_url(tool_name: str) -> bool:
    normalized = tool_name.lower()
    return any(
        normalized.endswith(suffix)
        for suffix in (
            ".firecrawl.scrape",
            ".firecrawl.extract",
            ".firecrawl.map",
            ".exa.web_fetch_exa",
            "firecrawl.scrape",
            "firecrawl.extract",
            "firecrawl.map",
            "exa.web_fetch_exa",
        )
    )


def _drop_url_dependent_remote_steps_without_task_url(
    steps: list[dict[str, Any]],
    notes: list[str],
    task: str,
) -> list[dict[str, Any]]:
    url = _first_url(task)
    if url:
        for step in steps:
            tool_name = str(step.get("tool_name") or "")
            if not _remote_step_requires_task_url(tool_name):
                continue
            arguments = step.setdefault("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
                step["arguments"] = arguments
            if not arguments.get("url") and not arguments.get("urls"):
                arguments["url"] = url
        return steps
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for step in steps:
        tool_name = str(step.get("tool_name") or "")
        if _remote_step_requires_task_url(tool_name):
            arguments = step.get("arguments")
            if isinstance(arguments, dict) and (arguments.get("url") or arguments.get("urls")):
                kept.append(step)
                continue
            removed.append(tool_name)
            continue
        kept.append(step)
    if removed:
        _append_note_once(
            notes,
            "Page-content MCP tools that require a concrete URL were removed because the task did not include a URL: "
            + ", ".join(sorted(set(removed))),
        )
    return kept


def _fill_remote_step_arguments(step: dict[str, Any], task: str) -> None:
    tool_name = str(step.get("tool_name") or "")
    spec = get_tool(tool_name)
    if not spec or "mcp_remote" not in spec.tags:
        return
    arguments = step.setdefault("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
        step["arguments"] = arguments
    fields = _schema_field_names(spec.input_schema or {})
    url = _first_url(task)
    domain = _domain_from_url(url)
    if "query" in fields and not str(arguments.get("query") or "").strip():
        arguments["query"] = _site_search_query(task)
    if "text" in fields and not str(arguments.get("text") or "").strip():
        arguments["text"] = task
    if "url" in fields and url and not arguments.get("url"):
        arguments["url"] = url
    if url and not arguments.get("url") and not arguments.get("urls") and _remote_step_requires_task_url(tool_name):
        arguments["url"] = url
    if "libraryName" in fields and not str(arguments.get("libraryName") or "").strip():
        arguments["libraryName"] = _guess_library_name(task)
    if "library_id" in fields and not str(arguments.get("library_id") or "").strip():
        arguments["library_id"] = _guess_library_name(task)
    if "libraryId" in fields and not str(arguments.get("libraryId") or "").strip():
        arguments["libraryId"] = _guess_library_name(task)
    if "limit" in fields and not arguments.get("limit"):
        arguments["limit"] = _remote_result_limit(tool_name, task)
    if "max_results" in fields and not arguments.get("max_results"):
        arguments["max_results"] = _requested_result_count(task, 5)
    if "numResults" in fields and not arguments.get("numResults"):
        arguments["numResults"] = _requested_result_count(task, 5)
    if domain and "includeDomains" in fields and not arguments.get("includeDomains"):
        arguments["includeDomains"] = [domain]


def _append_plan_allowed_tool(plan: dict[str, Any], tool_name: str, allowed_tools: list[str] | None) -> None:
    if allowed_tools is not None:
        return
    current = plan.get("allowed_tools")
    if not isinstance(current, list):
        current = DEFAULT_TOOL_ORDER.copy()
    normalized = [str(item) for item in current]
    if tool_name not in normalized:
        normalized.append(tool_name)
    plan["allowed_tools"] = normalized


def _is_remote_mcp_tool_name(tool_name: str) -> bool:
    spec = get_tool(tool_name)
    return bool(spec and "mcp_remote" in spec.tags)


def _append_github_trending_steps(
    steps: list[dict[str, Any]],
    notes: list[str],
    task: str,
    allowed_set: set[str] | None,
) -> None:
    """Use the GitHub Trending page as primary evidence for daily star-growth tasks."""

    requested_count = _requested_result_count(task, 20) or 20
    trending_task = f"{task} {GITHUB_TRENDING_URL}"
    inserted_remote = False
    remote_specs = [
        spec
        for spec in list_tools()
        if spec.enabled
        and "mcp_remote" in spec.tags
        and spec.name != "report_writer"
        and tool_channel(spec) == "readonly"
        and (allowed_set is None or spec.name in allowed_set)
    ]
    for provider, remote_tool_name in (
        ("firecrawl", "scrape"),
        ("firecrawl", "extract"),
        ("exa", "web_fetch_exa"),
    ):
        spec = next(
            (
                candidate
                for candidate in remote_specs
                if _remote_tool_matches(candidate, provider, remote_tool_name)
            ),
            None,
        )
        if spec is None:
            continue
        before = len(steps)
        _append_step(steps, notes, spec.name, trending_task, allowed_set)
        if len(steps) == before:
            continue
        step = steps[-1]
        arguments = step.setdefault("arguments", {})
        if isinstance(arguments, dict):
            arguments["url"] = GITHUB_TRENDING_URL
            fields = _schema_field_names(spec.input_schema or {})
            if "limit" in fields:
                arguments["limit"] = requested_count
        inserted_remote = True

    if inserted_remote:
        _append_note_once(
            notes,
            "GitHub trending task uses https://github.com/trending?since=daily as primary evidence.",
        )
        _append_note_once(
            notes,
            "GitHub Public API repository search is not used as the primary source because it does not expose today's star-growth ranking.",
        )
        return

    _append_step(steps, notes, "tavily_search", task, allowed_set)
    if steps and steps[-1].get("tool_name") == "tavily_search":
        steps[-1]["arguments"].update(
            {
                "query": "site:github.com/trending GitHub trending repositories today stars",
                "max_results": requested_count,
            }
        )
    _append_note_once(
        notes,
        "GitHub trending page reader was unavailable; fell back to web discovery evidence.",
    )


def _append_note_once(notes: list[str], note: str) -> None:
    if note not in notes:
        notes.append(note)


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
        "mcp_github_search": {
            "goal": "Collect real read-only GitHub evidence through the Public API adapter.",
            "arguments": {
                "query": task,
                "repo": None,
                "limit": 5,
                "mode": "public_api",
                "search_type": "issues",
            },
            "expected_output": "Real read-only GitHub Public API search results.",
            "completion_criteria": "The adapter returns evidence without any write operation.",
            "risk_level": "medium",
            "requires_confirmation": False,
        },
        "tavily_search": {
            "goal": "Search current external web sources through the read-only Tavily API.",
            "arguments": {
                "query": task,
                "max_results": settings.tavily_default_max_results,
                "search_depth": "advanced",
                "include_answer": True,
                "include_raw_content": False,
            },
            "expected_output": "Current web results returned by the real Tavily Search API.",
            "completion_criteria": "Tavily returns external evidence or a structured API error.",
            "risk_level": "medium",
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
        "web_fetcher": {
            "goal": "Fetch full-text content from URLs discovered in the previous step.",
            "arguments": {
                "urls": [],
                "max_chars": 8000,
                "timeout_seconds": 10,
            },
            "expected_output": "Full-text page content for each URL with content_basis tagging (full_text/partial/snippet_only).",
            "completion_criteria": "Each URL is either successfully fetched or a structured fetch error is recorded per URL.",
            "risk_level": "low",
            "requires_confirmation": False,
        },
    }
    if tool_name in templates:
        step = templates[tool_name].copy()
    else:
        spec = get_tool(tool_name)
        input_schema = spec.input_schema if spec else {}
        fields = _schema_field_names(input_schema)
        arguments: dict[str, Any] = {}
        url = _first_url(task)
        remote_tool_name = str((spec.metadata or {}).get("remote_tool_name") or tool_name) if spec else tool_name
        if "query" in fields:
            arguments["query"] = _site_search_query(task)
        if "text" in fields:
            arguments["text"] = task
        if "url" in fields and url:
            arguments["url"] = url
        if url and "url" not in arguments and _remote_step_requires_task_url(tool_name):
            arguments["url"] = url
        if "libraryName" in fields:
            arguments["libraryName"] = _guess_library_name(task)
        if "library_id" in fields:
            arguments.setdefault("library_id", _guess_library_name(task))
        if "libraryId" in fields:
            arguments.setdefault("libraryId", _guess_library_name(task))
        if "limit" in fields:
            arguments.setdefault("limit", _remote_result_limit(tool_name, task))
        if "max_results" in fields:
            arguments.setdefault("max_results", _requested_result_count(task, 5))
        if "numResults" in fields:
            arguments.setdefault("numResults", _requested_result_count(task, 5))
        domain = _domain_from_url(url)
        if domain and "includeDomains" in fields:
            arguments.setdefault("includeDomains", [domain])
        if not arguments and remote_tool_name.lower() in {"scrape", "fetch", "web_fetch_exa"}:
            arguments["query"] = task
        step = {
            "goal": f"Call remote MCP tool {tool_name} through the unified Tool Registry.",
            "arguments": arguments,
            "expected_output": "Remote MCP tool output or a structured remote failure.",
            "completion_criteria": "The remote tool returns an observation without crashing the Agent API.",
            "risk_level": (spec.risk_level.value if spec else "low"),
            "requires_confirmation": requires_interactive_confirmation(spec),
        }
        if spec and requires_interactive_confirmation(spec):
            step["completion_criteria"] = (
                "Human confirmation is recorded before this interactive remote MCP tool runs."
            )
            step["confirmation_reason"] = "mcp_interactive_channel"
            step["confirmation_details"] = {
                "tool_name": tool_name,
                "channel": tool_channel(spec),
                "remote_server": (spec.metadata or {}).get("remote_server"),
                "remote_tool_name": (spec.metadata or {}).get("remote_tool_name"),
            }
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



def _apply_execution_mode(plan: dict, override: str | None) -> dict:
    """Write execution_mode into plan from override or global settings."""
    effective = (override or settings.execution_mode or "planned").strip().lower()
    if effective not in ("planned", "react"):
        effective = "planned"
    plan["execution_mode"] = effective
    plan["requested_execution_mode"] = effective
    return plan


def plan_task(
    task: str,
    allowed_tools: list[str] | None = None,
    source_mode: str = "real",
    planner_mode: str | None = None,
    scenario_template: str | None = None,
    execution_mode_override: str | None = None,
) -> dict[str, Any]:
    """Create a plan using deterministic rules or optional LLM planning."""

    mode = (planner_mode or settings.llm_planner_mode or "deterministic").lower()
    if mode not in {"deterministic", "llm", "auto"}:
        mode = "deterministic"

    if mode == "deterministic":
        plan = deterministic_plan_task(task, allowed_tools, source_mode, scenario_template)
        plan["planner_source"] = "deterministic"
        plan["llm_provider"] = None
        plan["llm_model"] = None
        return _apply_execution_mode(plan, execution_mode_override)

    should_try_llm = mode == "llm" or (mode == "auto" and settings.llm_planner_enabled)
    if should_try_llm:
        fallback_reason = "LLM planner unavailable; used deterministic fallback."
        client = create_llm_client(settings)
        response = call_llm_for_plan(client, task, allowed_tools, source_mode, scenario_template)
        if response.success and response.content:
            raw_plan = extract_json_object(response.content)
            if raw_plan is not None:
                valid, normalized, _validation_notes = validate_and_normalize_plan(
                    raw_plan=raw_plan,
                    task=task,
                    allowed_tools=allowed_tools,
                    source_mode=source_mode,
                )
                if valid and normalized is not None:
                    _enforce_external_tool_modes(normalized, task, source_mode)
                    _apply_human_confirmation_policy(normalized, task)
                    normalize_plan_arguments(normalized, task, source_mode)
                    _synchronize_confirmation_notes(normalized)
                    normalized["planner_source"] = "llm"
                    normalized["llm_provider"] = response.provider
                    normalized["llm_model"] = response.model
                    try:
                        from app.agent.query_decomposer import decompose_and_annotate_plan

                        normalized = decompose_and_annotate_plan(task, normalized, client, n=4)
                    except Exception:
                        pass
                    _ensure_full_planner_steps(
                        normalized,
                        task,
                        allowed_tools,
                        scenario_template,
                    )
                    _ensure_github_trending_steps(normalized, task, allowed_tools)
                    _ensure_research_template_steps(
                        normalized,
                        task,
                        allowed_tools,
                        scenario_template,
                    )
                    normalize_plan_arguments(normalized, task, source_mode)
                    _apply_requested_result_count(normalized, task)
                    return _apply_execution_mode(normalized, execution_mode_override)
                fallback_reason = "LLM output failed schema validation; used deterministic fallback."
            else:
                fallback_reason = "LLM output was not valid JSON; used deterministic fallback."
        elif response.error_message:
            fallback_reason = f"{response.error_message}; used deterministic fallback."

        plan = deterministic_plan_task(task, allowed_tools, source_mode, scenario_template)
        plan["planner_source"] = "deterministic_fallback"
        plan["llm_provider"] = client.describe().get("provider")
        plan["llm_model"] = client.describe().get("model")
        plan["notes"] = list(plan.get("notes") or []) + [_safe_fallback_reason(fallback_reason)]
        _synchronize_confirmation_notes(plan)
        _apply_requested_result_count(plan, task)
        return _apply_execution_mode(plan, execution_mode_override)

    plan = deterministic_plan_task(task, allowed_tools, source_mode, scenario_template)
    plan["planner_source"] = "deterministic"
    plan["llm_provider"] = None
    plan["llm_model"] = None
    _synchronize_confirmation_notes(plan)
    _apply_requested_result_count(plan, task)
    return _apply_execution_mode(plan, execution_mode_override)


def deterministic_plan_task(
    task: str,
    allowed_tools: list[str] | None = None,
    source_mode: str = "real",
    scenario_template: str | None = None,
) -> dict[str, Any]:
    """Create a stable deterministic plan from task keywords.

    The same inputs produce the same JSON-compatible output. No LLM, network, or
    tool execution path is used here.
    """

    task_text = task.strip()
    task_lower = task_text.lower()
    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    requires_human_confirmation = False
    notes: list[str] = []
    steps: list[dict[str, Any]] = []

    scenario_marker = _scenario_marker(scenario_template)
    strict_deep_research = "deep_web_research" in scenario_marker
    strict_technical_docs = "technical_docs_research" in scenario_marker
    deep_research = _is_deep_research_scenario(task_lower, scenario_template)
    technical_docs = _is_technical_docs_scenario(task_lower, scenario_template)
    github_trending = _is_github_trending_task(task_lower)
    if strict_deep_research:
        technical_docs = False
    if strict_technical_docs:
        deep_research = False

    if github_trending:
        _append_github_trending_steps(steps, notes, task_text, allowed_set)
        if allowed_set is None or "report_writer" in allowed_set:
            _append_step(
                steps,
                notes,
                "report_writer",
                task_text,
                allowed_set,
                requires_human_confirmation,
            )
    elif deep_research:
        _append_step(steps, notes, "tavily_search", task_text, allowed_set)
        url = _first_url(task_text)
        priority = tuple(
            item
            for item in DEEP_RESEARCH_REMOTE_PRIORITY
            if url or not _url_dependent_remote_tool(item[0], item[1])
        )
        inserted = _append_preferred_remote_steps(
            steps,
            notes,
            task_text,
            allowed_set,
            priority,
        )
        if not inserted:
            # Fallback: use built-in web_fetcher for the fetch phase
            _append_step(steps, notes, "web_fetcher", task_text, allowed_set)
            # Mark that web_fetcher depends on tavily_search results
            _tavily_step_no = next(
                (int(s.get("step_no") or 0) for s in steps if s.get("tool_name") == "tavily_search"),
                None,
            )
            if _tavily_step_no is not None:
                for s in steps:
                    if s.get("tool_name") == "web_fetcher":
                        s["arguments_from"] = {"step_no": _tavily_step_no, "field": "results"}
                        s["arguments"] = {"urls": [], "max_chars": 8000, "timeout_seconds": 10}
                        break
            _append_note_once(
                notes,
                "Deep web research MCP tools were not configured; using built-in web_fetcher (httpx+BeautifulSoup) for full-text retrieval.",
            )
        elif not url:
            _append_note_once(
                notes,
                "Deep web research used MCP discovery tools. Page-content MCP tools that require a URL were skipped because the task did not include a concrete URL.",
            )
    if not github_trending and technical_docs:
        _append_step(steps, notes, "mcp_github_search", task_text, allowed_set)
        url = _first_url(task_text)
        priority = tuple(
            item
            for item in TECH_DOCS_REMOTE_PRIORITY
            if url or not _url_dependent_remote_tool(item[0], item[1])
        )
        inserted = _append_preferred_remote_steps(
            steps,
            notes,
            task_text,
            allowed_set,
            priority,
        )
        if not inserted:
            _append_note_once(
                notes,
                "Technical docs MCP tools were not configured; used available built-in research tools only.",
            )

    if not github_trending and not strict_deep_research and not strict_technical_docs:
        if _matches(task_lower, FILE_KEYWORDS):
            _append_step(steps, notes, "file_reader", task_text, allowed_set)
        if _matches(task_lower, SQL_KEYWORDS):
            _append_step(steps, notes, "sql_query", task_text, allowed_set)
        if _matches(task_lower, WEB_SEARCH_KEYWORDS):
            _append_step(steps, notes, "tavily_search", task_text, allowed_set)
        if _matches(task_lower, REMOTE_MCP_KEYWORDS):
            remote_tools = [
                spec.name
                for spec in list_tools()
                if spec.enabled and "mcp_remote" in spec.tags and spec.name != "report_writer"
                and (
                    tool_channel(spec) == "readonly"
                    or (allowed_set is not None and spec.name in allowed_set)
                )
            ]
            for remote_tool_name in remote_tools:
                if allowed_set is None or remote_tool_name in allowed_set:
                    _append_step(steps, notes, remote_tool_name, task_text, allowed_set)
                    break
        if _matches(task_lower, RAG_KEYWORDS):
            _append_step(steps, notes, "rag_search", task_text, allowed_set)
        if _matches(task_lower, GITHUB_KEYWORDS):
            _append_step(steps, notes, "mcp_github_search", task_text, allowed_set)
        if _matches(task_lower, REPORT_KEYWORDS):
            _append_step(
                steps,
                notes,
                "report_writer",
                task_text,
                allowed_set,
                requires_human_confirmation,
            )
    if (deep_research or technical_docs) and not github_trending and not any(
        step.get("tool_name") == "report_writer" for step in steps
    ):
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

    default_allowed = DEFAULT_TOOL_ORDER.copy()
    for spec in list_tools():
        if (
            spec.enabled
            and "mcp_remote" in spec.tags
            and tool_channel(spec) == "readonly"
            and spec.name not in default_allowed
        ):
            default_allowed.append(spec.name)

    plan = {
        "version": "deterministic-v1",
        "task": task_text,
        "source_mode": source_mode,
        "allowed_tools": allowed_tools if allowed_tools is not None else default_allowed,
        "steps": steps,
        "notes": notes,
        "confirmation": None,
    }
    if scenario_template:
        plan["scenario_template"] = scenario_template
    _ensure_full_planner_steps(plan, task_text, allowed_tools, scenario_template)
    _ensure_research_template_steps(plan, task_text, allowed_tools, scenario_template)
    _enforce_external_tool_modes(plan, task_text, source_mode)
    normalize_plan_arguments(plan, task_text, source_mode)
    _apply_requested_result_count(plan, task_text)
    return plan


def _enforce_external_tool_modes(
    plan: dict[str, Any], task: str, source_mode: str
) -> None:
    """Keep external tool plans consistent with explicit real/offline mode."""

    normalized_source = str(source_mode or "real").strip().lower()
    use_mock = normalized_source in {"mock", "offline"} or settings.offline_mode
    repository_ranking = _matches(task.lower(), GITHUB_REPOSITORY_RANKING_KEYWORDS)
    for step in plan.get("steps") or []:
        tool_name = step.get("tool_name")
        arguments = step.setdefault("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
            step["arguments"] = arguments
        if tool_name == "mcp_github_search":
            arguments["mode"] = "mock" if use_mock else "public_api"
            if repository_ranking:
                arguments.update(
                    {
                        "repo": None,
                        "search_type": "repositories",
                        "sort": "stars",
                        "order": "desc",
                    }
                )
        elif tool_name == "tavily_search":
            arguments.setdefault("max_results", settings.tavily_default_max_results)
            arguments.setdefault("search_depth", "advanced")


def _apply_requested_result_count(plan: dict[str, Any], task: str) -> None:
    requested_count = _requested_result_count(task)
    if requested_count is None:
        return
    plan["requested_result_count"] = requested_count
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name") or "")
        arguments = step.setdefault("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
            step["arguments"] = arguments
        if tool_name == "mcp_github_search":
            arguments["limit"] = requested_count
        elif tool_name == "tavily_search":
            arguments["max_results"] = requested_count
        else:
            spec = get_tool(tool_name)
            if spec and "mcp_remote" in spec.tags:
                fields = _schema_field_names(spec.input_schema or {})
                if "limit" in fields:
                    arguments["limit"] = requested_count
                if "max_results" in fields:
                    arguments["max_results"] = requested_count
                if "numResults" in fields:
                    arguments["numResults"] = requested_count


def _safe_fallback_reason(reason: str) -> str:
    """Return a fallback reason without provider secrets or headers."""

    blocked_tokens = ["authorization", "bearer", "api_key", "apikey", "token"]
    lowered = reason.lower()
    if any(token in lowered for token in blocked_tokens):
        return "LLM planner failed with a redacted provider error; used deterministic fallback."
    return reason[:300]


def _ensure_github_trending_steps(
    plan: dict[str, Any],
    task: str,
    allowed_tools: list[str] | None,
) -> None:
    if not _is_github_trending_task(task.lower()):
        return
    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    existing_steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    report_steps = [step for step in existing_steps if step.get("tool_name") == "report_writer"]
    notes = [str(note) for note in plan.get("notes") or []]
    steps: list[dict[str, Any]] = []
    _append_github_trending_steps(steps, notes, task, allowed_set)
    if allowed_set is None or "report_writer" in allowed_set:
        report_step = report_steps[0] if report_steps else _step_template("report_writer", task)
        report_step["tool_name"] = "report_writer"
        steps.append(report_step)
    for index, step in enumerate(steps, start=1):
        step["step_no"] = index
        tool_name = str(step.get("tool_name") or "")
        if tool_name:
            _append_plan_allowed_tool(plan, tool_name, allowed_tools)
    plan["steps"] = steps
    plan["notes"] = _dedupe_notes(notes)


def _ensure_research_template_steps(
    plan: dict[str, Any],
    task: str,
    allowed_tools: list[str] | None,
    scenario_template: str | None,
) -> None:
    """Make research templates honor their promised source-pack tool chain."""

    task_lower = task.lower()
    if _is_github_trending_task(task_lower):
        return
    deep_research = _is_deep_research_scenario(task_lower, scenario_template)
    technical_docs = _is_technical_docs_scenario(task_lower, scenario_template)
    marker = _scenario_marker(scenario_template)
    if "deep_web_research" in marker:
        technical_docs = False
    if "technical_docs_research" in marker:
        deep_research = False
    if not (deep_research or technical_docs):
        return

    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    existing_steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    report_steps = [step for step in existing_steps if step.get("tool_name") == "report_writer"]
    steps = [step for step in existing_steps if step.get("tool_name") != "report_writer"]
    notes = [str(note) for note in plan.get("notes") or []]
    steps = _drop_url_dependent_remote_steps_without_task_url(steps, notes, task)
    for step in steps:
        _fill_remote_step_arguments(step, task)
    plan["steps"] = steps

    if deep_research:
        _append_step(steps, notes, "tavily_search", task, allowed_set)
        url = _first_url(task)
        priority = tuple(
            item
            for item in DEEP_RESEARCH_REMOTE_PRIORITY
            if url or not _url_dependent_remote_tool(item[0], item[1])
        )
        inserted = _append_preferred_remote_steps(steps, notes, task, allowed_set, priority)
        has_remote_step = any(_is_remote_mcp_tool_name(str(step.get("tool_name") or "")) for step in steps)
        if not inserted and not has_remote_step:
            _append_note_once(
                notes,
                "Deep web research MCP tools were not configured; used available built-in discovery tools only.",
            )
        elif not url:
            _append_note_once(
                notes,
                "Deep web research used MCP discovery tools. Page-content MCP tools that require a URL were skipped because the task did not include a concrete URL.",
            )
    elif technical_docs:
        _append_step(steps, notes, "mcp_github_search", task, allowed_set)
        url = _first_url(task)
        priority = tuple(
            item
            for item in TECH_DOCS_REMOTE_PRIORITY
            if url or not _url_dependent_remote_tool(item[0], item[1])
        )
        inserted = _append_preferred_remote_steps(
            steps,
            notes,
            task,
            allowed_set,
            priority,
        )
        has_remote_step = any(_is_remote_mcp_tool_name(str(step.get("tool_name") or "")) for step in steps)
        if not inserted and not has_remote_step:
            _append_note_once(
                notes,
                "Technical docs MCP tools were not configured; used available built-in research tools only.",
            )

    for step in list(steps):
        tool_name = str(step.get("tool_name") or "")
        if tool_name:
            _append_plan_allowed_tool(plan, tool_name, allowed_tools)

    if allowed_set is None or "report_writer" in allowed_set:
        report_step = report_steps[0] if report_steps else _step_template("report_writer", task)
        report_step["tool_name"] = "report_writer"
        steps.append(report_step)
        _append_plan_allowed_tool(plan, "report_writer", allowed_tools)

    for index, step in enumerate(steps, start=1):
        step["step_no"] = index
    plan["steps"] = steps
    plan["notes"] = notes
    if scenario_template:
        plan["scenario_template"] = scenario_template


def _is_full_planner_scenario(
    allowed_tools: list[str] | None,
    scenario_template: str | None,
) -> bool:
    marker = _scenario_marker(scenario_template)
    if marker and ("full" in marker or "\u5168\u89c4\u5212\u5668" in marker):
        return True
    return False


def _ensure_full_planner_steps(
    plan: dict[str, Any],
    task: str,
    allowed_tools: list[str] | None,
    scenario_template: str | None,
) -> None:
    """Make the Streamlit full-planner template honor its all-tools promise."""

    if not _is_full_planner_scenario(allowed_tools, scenario_template):
        return

    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    report_steps = [step for step in steps if step.get("tool_name") == "report_writer"]
    non_report_steps = [step for step in steps if step.get("tool_name") != "report_writer"]
    steps_by_tool: dict[str, dict[str, Any]] = {}
    extra_steps: list[dict[str, Any]] = []
    for step in non_report_steps:
        tool_name = str(step.get("tool_name") or "")
        if tool_name in FULL_PLANNER_REQUIRED_TOOLS:
            steps_by_tool.setdefault(tool_name, step)
        else:
            extra_steps.append(step)
    inserted: list[str] = []

    ordered_steps: list[dict[str, Any]] = []
    for tool_name in FULL_PLANNER_REQUIRED_TOOLS:
        if tool_name == "report_writer":
            continue
        if allowed_set is not None and tool_name not in allowed_set:
            continue
        if tool_name in steps_by_tool:
            ordered_steps.append(steps_by_tool[tool_name])
        else:
            step = _step_template(tool_name, task)
            step["tool_name"] = tool_name
            ordered_steps.append(step)
            inserted.append(tool_name)

    steps = ordered_steps + extra_steps

    if allowed_set is None or "report_writer" in allowed_set:
        report_step = report_steps[0] if report_steps else _step_template("report_writer", task)
        report_step["tool_name"] = "report_writer"
        steps.append(report_step)

    for index, step in enumerate(steps, start=1):
        step["step_no"] = index

    plan["steps"] = steps
    if scenario_template:
        plan["scenario_template"] = scenario_template
    if inserted:
        notes = [str(note) for note in plan.get("notes") or []]
        notes.append(
            "Full planner template backfilled missing required tools: "
            + ", ".join(inserted)
            + "."
        )
        plan["notes"] = notes


def _apply_human_confirmation_policy(plan: dict[str, Any], task: str) -> None:
    """Keep legacy HITL prompts from making report_writer a separate approval scene."""

    for step in plan.setdefault("steps", []):
        tool_name = str(step.get("tool_name") or "")
        spec = get_tool(tool_name)
        if requires_interactive_confirmation(spec):
            step["requires_confirmation"] = True
            step.setdefault("confirmation_reason", "mcp_interactive_channel")
            step.setdefault(
                "confirmation_details",
                {
                    "tool_name": tool_name,
                    "channel": tool_channel(spec),
                    "remote_server": (spec.metadata or {}).get("remote_server") if spec else None,
                    "remote_tool_name": (spec.metadata or {}).get("remote_tool_name") if spec else None,
                },
            )
        elif tool_name != "file_reader":
            step["requires_confirmation"] = False


CONFIRMATION_NOTE_PATTERN = re.compile(
    r"(?:requires?|requiring)\s+(?:(?:human|manual|explicit)\s+)?(?:confirmation|approval)"
    r"|confirmation\s+required"
    r"|human\s+(?:approval|confirmation)"
    r"|manual\s+approval"
    r"|\u4eba\u5de5(?:\u786e\u8ba4|\u5ba1\u6279)",
    re.IGNORECASE,
)


def _synchronize_confirmation_notes(plan: dict[str, Any]) -> None:
    """Make planning notes authoritative with normalized step confirmation flags."""

    notes = [str(note) for note in plan.get("notes") or []]
    had_confirmation_note = any(CONFIRMATION_NOTE_PATTERN.search(note) for note in notes)
    notes = [note for note in notes if not CONFIRMATION_NOTE_PATTERN.search(note)]
    required_steps = [
        step for step in plan.get("steps") or [] if step.get("requires_confirmation")
    ]
    if required_steps:
        for step in required_steps:
            notes.append(
                "Confirmation required before step "
                f"{step.get('step_no')}: {step.get('tool_name')}."
            )
    elif had_confirmation_note:
        notes.append("No confirmation-required step was selected for this plan.")
    plan["notes"] = notes
