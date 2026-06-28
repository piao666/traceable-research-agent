"""Select the stable planned executor or the optional ReAct executor."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.config import Settings, settings
from app.llm.base import LLMClient


def run_task_by_mode(
    db: Session,
    run_id: str,
    settings_obj: Settings = settings,
    llm_client: LLMClient | None = None,
) -> dict:
    """Dispatch to ReAct or Planned executor.

    Priority (highest first):
      1. plan["execution_mode"] — written by Planner from Streamlit UI override
      2. settings_obj.execution_mode — from .env / global config
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

    effective_mode = plan_mode or settings_obj.execution_mode
    if effective_mode == "react" and settings_obj.react_enabled:
        from app.agent.react_executor import run_react_task
        return run_react_task(db, run_id, settings_obj, llm_client=llm_client)
    if effective_mode == "planned" and settings_obj.parallel_execution_enabled:
        from app.agent.parallel_executor import run_plan_parallel
        return run_plan_parallel(db, run_id, settings_obj)
    return run_plan(db, run_id)
