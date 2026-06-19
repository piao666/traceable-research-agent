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
    if settings_obj.execution_mode == "react" and settings_obj.react_enabled:
        from app.agent.react_executor import run_react_task

        return run_react_task(db, run_id, settings_obj, llm_client=llm_client)
    return run_plan(db, run_id)
