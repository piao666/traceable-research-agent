from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.improvement import models as improvement_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.research import models as research_models  # noqa: F401
from app.trace import store
from app.trace.logger import record_trace_event


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def r12_settings(tmp_path) -> Settings:
    return Settings(
        evidence_artifact_root=str(tmp_path / "artifacts"),
        evidence_reasoning_enabled=False,
        deep_research_enabled=True,
        research_profile="deep",
        report_generation_mode="deterministic",
        react_fallback_to_planned=False,
        research_max_tool_calls=80,
        research_max_llm_calls=64,
        research_max_tokens=200000,
        research_max_seconds=1800,
    )


def create_root(db: Session, task: str = "Compare traceable research systems"):
    run = store.create_agent_run(db, task, "summary", "real", allowed_tools=["web_fetcher"])
    store.update_agent_run_plan(
        db,
        run.run_id,
        {
            "version": "test",
            "task": task,
            "execution_mode": "react",
            "allowed_tools": ["web_fetcher"],
            "steps": [],
            "task_contract": {
                "version": "task-contract-v1",
                "goal_kind": "research",
                "original_task": task,
                "unresolved_fields": [],
            },
        },
    )
    return store.get_agent_run(db, run.run_id)


def add_web_trace(db: Session, run_id: str, text: str, suffix: str):
    return record_trace_event(
        db,
        run_id,
        1,
        "web_fetcher",
        "success",
        {"urls": [f"https://example.com/{suffix}"]},
        text[:120],
        {
            "pages": [
                {
                    "url": f"https://example.com/{suffix}",
                    "canonical_url": f"https://example.com/{suffix}",
                    "title": f"Source {suffix}",
                    "content": text,
                    "content_basis": "full_text",
                    "extraction_method": "fixture",
                    "extraction_confidence": 0.95,
                    "fetch_status": "success",
                }
            ],
            "fetched_count": 1,
            "failed_count": 0,
            "total_count": 1,
        },
    )


def materialize_run(db: Session, run, settings: Settings):
    from app.agent.outcome import load_observations
    from app.evidence.service import materialize_execution_provenance

    traces = store.list_tool_traces(db, run.run_id)
    return materialize_execution_provenance(
        db,
        run,
        json.loads(run.plan_json or "{}"),
        load_observations(traces),
        traces,
        settings,
    )
