"""Persisted, run-local tool recovery. Never grants new permissions."""
from __future__ import annotations

import hashlib
import json
import time

from app.tools.base import ToolResult
from app.tools.errors import classify_tool_error


def input_key(arguments: dict) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()[:24]


def unavailable_reason(state: dict, name: str, limit: int, arguments: dict | None = None) -> str | None:
    item = state.get("tool_recovery", {}).get(name, {})
    if item.get("status") == "disabled":
        return str(item.get("reason", "tool_disabled"))
    if int(state.get("tool_call_counts", {}).get(name, 0)) >= limit:
        return "tool_call_limit"
    if float(item.get("retry_at", 0)) > time.time():
        return "cooldown"
    if arguments is not None and input_key(arguments) in item.get("blocked_inputs", {}):
        return str(item["blocked_inputs"][input_key(arguments)])
    return None


def observe_result(state: dict, name: str, arguments: dict, result: ToolResult, limit: int) -> dict:
    item = state.setdefault("tool_recovery", {}).setdefault(name, {})
    counts = state.setdefault("tool_call_counts", {})
    attempts = 0 if result.metadata.get("executed") is False else 1 + max(0, int(result.metadata.get("retry_count") or 0))
    counts[name] = int(counts.get(name, 0)) + attempts
    item["attempts"] = counts[name]
    empty_discovery = (result.success and isinstance(result.output, dict)
                       and any(key in result.output and result.output[key] == [] for key in ("results", "papers")))
    if result.success and not empty_discovery:
        item.update(status="available", reason=None, retry_at=0)
    else:
        category = "empty_result" if empty_discovery else classify_tool_error(
            result.metadata.get("original_error_type") or result.metadata.get("error_type"), result.error_message).value
        item["last_error_category"] = category
        item["failures"] = int(item.get("failures", 0)) + 1
        # A page failure must not disable the entire web/PDF reader.
        page_scoped = name in {"web_fetcher", "pdf_reader"} or (
            result.metadata.get("http_status") == 403 and category != "rate_limited")
        if category in {"auth_error", "unavailable"} and not page_scoped:
            item.update(status="disabled", reason=category, retry_at=0)
        elif category in {"policy_error", "not_found", "invalid_request", "auth_error"}:
            item.setdefault("blocked_inputs", {})[input_key(arguments)] = category
            item.update(status="available", reason="input_blocked", retry_at=0)
        elif category in {"timeout", "rate_limited", "provider_error"} and not page_scoped:
            try:
                delay = float(result.metadata.get("retry_after_seconds") or min(2 ** (item["failures"] - 1), 8))
            except (TypeError, ValueError):
                delay = 1
            item.update(status="cooldown", reason=category, retry_at=time.time() + max(1, min(delay, 3600)))
        else:
            item.setdefault("blocked_inputs", {})[input_key(arguments)] = category
            item.update(status="available", reason="input_blocked", retry_at=0)
    if counts[name] >= limit and item.get("status") != "disabled":
        item.update(status="exhausted", reason="tool_call_limit")
    return dict(item)


def recovery_context(state: dict, allowed: list[str], limit: int, source_mode: str) -> dict:
    return {"source_mode": source_mode, "mock_allowed": source_mode in {"mock", "offline"},
            "tool_status": {name: {"unavailable_reason": unavailable_reason(state, name, limit),
                "remaining_attempts": max(0, limit - int(state.get("tool_call_counts", {}).get(name, 0)))}
                for name in allowed},
            "instruction": "Choose another permitted tool when one fails or is unavailable. Never replace real evidence with mock data."}
