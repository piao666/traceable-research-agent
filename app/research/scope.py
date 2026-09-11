"""DB-authoritative Research Scope creation and traversal."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.agent.budget import budget_snapshot
from app.research.models import ResearchNode, ResearchScope
from app.trace.models import AgentRun, ToolTrace


def create_research_scope(
    db: Session,
    root_run_id: str,
    research_contract: dict[str, Any] | None,
    *,
    engine_version: str = "v2",
) -> ResearchScope:
    """Create or return the sole scope belonging to a root run."""

    run = db.get(AgentRun, root_run_id)
    if run is None:
        raise ValueError("Root task run not found")
    existing = db.scalars(
        select(ResearchScope).where(ResearchScope.root_run_id == root_run_id)
    ).first()
    if existing is not None:
        return existing
    scope = ResearchScope(
        scope_id=f"scope_{uuid4().hex}",
        root_run_id=root_run_id,
        engine_version=engine_version,
        status="running",
        research_contract_json=json.dumps(
            research_contract or {}, ensure_ascii=False, sort_keys=True, default=str
        ),
    )
    db.add(scope)
    db.flush()
    run.parent_run_id = None
    run.root_run_id = root_run_id
    run.run_role = "root"
    run.research_scope_id = scope.scope_id
    run.engine_version = engine_version
    db.commit()
    db.refresh(scope)
    return scope


def resolve_research_scope(db: Session, run_id: str) -> ResearchScope | None:
    """Resolve a scope through explicit run lineage, never plan_json."""

    run = db.get(AgentRun, run_id)
    if run is None:
        return None
    if run.research_scope_id:
        return db.get(ResearchScope, run.research_scope_id)
    return db.scalars(
        select(ResearchScope).where(ResearchScope.root_run_id == (run.root_run_id or run.run_id))
    ).first()


def list_scope_runs(db: Session, scope_id: str) -> list[AgentRun]:
    """List all scope runs deterministically with the root first."""

    return list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.research_scope_id == scope_id)
            .order_by(
                case((AgentRun.run_role == "root", 0), else_=1),
                AgentRun.created_at.asc(),
                AgentRun.run_id.asc(),
            )
        ).all()
    )


def list_scope_nodes(db: Session, scope_id: str) -> list[ResearchNode]:
    return list(
        db.scalars(
            select(ResearchNode)
            .where(ResearchNode.scope_id == scope_id)
            .order_by(ResearchNode.depth.asc(), ResearchNode.priority.asc(), ResearchNode.node_id.asc())
        ).all()
    )


def list_scope_traces(db: Session, scope_id: str) -> list[ToolTrace]:
    run_ids = [run.run_id for run in list_scope_runs(db, scope_id)]
    if not run_ids:
        return []
    return list(
        db.scalars(
            select(ToolTrace)
            .where(ToolTrace.run_id.in_(run_ids))
            .order_by(ToolTrace.created_at.asc(), ToolTrace.trace_id.asc())
        ).all()
    )


def create_research_node(
    db: Session,
    scope_id: str,
    *,
    parent_node_id: str | None,
    run_id: str | None,
    node_type: str,
    topic: str,
    query: str,
    research_goal: str,
    depth: int,
    priority: int,
    status: str = "pending",
    metadata: dict[str, Any] | None = None,
) -> ResearchNode:
    normalized_query = normalize_research_query(query)
    siblings = db.scalars(
        select(ResearchNode).where(
            ResearchNode.scope_id == scope_id,
            ResearchNode.parent_node_id == parent_node_id,
        )
    ).all()
    for sibling in siblings:
        if normalize_research_query(sibling.query) == normalized_query:
            return sibling
    node_metadata = {"branch_planning_status": "pending", **(metadata or {})}
    node = ResearchNode(
        node_id=f"node_{uuid4().hex}",
        scope_id=scope_id,
        parent_node_id=parent_node_id,
        run_id=run_id,
        node_type=node_type,
        topic=topic,
        query=query,
        research_goal=research_goal,
        depth=max(0, int(depth)),
        priority=max(0, int(priority)),
        status=status,
        metadata_json=json.dumps(node_metadata, ensure_ascii=False, sort_keys=True, default=str),
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def normalize_research_query(query: str) -> str:
    """Normalize a query for sibling-node identity checks."""

    normalized = unicodedata.normalize("NFKC", str(query or "")).lower().strip()
    return re.sub(r"\s+", " ", normalized)


def update_node_metadata(
    db: Session,
    node: ResearchNode,
    **updates: Any,
) -> ResearchNode:
    metadata = _json_object(node.metadata_json)
    metadata.update(updates)
    node.metadata_json = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, default=str
    )
    node.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(node)
    return node


def update_scope_status(db: Session, scope_id: str, status: str) -> ResearchScope:
    scope = db.get(ResearchScope, scope_id)
    if scope is None:
        raise ValueError("Research scope not found")
    scope.status = status
    scope.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(scope)
    return scope


def scope_summary(db: Session, scope: ResearchScope) -> dict[str, Any]:
    runs = list_scope_runs(db, scope.scope_id)
    nodes = list_scope_nodes(db, scope.scope_id)
    status_counts: dict[str, int] = {}
    for node in nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1
    return {
        "scope_id": scope.scope_id,
        "root_run_id": scope.root_run_id,
        "engine_version": scope.engine_version,
        "status": scope.status,
        "research_contract": _json_object(scope.research_contract_json),
        "run_ids": [run.run_id for run in runs],
        "node_counts": status_counts,
        "budget": budget_snapshot(db, scope.root_run_id),
        "created_at": scope.created_at.isoformat(),
        "updated_at": scope.updated_at.isoformat(),
    }


def node_dict(node: ResearchNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "scope_id": node.scope_id,
        "parent_node_id": node.parent_node_id,
        "run_id": node.run_id,
        "node_type": node.node_type,
        "topic": node.topic,
        "query": node.query,
        "research_goal": node.research_goal,
        "depth": node.depth,
        "priority": node.priority,
        "status": node.status,
        "metadata": _json_object(node.metadata_json),
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
    }


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
