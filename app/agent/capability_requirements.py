"""Task capability requirements and conservative admission.

Configuration is local truth; probe results are runtime truth.  A configured
capability whose endpoint has not been probed remains executable (the normal
executor/final integrity gate decides whether it worked), but is reported as
unverified rather than admitted as healthy.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import Settings

# A requirement can be fulfilled by any member of its alternatives.  The
# alternatives describe existing registry/retrieval capabilities only.
ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "search": ("tavily_search", "search"),
    "full_text": ("web_fetcher", "browser", "remote_extract"),
    "browser": ("browser", "remote_extract"),
    "pdf": ("pdf_reader",),
    "academic": ("academic_search", "openalex_search", "crossref_search", "arxiv_search", "semantic_scholar_search"),
    "structured_data": ("structured_data", "web_fetcher", "file_reader", "sql_query"),
}


def task_required_capabilities(plan: dict[str, Any]) -> set[str]:
    """Return requirements from the persisted contract and explicit task text."""
    contract = plan.get("task_contract") or {}
    raw = contract.get("required_capabilities")
    if raw is None:
        raw = plan.get("required_capabilities")
    required: set[str] = set()
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    if isinstance(raw, (list, tuple, set)):
        required.update(str(value).strip().lower() for value in raw if str(value).strip())

    task = str(contract.get("original_task") or plan.get("task") or "")
    # Explicit requirements are intentionally conservative.  Generic web
    # research still uses the plan's tool list and does not become browser-only.
    if re.search(r"(?:检索|查找|搜索|search|find).*(?:论文|文献|doi|arxiv|academic|papers?)", task, re.I):
        required.add("academic")
    if re.search(r"\bpdf\b|pdf文件|pdf文档|pdf格式", task, re.I):
        required.add("pdf")
    if re.search(r"javascript渲染|动态渲染|js渲染|浏览器渲染|browser rendering|需要执行脚本", task, re.I):
        required.add("browser")
    if re.search(r"csv|xlsx|数据集|数据表|结构化数据|structured data", task, re.I):
        required.add("structured_data")
    if re.search(r"全文|完整文章|full[- ]text|原文|正文", task, re.I):
        required.add("full_text")
    # A search requirement is only inferred when the plan has a search step;
    # this avoids blocking local file/SQL tasks.
    tools = {str(step.get("tool_name") or "") for step in plan.get("steps") or []
             if step.get("required") is not False}
    tools.update(str(name) for name in plan.get("required_tools") or [])
    if "tavily_search" in tools:
        required.add("search")
    return required


def _capability_candidates(items: list[dict[str, Any]], settings: Settings,
                           allowed: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Match backend facts to registry permissions without inventing health."""
    observed = {str(row.get("name")): row for row in items}

    def candidate(tool_name: str, configured: bool, *probe_names: str) -> dict[str, Any]:
        if tool_name not in allowed or not configured:
            return {}
        row = next((dict(observed[name]) for name in probe_names if name in observed), {})
        return {"configured": True, "verification": "unknown", "usable": True, **row}

    search = candidate("tavily_search", bool(settings.offline_mode or (
        settings.tavily_search_enabled and settings.tavily_api_key)),
        "tavily_search", settings.search_provider)
    http = candidate("web_fetcher", True, "http")
    browser_enabled = settings.fetch_router_enabled and (
        settings.fetch_browser_enabled or settings.web_fetcher_playwright_enabled)
    browser = candidate("web_fetcher", browser_enabled, "browser")
    remote_configured = False
    if "web_fetcher" in allowed and settings.fetch_router_enabled and settings.fetch_remote_extract_enabled:
        from app.retrieval.remote_extract import configured_remote_providers
        remote_configured = any(provider.available() for provider in configured_remote_providers(
            provider_order=settings.fetch_remote_extract_provider_order))
    remote = candidate("web_fetcher", remote_configured, "remote_extract")
    # A router probe describes its actual backend, not all possible backends.
    router_probe = observed.get("web_fetcher") or {}
    actual_backend = router_probe.get("fetch_backend") or "http"
    target = {"http": http, "cache": http, "browser": browser,
              "remote_extract": remote}.get(actual_backend)
    if target and router_probe and (
        router_probe.get("verification") == "probed"
        or target.get("verification") != "probed"
    ):
        target.update(router_probe)
    pdf = candidate("pdf_reader", settings.pdf_reader_enabled, "pdf_reader")
    academic = [candidate(name, enabled, name) for name, enabled in (
        ("openalex_search", settings.openalex_search_enabled),
        ("crossref_search", settings.crossref_search_enabled),
        ("arxiv_search", True), ("semantic_scholar_search", True))]
    local_data = [candidate(name, True, name) for name in ("file_reader", "sql_query")]
    return {
        "search": [search], "full_text": [http, browser, remote, candidate("file_reader", True, "file_reader")],
        # Remote extraction alone does not promise JavaScript execution.
        "browser": [browser], "pdf": [pdf], "academic": academic,
        "structured_data": [*local_data, http, browser, remote],
    }


def admit_task_capabilities(plan: dict[str, Any], settings: Settings,
                             items: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """Known missing/unavailable capabilities block; unknown permits an attempt.

    Only trusted server-side configuration/probes enter this function. A probe
    from a single URL never proves global failure unless it is tool-scoped.
    """
    from app.agent.execution_policy import allowed_tool_names
    candidates_by_requirement = _capability_candidates(items, settings, set(allowed_tool_names(plan)))
    # Permission to read local files is not evidence that a local copy of a
    # requested webpage exists. Only a planned local read can satisfy full text.
    if not any(step.get("tool_name") == "file_reader" for step in plan.get("steps") or []):
        candidates_by_requirement["full_text"] = candidates_by_requirement["full_text"][:3]
    blockers: list[dict[str, str]] = []
    warnings: list[str] = []
    for requirement in sorted(task_required_capabilities(plan)):
        candidates = [row for row in candidates_by_requirement.get(requirement, []) if row.get("configured")]
        if not candidates:
            blockers.append({"code": "capability_missing", "capability": requirement,
                "environment_variable": "runtime_settings",
                "message": f"Task requires '{requirement}', but no permitted backend is configured."})
        elif all(row.get("verification") == "probed" and row.get("usable") is False for row in candidates):
            blockers.append({"code": "capability_unavailable", "capability": requirement,
                "environment_variable": "runtime_preflight",
                "message": f"All permitted backends for '{requirement}' were probed and are unavailable."})
        elif not any(row.get("verification") == "probed" and row.get("usable") is True for row in candidates):
            warnings.append(f"Capability '{requirement}' is configured but unverified; execution must establish usable evidence.")
    return blockers, warnings
