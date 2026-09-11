"""R12.1 result-boundary resolver contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.improvement import models as improvement_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.research.models import ResearchScope
from app.research.result_context import resolve_research_result
from app.trace.models import AgentRun


def _run(run_id: str, **updates) -> AgentRun:
    values = {
        "run_id": run_id,
        "task": "fixture",
        "report_type": "summary",
        "source_mode": "mock",
        "status": "completed",
        "root_run_id": run_id,
        "run_role": "root",
        "engine_version": "legacy",
    }
    values.update(updates)
    return AgentRun(**values)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_ordinary_root_run_resolves_to_itself(db: Session) -> None:
    db.add(_run("ordinary"))
    db.commit()

    result = resolve_research_result(db, "ordinary")

    assert result.requested_run_id == "ordinary"
    assert result.root_run_id == "ordinary"
    assert result.is_scope is False
    assert result.scope_id is None
    assert result.engine_version == "legacy"
    assert result.member_run_ids == ("ordinary",)


def test_deep_root_and_child_resolve_the_same_scope(db: Session) -> None:
    now = datetime.now(timezone.utc)
    root = _run(
        "deep-root",
        research_scope_id="scope-fixture",
        engine_version="v2",
        status="running",
        created_at=now,
    )
    child = _run(
        "deep-child",
        root_run_id=root.run_id,
        parent_run_id=root.run_id,
        run_role="child",
        research_scope_id="scope-fixture",
        engine_version="v2",
        created_at=now + timedelta(seconds=1),
    )
    db.add_all([root, child])
    db.flush()
    db.add(
        ResearchScope(
            scope_id="scope-fixture",
            root_run_id=root.run_id,
            engine_version="v2",
            status="running",
            research_contract_json="{}",
        )
    )
    db.commit()

    root_result = resolve_research_result(db, root.run_id)
    child_result = resolve_research_result(db, child.run_id)

    assert root_result.scope_id == child_result.scope_id == "scope-fixture"
    assert root_result.root_run_id == child_result.root_run_id == root.run_id
    assert root_result.is_scope and child_result.is_scope
    assert root_result.member_run_ids == child_result.member_run_ids == (
        root.run_id,
        child.run_id,
    )
    assert child_result.requested_run_id == child.run_id


def test_scope_members_are_root_first_then_stable(db: Session) -> None:
    now = datetime.now(timezone.utc)
    root = _run(
        "root",
        research_scope_id="scope-order",
        engine_version="v2",
        created_at=now + timedelta(seconds=2),
    )
    child_b = _run(
        "child-b",
        root_run_id="root",
        parent_run_id="root",
        run_role="child",
        research_scope_id="scope-order",
        engine_version="v2",
        created_at=now + timedelta(seconds=1),
    )
    child_a = _run(
        "child-a",
        root_run_id="root",
        parent_run_id="root",
        run_role="child",
        research_scope_id="scope-order",
        engine_version="v2",
        created_at=now,
    )
    db.add_all([root, child_b, child_a])
    db.flush()
    db.add(
        ResearchScope(
            scope_id="scope-order",
            root_run_id="root",
            engine_version="v2",
            status="completed",
            research_contract_json="{}",
        )
    )
    db.commit()

    result = resolve_research_result(db, "child-b")

    assert result.member_run_ids == ("root", "child-a", "child-b")


def test_nonexistent_run_is_rejected(db: Session) -> None:
    with pytest.raises(ValueError, match="Task run not found"):
        resolve_research_result(db, "missing")
