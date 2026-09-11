"""Resolve the user-visible research result boundary for runs and scopes."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.evidence.scope_service import get_scope_provenance_bundle
from app.evidence.service import get_provenance_bundle
from app.research.scope import (
    list_scope_runs,
    list_scope_traces,
    resolve_research_scope,
)
from app.trace.models import AgentRun, ToolTrace
from app.trace.store import list_tool_traces


@dataclass(frozen=True)
class ResearchResultContext:
    requested_run_id: str
    root_run_id: str
    is_scope: bool
    scope_id: str | None
    engine_version: str
    member_run_ids: tuple[str, ...]


def resolve_research_result(
    db: Session,
    run_id: str,
) -> ResearchResultContext:
    """Resolve an ordinary run or any member of a Deep Research scope."""

    run = db.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Task run not found")
    scope = resolve_research_scope(db, run_id)
    if scope is None:
        if run.research_scope_id is not None:
            raise ValueError("Research scope not found")
        return ResearchResultContext(
            requested_run_id=run_id,
            root_run_id=run_id,
            is_scope=False,
            scope_id=None,
            engine_version=run.engine_version,
            member_run_ids=(run_id,),
        )
    members = tuple(item.run_id for item in list_scope_runs(db, scope.scope_id))
    return ResearchResultContext(
        requested_run_id=run_id,
        root_run_id=scope.root_run_id,
        is_scope=True,
        scope_id=scope.scope_id,
        engine_version=scope.engine_version,
        member_run_ids=members,
    )


def get_result_provenance_bundle(
    db: Session,
    result: ResearchResultContext,
) -> dict:
    """Return provenance for the resolved run or complete research scope."""

    if result.is_scope:
        if result.scope_id is None:
            raise ValueError("Research scope not found")
        return get_scope_provenance_bundle(db, result.scope_id)
    return get_provenance_bundle(db, result.root_run_id)


def list_result_traces(
    db: Session,
    result: ResearchResultContext,
) -> list[ToolTrace]:
    """Return traces for the resolved run or complete research scope."""

    if result.is_scope:
        if result.scope_id is None:
            raise ValueError("Research scope not found")
        return list_scope_traces(db, result.scope_id)
    return list_tool_traces(db, result.root_run_id)


__all__ = [
    "ResearchResultContext",
    "get_result_provenance_bundle",
    "list_result_traces",
    "resolve_research_result",
]
