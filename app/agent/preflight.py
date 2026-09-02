"""Non-network, non-secret readiness checks shared by every execution entry."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.trace import store
from app.trace.logger import record_trace_event


def capability_summary(settings: Settings) -> dict[str, Any]:
    """Presence is not a connectivity test. Never return endpoints or credentials."""
    provider = settings.llm_provider
    react_provider = settings.react_llm_provider or provider
    return {
        "offline_mode": settings.offline_mode,
        "tavily_configured": bool(settings.tavily_api_key),
        "llm_provider": provider,
        "llm_configured": bool(settings.get_llm_api_key(provider)),
        "react_provider": react_provider,
        "react_configured": bool(settings.get_llm_api_key(react_provider)),
        "react_enabled": settings.react_enabled,
        "deep_research_enabled": settings.deep_research_enabled,
        "report_generation_mode": settings.report_generation_mode,
        "connectivity_verified": False,
    }


def check_plan_readiness(
    plan: dict[str, Any], settings: Settings, *, llm_available: bool = False,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    warnings: list[str] = []
    tools = {str(step.get("tool_name") or "") for step in plan.get("steps", [])}

    def missing(code: str, capability: str, variable: str, message: str) -> None:
        blockers.append({"code": code, "capability": capability,
                         "environment_variable": variable, "message": message})

    if "tavily_search" in tools and not settings.tavily_search_enabled:
        missing("capability_disabled", "tavily_search", "TAVILY_SEARCH_ENABLED",
                "TAVILY_SEARCH_ENABLED is false; this plan requires Tavily search.")
    elif "tavily_search" in tools and not settings.offline_mode and not settings.tavily_api_key:
        missing("missing_configuration", "tavily_search", "TAVILY_API_KEY",
                "TAVILY_API_KEY is not configured; Web search cannot start.")
    mode = plan.get("execution_mode") or "planned"
    if mode == "react":
        provider = settings.react_llm_provider or settings.llm_provider
        if not settings.react_enabled:
            missing("capability_disabled", "react", "REACT_ENABLED", "ReAct execution is disabled.")
        elif not llm_available and not settings.get_llm_api_key(provider):
            variable = settings.get_llm_provider_config(provider).get("api_key_env_name") or "LLM_PROVIDER"
            missing("missing_configuration", "react", variable,
                    f"{variable} is not configured; selected ReAct provider is unavailable.")
    elif settings.react_enabled and not settings.get_llm_api_key(settings.react_llm_provider or settings.llm_provider) and not llm_available:
        warnings.append("Optional adaptive ReAct upgrade is unavailable; planned research remains available.")
    if settings.report_generation_mode == "llm" and not llm_available and not settings.get_llm_api_key(settings.llm_provider):
        variable = settings.get_llm_provider_config(settings.llm_provider).get("api_key_env_name") or "LLM_PROVIDER"
        missing("missing_configuration", "report_synthesis", variable,
                f"{variable} is not configured; selected LLM report mode is unavailable.")
    if settings.offline_mode:
        warnings.append("Offline mode uses demonstration sources, not live external research.")
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings,
            "capabilities": capability_summary(settings)}


def enforce_execution_readiness(
    db: Session, run_id: str, plan: dict[str, Any], settings: Settings,
    *, llm_available: bool = False, decision_tool: str | None = None,
) -> bool:
    """Last-line guard for workers/direct executors (HTTP preflight preserves drafts)."""
    checked_plan = {**plan, "steps": [{"tool_name": decision_tool}]} if decision_tool else plan
    result = check_plan_readiness(checked_plan, settings, llm_available=llm_available)
    plan["preflight"] = result
    store.replace_agent_run_plan(db, run_id, plan)
    run = store.get_fresh_agent_run(db, run_id)
    if run is None or run.status in {"cancelled", "failed", "completed"}:
        return False
    snapshot = settings.get_safe_runtime_config_summary()
    snapshot.update({key: plan.get(key) for key in ("retrieval_profile", "profile_constraints", "policy_version")})
    run.run_config_snapshot = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    db.commit()
    if result["ready"]:
        return True
    message = " ".join(issue["message"] for issue in result["blockers"])
    record_trace_event(db, run_id, run.current_step, "execution_preflight", "failed",
                       {}, message, result, error_message=message)
    plan["research_outcome"] = {
        "version": "research-integrity-v1", "status": "failed",
        "error_code": "configuration_not_ready", "effective_evidence_count": 0,
        "warnings": result["warnings"], "message": message,
    }
    plan["adaptive_gate_pending"] = False
    plan["deepening_pending"] = False
    store.replace_agent_run_plan(db, run_id, plan)
    if not store.is_agent_run_cancelled(db, run_id):
        store.update_agent_run_status(db, run_id, "failed", message)
    return False
