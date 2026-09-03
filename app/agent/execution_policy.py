"""Shared run permissions and real-source guards, independent of execution mode."""
from __future__ import annotations

import json
from typing import Any, Callable

from app.config import Settings
from app.mcp.policy import tool_channel
from app.tools.base import ToolResult
from app.tools.registry import get_tool, list_tools


def allowed_tool_names(plan: dict) -> list[str]:
    explicit = plan.get("allowed_tools")
    if isinstance(explicit, list):
        return list(dict.fromkeys(str(name) for name in explicit))
    # Legacy plans without a permission list get only their own steps, never all tools.
    return list(dict.fromkeys(str(s.get("tool_name")) for s in plan.get("steps", []) if s.get("tool_name")))


def bind_run_policy(run, plan: dict) -> dict:
    """The persisted request, not an LLM-modified plan, owns these boundaries."""
    plan["source_mode"] = run.source_mode
    if run.allowed_tools_json is not None:
        try:
            allowed = json.loads(run.allowed_tools_json)
        except (TypeError, ValueError):
            allowed = []
        plan["allowed_tools"] = allowed if isinstance(allowed, list) else []
    return plan


def default_skill_tools(steps: list[dict], scenario: str) -> list[str]:
    names = list(dict.fromkeys(str(s["tool_name"]) for s in steps))
    if scenario in {"deep_web_research", "technical_docs_research", "systematic_review"}:
        candidates = {"tavily_search", "web_fetcher", "pdf_reader", "mcp_github_search",
                      "arxiv_search", "semantic_scholar_search", "openalex_search", "crossref_search"}
        for spec in list_tools():
            if (spec.enabled and spec.read_only and not spec.requires_confirmation and tool_channel(spec) == "readonly"
                and (spec.name in candidates or (spec.metadata or {}).get("tool_source") == "mcp_remote")
                and spec.name not in names):
                names.append(spec.name)
    return names


def real_sources(plan: dict) -> bool:
    return str(plan.get("source_mode", "real")).lower() not in {"mock", "offline"}


def policy_failure(code: str, message: str, *, executed: bool = False, metadata=None) -> ToolResult:
    return ToolResult(success=False, output={}, error_message=message, output_summary=message,
        metadata={**(metadata or {}), "error_type": code, "executed": executed})


def argument_violation(name: str, arguments: dict, plan: dict, settings: Settings) -> ToolResult | None:
    if name not in allowed_tool_names(plan):
        return policy_failure("disallowed_tool", f"Tool '{name}' is outside this run's permission list.")
    spec = get_tool(name)
    if spec is None or not spec.enabled:
        return policy_failure("unavailable", f"Tool '{name}' is not registered or enabled.")
    if real_sources(plan):
        if settings.offline_mode:
            return policy_failure("source_mode_violation", "Real research cannot execute in global offline mode.")
        if str(arguments.get("mode", "")).lower() in {"mock", "offline", "fallback"}:
            return policy_failure("source_mode_violation", "Real research cannot request mock/offline/fallback data.")
    return None


def _contains_demonstration(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_demonstration(item) for item in value)
    if not isinstance(value, dict):
        return False
    if (str(value.get("data_source", "")).lower() in {"mock", "fallback", "offline"}
        or str(value.get("source_type", "")).lower() in {"mock", "fallback", "offline"}
        or str(value.get("mode", "")).lower() in {"mock", "offline"}
        or value.get("is_mock") is True or value.get("is_fallback") is True
        or value.get("fallback_used") is True):
        return True
    # Examine structured provenance, not arbitrary text that happens to mention mock.
    return any(_contains_demonstration(value.get(key)) for key in
               ("metadata", "results", "pages", "papers", "documents", "evidence_items"))


def execute_with_policy(name: str, arguments: dict, plan: dict, settings: Settings,
                        execute: Callable[[str, dict], ToolResult], *, budget_reserved: bool = False) -> ToolResult:
    violation = argument_violation(name, arguments, plan, settings)
    if violation is not None:
        return violation
    from app.agent.budget import BudgetExceeded, current_budget, reserve_tool
    if not budget_reserved:
        try:
            reserve_tool(name)
        except BudgetExceeded as exc:
            return policy_failure("budget_exhausted", str(exc), metadata={"budget_reason": exc.reason})
    prepared = dict(arguments)
    if name == "mcp_github_search":
        # A configured mock default must not override a real run (or vice versa).
        prepared["mode"] = "public_api" if real_sources(plan) else "mock"
    if (plan.get("execution_mode") == "react" or current_budget() is not None) and name in {"mcp_github_search", "tavily_search"}:
        # The ReAct recovery loop owns retries; prevent nested transport retries.
        prepared["_max_transport_retries"] = 0
    result = execute(name, prepared)
    if real_sources(plan) and (_contains_demonstration(result.metadata) or _contains_demonstration(result.output)):
        return policy_failure("source_mode_violation", "Demonstration/fallback output rejected for real research.",
            executed=True, metadata={"rejected_source_type": result.metadata.get("data_source"),
                                     "original_error_type": result.metadata.get("original_error_type"),
                                     "http_status": result.metadata.get("http_status"),
                                     "retry_after_seconds": result.metadata.get("retry_after_seconds"),
                                     "retry_count": result.metadata.get("retry_count", 0)})
    return result
