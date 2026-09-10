"""Read-only Research Tree projection."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.research.models import ResearchScope
from app.research.scope import list_scope_nodes, node_dict, scope_summary


def get_research_tree(db: Session, scope: ResearchScope) -> dict[str, Any]:
    nodes = list_scope_nodes(db, scope.scope_id)
    payloads = {node.node_id: {**node_dict(node), "children": []} for node in nodes}
    roots: list[dict[str, Any]] = []
    for node in nodes:
        payload = payloads[node.node_id]
        parent = payloads.get(node.parent_node_id or "")
        if parent is None:
            roots.append(payload)
        else:
            parent["children"].append(payload)
    return {"scope": scope_summary(db, scope), "roots": roots}
