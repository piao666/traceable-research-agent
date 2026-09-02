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
from app.agent.preflight import enforce_execution_readiness
from app.agent.outcome import fail_execution, result_integrity
from app.config import Settings, settings
from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

# ── Adaptive gate thresholds ──────────────────────────────────────────
_MIN_CITATIONS_FOR_ADAPTIVE = 3
_MIN_AUDITABILITY_FOR_ADAPTIVE = 5.0
_MAX_UNRESOLVED_FOR_ADAPTIVE = 0


def _adaptive_upgrade_reason(db: Session, run_id: str) -> str | None:
    """Return a user-visible reason when planned output needs deeper research."""
    from app.trace import store as _store
    run = _store.get_agent_run(db, run_id)
    if run is None or run.status not in {"running", "completed"} or not run.report_path:
        return None

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
        return "Planned 执行质量不足，自动升级为 ReAct 深入探索（" + ", ".join(reasons) + "）。"
    return None


def _refresh_result(db: Session, run_id: str, result: dict) -> dict:
    """Synchronize an executor summary with the final persisted run and plan."""
    from app.trace import store as _store

    refreshed = dict(result)
    run = _store.get_fresh_agent_run(db, run_id)
    if run is None:
        return refreshed
    refreshed.update(
        {
            **result_integrity(run),
            "status": run.status,
            "current_step": run.current_step,
            "total_steps": run.total_steps,
            "total_tool_calls": run.total_tool_calls,
            "error_message": run.error_message,
        }
    )
    try:
        plan = json.loads(run.plan_json or "{}")
    except (json.JSONDecodeError, TypeError):
        plan = {}
    refreshed.update(
        {
            "execution_mode": plan.get("execution_mode") or "planned",
            "requested_execution_mode": plan.get("requested_execution_mode")
            or plan.get("execution_mode")
            or "planned",
            "planner_source": plan.get("planner_source"),
            "adaptive_upgrade": bool(plan.get("adaptive_upgrade")),
            "adaptive_upgrade_reason": plan.get("adaptive_upgrade_reason"),
            "adaptive_upgrade_failed": bool(plan.get("adaptive_upgrade_failed")),
            "adaptive_phase": plan.get("adaptive_phase"),
            "deepening_pending": bool(plan.get("deepening_pending")),
            "deepening_phase": plan.get("deepening_phase"),
        }
    )
    return refreshed


def _finalize_result(db: Session, run_id: str, result: dict) -> dict:
    """Run local learning hooks only after the final terminal result exists."""
    from app.improvement.lifecycle import finalize_improvement_cycle

    finalize_improvement_cycle(db, run_id)
    return _refresh_result(db, run_id, result)


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
    from app.trace import store as _store
    run = _store.get_agent_run(db, run_id)
    plan: dict = {}
    plan_mode: str | None = None
    if run is not None:
        try:
            plan = json.loads(run.plan_json or "{}")
            plan_mode = plan.get("execution_mode") or None
        except Exception:
            plan_mode = None

    effective_mode = plan_mode or "planned"
    if run is not None and run.status in {"failed", "cancelled", "completed", "waiting_human", "waiting_human_plan"}:
        from app.agent.executor import _summary
        return _refresh_result(db, run_id, _summary(run))
    if run is not None and run.status not in {"failed", "cancelled", "completed", "waiting_human", "waiting_human_plan"}:
        if not enforce_execution_readiness(db, run_id, plan, settings_obj,
                                          llm_available=bool(llm_client and llm_client.is_available())):
            from app.agent.executor import _summary
            return _refresh_result(db, run_id, _summary(_store.get_fresh_agent_run(db, run_id)))
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
        adaptive_requested_mode = (
            plan.get("requested_execution_mode") if plan.get("adaptive_upgrade") else None
        )
        try:
            if settings_obj.deep_research_enabled:
                from app.agent.deepening import run_deepening
                result = run_deepening(db, run_id, settings_obj, llm_client=llm_client)
            else:
                result = run_react_task(db, run_id, settings_obj, llm_client=llm_client)
            if adaptive_requested_mode:
                final_run = _store.get_fresh_agent_run(db, run_id)
                if final_run is not None:
                    final_plan = json.loads(final_run.plan_json or "{}")
                    final_plan["requested_execution_mode"] = adaptive_requested_mode
                    final_plan["adaptive_upgrade"] = True
                    final_plan["adaptive_gate_pending"] = False
                    final_plan["adaptive_phase"] = (
                        final_run.status
                        if final_run.status in {"completed", "failed", "cancelled"}
                        else "react_execution"
                    )
                    _store.replace_agent_run_plan(db, run_id, final_plan)
            return _finalize_result(db, run_id, result)
        except Exception as exc:
            successful = any(trace.status == "success" for trace in _store.list_tool_traces(db, run_id))
            if not settings_obj.react_fallback_to_planned or successful or run is None:
                db.rollback()
                failed = fail_execution(db, run_id, exc)
                from app.agent.executor import _summary
                return _finalize_result(db, run_id, _summary(failed))
            plan["requested_execution_mode"] = adaptive_requested_mode or "react"
            plan["execution_mode"] = "planned"
            plan["react_state"] = {**(plan.get("react_state") or {}), "fallback_used": True}
            routing = dict(plan.get("execution_routing") or {})
            routing.update({"selected": "planned", "fallback": f"ReAct 启动失败，降级为固定计划：{type(exc).__name__}"})
            plan["execution_routing"] = routing
            _store.replace_agent_run_plan(db, run_id, plan)
            result = run_plan(db, run_id, settings_obj=settings_obj)
            if adaptive_requested_mode:
                fallback_run = _store.get_fresh_agent_run(db, run_id)
                if fallback_run is not None:
                    fallback_plan = json.loads(fallback_run.plan_json or "{}")
                    fallback_plan["requested_execution_mode"] = adaptive_requested_mode
                    fallback_plan["adaptive_upgrade"] = True
                    fallback_plan["adaptive_gate_pending"] = False
                    fallback_plan["adaptive_phase"] = (
                        fallback_run.status
                        if fallback_run.status in {"completed", "failed", "cancelled"}
                        else "planned_execution"
                    )
                    _store.replace_agent_run_plan(db, run_id, fallback_plan)
            return _finalize_result(db, run_id, result)

    adaptive_candidate = bool(effective_mode == "planned" and settings_obj.react_enabled
                              and (llm_client is not None or settings_obj.get_llm_api_key(
                                  settings_obj.react_llm_provider or settings_obj.llm_provider)))
    if run is not None:
        plan["parallel_execution"] = bool(
            effective_mode == "planned" and settings_obj.parallel_execution_enabled
        )
        if adaptive_candidate:
            plan["adaptive_gate_pending"] = True
            plan["adaptive_phase"] = "planned_execution"
            plan.pop("adaptive_upgrade_failed", None)
        _store.replace_agent_run_plan(db, run_id, plan)

    completion_status = "running" if adaptive_candidate else "completed"
    if effective_mode == "planned" and settings_obj.parallel_execution_enabled:
        from app.agent.parallel_executor import run_plan_parallel
        result = run_plan_parallel(
            db,
            run_id,
            settings_obj,
            completion_status=completion_status,
            report_llm_client=llm_client,
        )
    else:
        result = run_plan(
            db,
            run_id,
            settings_obj=settings_obj,
            completion_status=completion_status,
            report_llm_client=llm_client,
        )

    current = _store.get_fresh_agent_run(db, run_id)
    if not adaptive_candidate or current is None or current.status != "running" or not current.report_path:
        if adaptive_candidate and current is not None and current.status in {"failed", "cancelled"}:
            terminal_plan = json.loads(current.plan_json or "{}")
            terminal_plan["adaptive_gate_pending"] = False
            terminal_plan["adaptive_phase"] = current.status
            _store.replace_agent_run_plan(db, run_id, terminal_plan)
        return _finalize_result(db, run_id, result)

    # ── Adaptive gate: upgrade planned → ReAct if quality insufficient ──
    upgrade_reason = _adaptive_upgrade_reason(db, run_id)
    if upgrade_reason:
        original_requested_mode = plan.get("requested_execution_mode") or "planned"
        try:
            from app.agent.react_executor import run_react_task
            run = _store.get_agent_run(db, run_id)
            if run is not None:
                plan = json.loads(run.plan_json or "{}")
                plan["execution_mode"] = "react"
                plan["adaptive_upgrade"] = True
                plan["adaptive_gate_pending"] = False
                plan["adaptive_phase"] = "react_execution"
                plan["adaptive_upgrade_reason"] = upgrade_reason
                _store.replace_agent_run_plan(db, run_id, plan)
            result = run_react_task(db, run_id, settings_obj, llm_client=llm_client)
            final_run = _store.get_fresh_agent_run(db, run_id)
            if final_run is not None and final_run.plan_json:
                final_plan = json.loads(final_run.plan_json)
                final_plan["requested_execution_mode"] = original_requested_mode
                final_plan["adaptive_upgrade"] = True
                final_plan["adaptive_gate_pending"] = False
                final_plan["adaptive_phase"] = (
                    final_run.status
                    if final_run.status in {"completed", "failed", "cancelled"}
                    else "react_execution"
                )
                final_plan["adaptive_upgrade_reason"] = upgrade_reason
                _store.replace_agent_run_plan(db, run_id, final_plan)
            return _finalize_result(db, run_id, result)
        except Exception as exc:
            logger.warning("Adaptive ReAct upgrade failed, returning planned result.", exc_info=True)
            failed_run = _store.get_fresh_agent_run(db, run_id)
            failed_plan = json.loads(failed_run.plan_json or "{}") if failed_run else dict(plan)
            failed_plan["execution_mode"] = "planned"
            failed_plan["requested_execution_mode"] = original_requested_mode
            failed_plan["adaptive_upgrade"] = True
            failed_plan["adaptive_upgrade_failed"] = True
            failed_plan["adaptive_gate_pending"] = False
            failed_plan["adaptive_phase"] = "completed"
            failed_plan["adaptive_upgrade_reason"] = upgrade_reason
            failed_plan["adaptive_upgrade_error"] = type(exc).__name__
            _store.replace_agent_run_plan(db, run_id, failed_plan)
            from app.trace.logger import record_trace_event
            record_trace_event(db, run_id, 0, "adaptive_upgrade", "failed", {},
                               "Optional ReAct upgrade failed; rechecking the planned result.",
                               {"error_type": type(exc).__name__})
            result = run_plan(db, run_id, settings_obj=settings_obj, report_llm_client=llm_client)
            return _finalize_result(db, run_id, result)

    final_run = _store.get_fresh_agent_run(db, run_id)
    if final_run is not None and final_run.status in {"failed", "cancelled"}:
        return _finalize_result(db, run_id, result)
    final_plan = json.loads(final_run.plan_json or "{}") if final_run else dict(plan)
    final_plan["adaptive_gate_pending"] = False
    final_plan["adaptive_phase"] = "completed"
    _store.replace_agent_run_plan(db, run_id, final_plan)
    _store.update_agent_run_status(db, run_id, "completed", None)
    return _finalize_result(db, run_id, result)
