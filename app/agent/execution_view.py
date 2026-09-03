"""Read-only, bounded execution insights; never creates budgets or changes plans."""
from __future__ import annotations

import time

from app.agent.execution_policy import allowed_tool_names, bind_run_policy
from app.agent.source_context import build_source_context
from app.agent.tool_recovery import unavailable_reason


def execution_insights(run, plan: dict, traces: list) -> dict:
    policy = bind_run_policy(run, dict(plan))
    allowed = allowed_tool_names(policy)
    state = plan.get("react_state")
    state = state if isinstance(state, dict) else {}
    recovery = state.get("tool_recovery") or {}
    counts = state.get("tool_call_counts") or {}
    limit = state.get("same_tool_max_calls")
    limit = limit if isinstance(limit, int) and limit > 0 else None
    recorded = limit is not None
    tools = []
    # Only report persisted ReAct policy: never invent live provider readiness.
    for name in allowed:
        item = recovery.get(name) or {}
        attempts = int(counts.get(name, 0))
        reason = unavailable_reason(state, name, limit) if limit is not None else None
        status = ("disabled" if item.get("status") == "disabled" else
                  "exhausted" if reason == "tool_call_limit" else
                  "cooldown" if reason == "cooldown" else "available" if recorded else "unknown")
        tools.append({"name": name, "status": status, "reason": reason or item.get("reason"),
                      "attempts": attempts if recorded else None,
                      "remaining_attempts": max(0, limit - attempts) if limit is not None else None,
                      "blocked_input_count": len(item.get("blocked_inputs") or {}),
                      "retry_at": item.get("retry_at") if status == "cooldown" else None})
    return {"version": "execution-insights-v1", "sampled_at": time.time(),
            "source_mode": run.source_mode, "allowed_tools": allowed,
            "recovery_recorded": recorded, "tools": tools,
            "source_context": build_source_context(traces)}
