"""Execute one Research Tree node using the existing governed runtime."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.agent.budget import BudgetExceeded, BudgetRuntime, ensure_budget
from app.agent.execution_policy import allowed_tool_names, bind_run_policy
from app.agent.outcome import load_observations
from app.agent.react_executor import run_react_task
from app.config import Settings
from app.evidence.service import materialize_execution_provenance
from app.llm.base import LLMClient
from app.research.models import ResearchNode, ResearchScope
from app.research.contracts import NodeExecutionResult, requirement_index
from app.trace import store
from app.trace.logger import record_phase_event, record_trace_event


NodeRunner = Callable[[Session, str, Settings, LLMClient | None], dict[str, Any]]


def run_governed_react_node(
    db: Session,
    run_id: str,
    settings: Settings,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the shared governed tool loop behind the PEAR node API."""

    result = run_react_task(db, run_id, settings, llm_client)
    return {
        **(result if isinstance(result, dict) else {}),
        "run_id": run_id,
        "status": str((result or {}).get("status") or "failed"),
    }


class ResearchNodeExecutor:
    """Create a child AgentRun with explicit lineage and reuse ReAct unchanged."""

    def __init__(self, runner: NodeRunner | None = None) -> None:
        self.runner = runner or run_governed_react_node

    def execute(
        self,
        db: Session,
        scope: ResearchScope,
        node: ResearchNode,
        settings: Settings,
        llm_client: LLMClient | None = None,
    ) -> NodeExecutionResult:
        root = store.get_agent_run(db, scope.root_run_id)
        if root is None:
            raise ValueError("Research root run not found")
        child = store.get_agent_run(db, node.run_id) if node.run_id else None
        if child is not None and child.status in {
            "completed",
            "incomplete",
            "waiting_human",
            "waiting_human_plan",
            "failed",
            "cancelled",
        }:
            node.status = child.status
            node.updated_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "node_id": node.node_id,
                "run_id": child.run_id,
                "status": child.status,
            }
        parent_run_id = _parent_run_id(db, node) or scope.root_run_id
        parent = store.get_agent_run(db, parent_run_id) or root
        parent_plan = bind_run_policy(parent, _json_object(parent.plan_json))
        inherited_tools = allowed_tool_names(parent_plan)
        if child is None:
            child_contract = _node_task_contract(parent_plan.get("task_contract"), node)
            # Check admission before allocating a child Run. Creating empty
            # branches after exhaustion breaks recovery and pollutes lineage.
            runtime = BudgetRuntime(db, scope.root_run_id, settings)
            runtime.reserve()
            if runtime.snapshot()["tool_calls"] >= runtime.limits["max_tool_calls"]:
                runtime.stop("tool_calls")
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
                "research_mode": parent_plan.get("research_mode") or "deep",
                "research_controller": parent_plan.get("research_controller") or "pear",
                "allowed_tools": inherited_tools,
                "retrieval_profile": parent_plan.get("retrieval_profile"),
                "intake_profile_name": parent_plan.get("intake_profile_name"),
                "intake_profile_snapshot": parent_plan.get("intake_profile_snapshot"),
                "research_profile": parent_plan.get("research_profile"),
                "source_constraints": parent_plan.get("source_constraints"),
                "evidence_policy_version": parent_plan.get("evidence_policy_version"),
                "task_contract": child_contract,
                "research_goal": node.research_goal,
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
                {
                    "node_id": node.node_id,
                    "child_run_id": child.run_id,
                    "parent_run_id": parent_run_id,
                },
            )
        else:
            node.status = "running"
            node.updated_at = datetime.now(timezone.utc)
            db.commit()
        execution_trace = record_phase_event(
            db,
            child.run_id,
            "node_execution",
            "started",
            details={"node_id": node.node_id, "node_type": node.node_type},
        )
        try:
            result = self.runner(db, child.run_id, settings, llm_client)
        except BudgetExceeded:
            stopped_child = store.update_agent_run_status(
                db, child.run_id, "failed", "Research node exceeded its budget."
            )
            node.status = stopped_child.status
            node.updated_at = datetime.now(timezone.utc)
            db.commit()
            record_phase_event(
                db, child.run_id, "node_execution", "failed",
                parent_trace_id=execution_trace.trace_id,
                details={"node_id": node.node_id, "error_type": "BudgetExceeded"},
                error_message="Research node exceeded its budget.",
            )
            raise
        except Exception as exc:
            db.rollback()
            error_details = {"error_type": type(exc).__name__}
            if isinstance(exc, OperationalError):
                # Do not persist SQL, parameters, or provider secrets from the
                # exception string. SQLite's symbolic code is enough to tell
                # a transient busy/locked database from a schema failure.
                sqlite_name = getattr(getattr(exc, "orig", None), "sqlite_errorname", None)
                if sqlite_name:
                    error_details["sqlite_errorname"] = sqlite_name
            record_trace_event(
                db,
                child.run_id,
                0,
                "research_node_execution",
                "failed",
                {"node_id": node.node_id},
                "Research branch execution failed.",
                error_details,
                error_message="Research branch execution failed; inspect Trace.",
                phase="node_execution",
                parent_trace_id=execution_trace.trace_id,
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
        # Preserve a real pause or in-flight state.  A runner may have reached
        # an approval boundary without being a failed tool invocation.
        node.status = (
            child.status
            if child and child.status in {
                "completed",
                "failed",
                "cancelled",
                "waiting_human",
                "waiting_human_plan",
                "running",
                "pending",
            }
            else "failed"
        )
        node.updated_at = datetime.now(timezone.utc)
        db.commit()
        record_phase_event(
            db,
            child.run_id,
            "node_execution",
            "success" if node.status == "completed" else "waiting" if node.status in {"waiting_human", "waiting_human_plan"} else "failed",
            parent_trace_id=execution_trace.trace_id,
            details={"node_id": node.node_id, "status": node.status},
            error_message=(
                "Research node is waiting for confirmation."
                if node.status in {"waiting_human", "waiting_human_plan"}
                else "Research node did not complete."
                if node.status != "completed"
                else None
            ),
        )
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


def _node_task_contract(root_contract: Any, node: ResearchNode) -> dict[str, Any]:
    """Project only assigned work; the Scope retains the unchanged root contract."""
    from copy import deepcopy

    contract = deepcopy(root_contract) if isinstance(root_contract, dict) else {}
    contract["original_task"] = node.research_goal or node.query
    metadata = _json_object(getattr(node, "metadata_json", None))
    assigned = metadata.get("assigned_requirement_ids")
    indexed = requirement_index(contract)
    if "assigned_requirement_ids" in metadata:
        if not isinstance(assigned, list) or any(not isinstance(value, str) for value in assigned):
            raise ValueError("Assigned requirement IDs must be a list of strings")
        if set(assigned) - indexed.keys():
            raise ValueError("Unknown assigned requirement ID")
    objective = f"{node.query} {node.research_goal}".casefold()

    def mentioned(value: Any) -> bool:
        term = str(value or "").strip().casefold()
        if not term:
            return True
        return bool(re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if term.isascii() else re.escape(term), objective,
        ))

    selected_ids: set[str] = set()
    for key in ("requirements", "evidence_scope_requirements"):
        requirements = contract.get(key)
        if not isinstance(requirements, list):
            continue
        records = [item for item in requirements if isinstance(item, dict)]
        if isinstance(assigned, list):
            wanted = {str(value) for value in assigned}
            matched = [item for item in records if str(item.get("requirement_id")) in wanted]
        else:
            # Persisted nodes without IDs can be resumed conservatively.
            # Entity is mandatory when present; dimension narrows a single
            # entity's work only when the objective names a known dimension.
            entity_matches = [item for item in records if item.get("entity") and mentioned(item["entity"])]
            dimensions_named = any(item.get("dimension") and mentioned(item["dimension"]) for item in entity_matches)
            matched = [
                item for item in entity_matches
                if not dimensions_named or mentioned(item.get("dimension"))
            ]
        if matched:
            contract[key] = matched
            selected_ids.update(str(item.get("requirement_id")) for item in matched)
        else:
            contract.pop(key, None)
    contract["assigned_requirement_ids"] = sorted(selected_ids)
    if isinstance(contract.get("questions"), list):
        contract["questions"] = [
            {**question, "requirement_ids": [
                value for value in question.get("requirement_ids", []) if value in selected_ids
            ]}
            for question in contract["questions"]
            if isinstance(question, dict)
            and any(value in selected_ids for value in question.get("requirement_ids", []))
        ]
    for field in ("entities", "dimensions"):
        singular = "entity" if field == "entities" else "dimension"
        contract[field] = list(dict.fromkeys(
            str(item[singular])
            for key in ("requirements", "evidence_scope_requirements")
            for item in contract.get(key, [])
            if isinstance(item, dict) and item.get(singular)
        ))
    return contract
