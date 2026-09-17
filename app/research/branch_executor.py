"""Deterministic serial execution for PEAR research branches.

The first PEAR execution phase intentionally has one owner and one in-flight
node at a time.  This keeps lineage, budgets, approval pauses, and terminal
state transitions auditable while the Quick path remains outside this module.
"""

from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.base import LLMClient
from app.research.contracts import NodeExecutionResult
from app.research.models import ResearchNode, ResearchScope
from app.research.node_executor import ResearchNodeExecutor


class SerialPearExecutor:
    """Execute PEAR nodes in deterministic depth/priority order.

    ``ResearchNodeExecutor`` remains the compatibility adapter for the legacy
    ReAct runtime.  This class owns the PEAR scheduling boundary: it never
    submits work to a pool and never starts a second node before the previous
    node has returned a terminal or waiting result.
    """

    def __init__(self, node_executor: ResearchNodeExecutor | None = None) -> None:
        self.node_executor = node_executor or ResearchNodeExecutor()

    @staticmethod
    def order(nodes: Iterable[ResearchNode]) -> list[ResearchNode]:
        """Return a stable execution order independent of database row order."""

        return sorted(
            nodes,
            key=lambda node: (
                int(node.depth or 0),
                int(node.priority or 0),
                str(node.node_id),
            ),
        )

    def execute_one(
        self,
        db: Session,
        scope: ResearchScope,
        node: ResearchNode,
        settings: Settings,
        llm_client: LLMClient | None = None,
    ) -> NodeExecutionResult:
        """Run exactly one node; callers may inspect the result before next."""

        return self.node_executor.execute(db, scope, node, settings, llm_client)

    def execute_serial(
        self,
        db: Session,
        scope: ResearchScope,
        nodes: Iterable[ResearchNode],
        settings: Settings,
        llm_client: LLMClient | None = None,
    ) -> list[NodeExecutionResult]:
        """Run nodes one-by-one and stop at a required non-completed node."""

        results: list[NodeExecutionResult] = []
        for node in self.order(nodes):
            result = self.execute_one(db, scope, node, settings, llm_client)
            results.append(result)
            try:
                metadata = json.loads(node.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            required = bool(metadata.get("required", True))
            if required and result.get("status") in {
                "waiting_human",
                "waiting_human_plan",
                "failed",
                "cancelled",
            }:
                break
        return results
