"""Dispatch new work to one Quick executor or one Deep Scope controller.

Historical result projections remain readable; adaptive reruns and rollout-based
legacy execution are deliberately not part of the runtime.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.agent.preflight import RoleAvailability, enforce_execution_readiness
from app.agent.outcome import fail_execution, result_integrity
from app.config import Settings, settings
from app.llm.base import LLMClient

def _is_quick_plan(plan: dict) -> bool:
    """Recognize the persisted Quick product contract, not a runtime fallback."""
    return str(plan.get("research_mode") or "").casefold() == "quick" or bool(plan.get("quick_mode"))

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
            "research_scope_id": run.research_scope_id,
            "engine_version": run.engine_version,
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
    """Execute an approved plan once; never switch engines after a failure."""
    from app.trace import store as _store
    from app.agent.executor import _summary

    run = _store.get_fresh_agent_run(db, run_id)
    if run is None:
        raise ValueError("Task run not found")
    if run.status in {"failed", "cancelled", "completed", "incomplete", "waiting_human", "waiting_human_plan"}:
        return _refresh_result(db, run_id, _summary(run))
    plan = json.loads(run.plan_json or "{}")
    # A plan may predate the approval/retry normalization introduced for this
    # boundary.  Detect that legacy condition but never silently change an
    # already-approved plan at execution time: move it back to review so the
    # user sees the re-bound fetch parameters before any HTTP request occurs.
    if isinstance(plan.get("steps"), list):
        from app.agent.planner import _canonical_task_url, _explicit_task_urls

        task_text = str(getattr(run, "task", "") or plan.get("task") or "")
        task_urls = set(_explicit_task_urls(task_text))
        untrusted_direct_fetch = any(
            step.get("tool_name") == "web_fetcher"
            and isinstance(step.get("arguments"), dict)
            and isinstance(step["arguments"].get("urls"), list)
            and bool(step["arguments"]["urls"])
            and not all(
                isinstance(url, str) and _canonical_task_url(url) in task_urls
                for url in step["arguments"]["urls"]
            )
            for step in plan["steps"]
            if isinstance(step, dict)
        )
        if untrusted_direct_fetch:
            from app.trace.logger import record_trace_event

            plan["plan_review_required"] = "Legacy direct fetch URLs require review against the original task."
            plan["notes"] = list(plan.get("notes") or []) + [
                "Execution blocked: legacy fetch URL plan requires review before rebinding to discovery results."
            ]
            _store.replace_agent_run_plan(db, run_id, plan)
            waiting = _store.update_agent_run_status(
                db, run_id, "waiting_human_plan", "Plan review required before executing legacy fetch URLs."
            )
            record_trace_event(
                db, run_id, 0, "plan_revalidation", "waiting_human_plan",
                {"reason": "legacy_untrusted_fetch_url"},
                "Execution blocked until the legacy fetch URL plan is reviewed.",
                {"approved_plan_changed": False, "review_required": True},
            )
            from app.agent.executor import _summary

            return _refresh_result(db, run_id, _summary(waiting))


    mode = str(plan.get("research_mode") or "auto").casefold()
    deep = not _is_quick_plan(plan) and (mode == "deep" or (
        mode == "auto" and (
            plan.get("execution_mode") in {"react", "deep_research_v2"}
            or plan.get("research_controller") == "pear"
        )
    ))
    if deep and not (settings_obj.deep_research_enabled and settings_obj.react_enabled):
        failed = fail_execution(db, run_id, ValueError("deep_research_disabled"))
        return _refresh_result(db, run_id, _summary(failed))
    plan["execution_mode"] = "react" if deep else "planned"
    plan["requested_execution_mode"] = plan["execution_mode"]
    plan["runtime_dispatch_version"] = "single-controller-v1"
    for retired in ("adaptive_gate_pending", "adaptive_phase", "adaptive_upgrade",
                    "adaptive_upgrade_reason", "adaptive_upgrade_failed", "pear_rollout",
                    "parallel_execution"):
        plan.pop(retired, None)
    _store.replace_agent_run_plan(db, run_id, plan)

    actor_available = bool(llm_client and llm_client.is_available())
    actor_provider = settings_obj.react_llm_provider or settings_obj.llm_provider
    actor_model = settings_obj.react_llm_model or settings_obj.get_llm_provider_config(actor_provider).get("model")
    writer_model = settings_obj.llm_model or settings_obj.get_llm_provider_config(settings_obj.llm_provider).get("model")
    if not enforce_execution_readiness(
        db, run_id, plan, settings_obj,
        role_availability=RoleAvailability(
            actor=actor_available,
            synthesizer=actor_available and (actor_provider, actor_model) == (settings_obj.llm_provider, writer_model),
        ),
    ):
        return _refresh_result(db, run_id, _summary(_store.get_fresh_agent_run(db, run_id)))
    try:
        if deep:
            from app.research.orchestrator import run_deep_research_v2
            result = run_deep_research_v2(db, run_id, settings_obj, actor_client=llm_client)
        else:
            result = run_plan(db, run_id, settings_obj=settings_obj, report_llm_client=llm_client)
    except Exception as exc:
        db.rollback()
        result = _summary(fail_execution(db, run_id, exc))
    return _finalize_result(db, run_id, result)
