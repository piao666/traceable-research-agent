"""Execute one Research Tree node using the existing governed runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.agent.budget import BudgetExceeded, ensure_budget
from app.agent.execution_policy import allowed_tool_names, bind_run_policy
from app.agent.outcome import load_observations
from app.agent.react_executor import run_react_task
from app.config import Settings
from app.evidence.service import materialize_execution_provenance
from app.llm.base import LLMClient
from app.research.models import ResearchNode, ResearchScope
from app.trace import store
from app.trace.logger import record_trace_event


NodeRunner = Callable[[Session, str, Settings, LLMClient | None], dict[str, Any]]


class ResearchNodeExecutor:
    """Create a child AgentRun with explicit lineage and reuse ReAct unchanged."""

    def __init__(self, runner: NodeRunner | None = None) -> None:
        self.runner = runner or run_react_task

    def execute(
        self,
        db: Session,
        scope: ResearchScope,
        node: ResearchNode,
        settings: Settings,
        llm_client: LLMClient | None = None,
    ) -> dict[str, Any]:
        root = store.get_agent_run(db, scope.root_run_id)
        if root is None:
            raise ValueError("Research root run not found")
        parent_run_id = _parent_run_id(db, node) or scope.root_run_id
        parent = store.get_agent_run(db, parent_run_id) or root
        parent_plan = bind_run_policy(parent, _json_object(parent.plan_json))
        inherited_tools = allowed_tool_names(parent_plan)
        child = store.create_agent_run(
            db,
            task=node.query,
            report_type=root.report_type,
            source_mode=root.source_mode,
            allowed_tools=inherited_tools,
            session_id=None,
            run_config_snapshot=root.run_config_snapshot,
            parent_run_id=parent_run_id,
            root_run_id=scope.root_run_id,
            run_role=_run_role(node.node_type),
            research_scope_id=scope.scope_id,
            engine_version=scope.engine_version,
        )
        node.run_id = child.run_id
        node.status = "running"
        node.updated_at = datetime.now(timezone.utc)
        db.commit()
        child_plan = {
            "version": "research-node-v2",
            "task": node.query,
            "execution_mode": "react",
            "requested_execution_mode": "react",
            "source_mode": root.source_mode,
            "allowed_tools": inherited_tools,
            "task_contract": parent_plan.get("task_contract"),
            "research_scope_id": scope.scope_id,
            "research_node_id": node.node_id,
            "parent_run_id": parent_run_id,
            "root_run_id": scope.root_run_id,
            "run_role": child.run_role,
            "engine_version": scope.engine_version,
            "defer_to_research_scope": True,
            "steps": [],
            "notes": ["Executed by Deep Research Engine V2 ResearchNodeExecutor."],
        }
        ensure_budget(db, child.run_id, settings, parent_run_id=parent_run_id)
        store.update_agent_run_plan(db, child.run_id, child_plan)
        record_trace_event(
            db,
            scope.root_run_id,
            0,
            "research_node_dispatch",
            "success",
            {"node_id": node.node_id},
            "Research branch dispatched with explicit lineage.",
            {"node_id": node.node_id, "child_run_id": child.run_id, "parent_run_id": parent_run_id},
        )
        try:
            result = self.runner(db, child.run_id, settings, llm_client)
        except BudgetExceeded:
            node.status = "failed"
            node.updated_at = datetime.now(timezone.utc)
            db.commit()
            raise
        except Exception as exc:
            db.rollback()
            record_trace_event(
                db,
                child.run_id,
                0,
                "research_node_execution",
                "failed",
                {"node_id": node.node_id},
                "Research branch execution failed.",
                {"error_type": type(exc).__name__},
                error_message="Research branch execution failed; inspect Trace.",
            )
            store.update_agent_run_status(
                db, child.run_id, "failed", "Research branch execution failed; inspect Trace."
            )
            result = {"run_id": child.run_id, "status": "failed"}

        child = store.get_fresh_agent_run(db, child.run_id)
        if child and child.status == "completed":
            traces = store.list_tool_traces(db, child.run_id)
            if traces:
                materialize_execution_provenance(
                    db,
                    child,
                    _json_object(child.plan_json),
                    load_observations(traces),
                    traces,
                    settings,
                )
        node.status = child.status if child and child.status in {"completed", "failed", "cancelled"} else "failed"
        node.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {**result, "node_id": node.node_id, "run_id": child.run_id, "status": node.status}


def _parent_run_id(db: Session, node: ResearchNode) -> str | None:
    if not node.parent_node_id:
        return None
    parent = db.get(ResearchNode, node.parent_node_id)
    return parent.run_id if parent else None


def _run_role(node_type: str) -> str:
    return {
        "verification": "verification_branch",
        "contradiction_check": "verification_branch",
        "data_analysis": "analysis_branch",
    }.get(node_type, "research_branch")


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
