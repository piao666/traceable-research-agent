"""Select the stable planned executor or the optional ReAct executor.

When planned execution finishes with insufficient quality (low citations,
poor auditability, unresolved claims), the dispatcher automatically upgrades
to ReAct for deeper exploration — adaptive hybrid mode.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.config import Settings, settings
from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

# ── Adaptive gate thresholds ──────────────────────────────────────────
_MIN_CITATIONS_FOR_ADAPTIVE = 3
_MIN_AUDITABILITY_FOR_ADAPTIVE = 5.0
_MAX_UNRESOLVED_FOR_ADAPTIVE = 0


def _should_upgrade_to_react(db: Session, run_id: str) -> bool:
    """Evaluate planned execution quality and decide whether to upgrade."""
    from app.trace import store as _store
    run = _store.get_agent_run(db, run_id)
    if run is None or run.status != "completed":
        return False

    citations = getattr(run, "citation_total", 0) or 0
    accuracy = getattr(run, "citation_accuracy", 0.0) or 0.0
    unsupported = getattr(run, "citation_unsupported", 0) or 0
    auditability = (
        min(citations / 10, 1.0) * 5 + accuracy * 3
        if citations > 0 else 3.0
    )

    reasons: list[str] = []
    if citations < _MIN_CITATIONS_FOR_ADAPTIVE:
        reasons.append(f"citations={citations}<{_MIN_CITATIONS_FOR_ADAPTIVE}")
    if auditability < _MIN_AUDITABILITY_FOR_ADAPTIVE:
        reasons.append(f"auditability={auditability:.1f}<{_MIN_AUDITABILITY_FOR_ADAPTIVE}")
    if unsupported > _MAX_UNRESOLVED_FOR_ADAPTIVE:
        reasons.append(f"unsupported={unsupported}>{_MAX_UNRESOLVED_FOR_ADAPTIVE}")

    if reasons:
        logger.info("Adaptive gate: upgrading planned→ReAct (%s)", ", ".join(reasons))
        return True
    return False


def run_task_by_mode(
    db: Session,
    run_id: str,
    settings_obj: Settings = settings,
    llm_client: LLMClient | None = None,
) -> dict:
    """Dispatch to ReAct or Planned executor.

    The Planner writes an automatic execution route into the persisted plan.
    ReAct falls back to the stable planned executor when it is disabled or
    cannot start before any successful tool observation is recorded.
    """
    import json
    from app.trace import store as _store
    run = _store.get_agent_run(db, run_id)
    plan_mode: str | None = None
    if run is not None:
        try:
            plan = json.loads(run.plan_json or "{}")
            plan_mode = plan.get("execution_mode") or None
        except Exception:
            plan_mode = None

    effective_mode = plan_mode or "planned"
    if effective_mode == "react" and not settings_obj.react_enabled:
        if run is not None:
            plan["requested_execution_mode"] = "react"
            plan["execution_mode"] = "planned"
            routing = dict(plan.get("execution_routing") or {})
            routing.update({"selected": "planned", "fallback": "ReAct 未启用，降级为固定计划。"})
            plan["execution_routing"] = routing
            _store.replace_agent_run_plan(db, run_id, plan)
        effective_mode = "planned"
    if effective_mode == "react" and settings_obj.react_enabled:
        from app.agent.react_executor import run_react_task
        try:
            if settings_obj.deep_research_enabled:
                from app.agent.deepening import run_deepening
                return run_deepening(db, run_id, settings_obj, llm_client=llm_client)
            return run_react_task(db, run_id, settings_obj, llm_client=llm_client)
        except Exception as exc:
            successful = any(trace.status == "success" for trace in _store.list_tool_traces(db, run_id))
            if not settings_obj.react_fallback_to_planned or successful or run is None:
                raise
            plan["requested_execution_mode"] = "react"
            plan["execution_mode"] = "planned"
            routing = dict(plan.get("execution_routing") or {})
            routing.update({"selected": "planned", "fallback": f"ReAct 启动失败，降级为固定计划：{type(exc).__name__}"})
            plan["execution_routing"] = routing
            _store.replace_agent_run_plan(db, run_id, plan)
            return run_plan(db, run_id, settings_obj=settings_obj)
    if effective_mode == "planned" and settings_obj.parallel_execution_enabled:
        from app.agent.parallel_executor import run_plan_parallel
        result = run_plan_parallel(db, run_id, settings_obj)
    else:
        result = run_plan(db, run_id, settings_obj=settings_obj)

    # ── Adaptive gate: upgrade planned → ReAct if quality insufficient ──
    if (
        effective_mode == "planned"
        and settings_obj.react_enabled
        and _should_upgrade_to_react(db, run_id)
    ):
        try:
            from app.agent.react_executor import run_react_task
            from app.trace import store as _store
            run = _store.get_agent_run(db, run_id)
            if run is not None:
                plan = json.loads(run.plan_json or "{}")
                plan["execution_mode"] = "react"
                plan["adaptive_upgrade"] = True
                plan["adaptive_upgrade_reason"] = "planned 执行质量不足，自动升级为 ReAct 深入探索。"
                _store.replace_agent_run_plan(db, run_id, plan)
                _store.update_agent_run_status(db, run_id, "running", None)
            return run_react_task(db, run_id, settings_obj, llm_client=llm_client)
        except Exception:
            logger.warning("Adaptive ReAct upgrade failed, returning planned result.", exc_info=True)
    return result
