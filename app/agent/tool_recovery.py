"""Persisted, run-local tool recovery. Never grants new permissions."""
from __future__ import annotations

import hashlib
import json
import time

from app.tools.base import ToolResult
from app.tools.errors import classify_tool_error


def _normalized_int(value, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _semantic_arguments(name: str, arguments: dict, state: dict | None = None) -> dict:
    """Normalize equivalent requests before persisting a blocked-input key."""

    prepared = dict(arguments or {})
    if name != "web_fetcher":
        return prepared
    sources = (state or {}).get("source_context", {}).get("sources", [])
    source_id = str(prepared.get("source_id") or "")
    source = next((row for row in sources if row.get("source_id") == source_id), None)
    if source_id:
        # Pending IDs and their recorded URLs are the same fetch intent. Fetched
        # IDs remain snapshot reads because offset is meaningful in that mode.
        if source and source.get("fetch_status") != "fetched":
            prepared.pop("source_id", None)
            prepared.pop("offset", None)
            prepared["urls"] = [source.get("url")]
        else:
            prepared = {
                "source_id": source_id,
                "offset": _normalized_int(prepared.get("offset"), 0),
                "max_chars": _normalized_int(prepared.get("max_chars"), 8000),
            }
            return prepared
    urls = prepared.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    from app.agent.source_context import source_url
    normalized_urls = sorted({source_url(url) or str(url) for url in urls})
    return {
        "urls": normalized_urls,
        "max_chars": _normalized_int(prepared.get("max_chars"), 8000),
        "timeout_seconds": _normalized_int(prepared.get("timeout_seconds"), 10),
        "batch_timeout_seconds": _normalized_int(prepared.get("batch_timeout_seconds"), 25),
    }


def input_key(arguments: dict, name: str = "", state: dict | None = None) -> str:
    normalized = _semantic_arguments(name, arguments, state) if name else dict(arguments or {})
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _complete_fetch_urls(state: dict, arguments: dict) -> set[str]:
    from app.agent.source_context import source_url
    try:
        requested_length = min(50000, int(arguments.get("max_chars", 8000)))
    except (TypeError, ValueError):
        requested_length = 8000
    return {url for row in state.get("source_context", {}).get("sources", [])
            if row.get("fetch_status") == "fetched"
            and not (row.get("content_basis") == "partial"
                     and requested_length > int(row.get("content_length") or 0))
            if (url := source_url(row.get("url")))}


def prune_completed_fetch_urls(state: dict, arguments: dict) -> tuple[dict, list[str]]:
    """Remove completed URLs from a mixed fetch without widening its request."""
    from app.agent.source_context import source_url
    prepared = dict(arguments)
    if prepared.get("source_id") or not prepared.get("urls"):
        return prepared, []
    urls = prepared["urls"] if isinstance(prepared["urls"], list) else [prepared["urls"]]
    completed = _complete_fetch_urls(state, prepared)
    kept: list = []
    skipped: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        canonical = source_url(raw)
        # Unsafe or malformed values still reach the normal SSRF/input guard.
        identity = canonical or f"raw:{raw}"
        if canonical in completed or identity in seen:
            if canonical:
                skipped.append(canonical)
            continue
        seen.add(identity)
        kept.append(raw)
    # Keep an all-completed request intact so unavailable_reason can reject it
    # as non-executed. An empty list would otherwise reach the reader and spend
    # a tool attempt on an artificial "missing URLs" failure.
    if kept:
        prepared["urls"] = kept
    return prepared, skipped


def unavailable_reason(state: dict, name: str, limit: int, arguments: dict | None = None) -> str | None:
    item = state.get("tool_recovery", {}).get(name, {})
    if item.get("status") == "disabled":
        return str(item.get("reason", "tool_disabled"))
    if int(state.get("tool_call_counts", {}).get(name, 0)) >= limit:
        return "tool_call_limit"
    if float(item.get("retry_at", 0)) > time.time():
        return "cooldown"
    if arguments is not None and input_key(arguments, name, state) in item.get("blocked_inputs", {}):
        return str(item["blocked_inputs"][input_key(arguments, name, state)])
    if arguments is not None:
        from app.agent.source_context import source_url
        sources = state.get("source_context", {}).get("sources", [])
        if name == "web_fetcher" and not arguments.get("source_id"):
            urls = arguments.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            fetched = _complete_fetch_urls(state, arguments)
            if urls and all(source_url(url) in fetched for url in urls):
                return "already_fetched_use_source_id"
        if name == "file_reader" and any(
                row["source_id"] in str(arguments.get("path") or "") for row in sources):
            return "source_id_is_not_a_file_use_web_fetcher"
        if name == "file_reader" and str(arguments.get("path") or "").lower().endswith((".html", ".htm")):
            return "unsupported_file_extension_use_web_fetcher"
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
            item.setdefault("blocked_inputs", {})[input_key(arguments, name, state)] = category
            item.update(status="available", reason="input_blocked", retry_at=0)
        elif category in {"timeout", "rate_limited", "provider_error"} and not page_scoped:
            try:
                delay = float(result.metadata.get("retry_after_seconds") or min(2 ** (item["failures"] - 1), 8))
            except (TypeError, ValueError):
                delay = 1
            item.update(status="cooldown", reason=category, retry_at=time.time() + max(1, min(delay, 3600)))
        else:
            item.setdefault("blocked_inputs", {})[input_key(arguments, name, state)] = category
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
