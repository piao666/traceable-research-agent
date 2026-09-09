"""Explicit, read-only verification of the real research runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.llm.base import LLMClient, LLMMessage
from app.llm.providers import create_llm_client
from app.runtime.capabilities import local_capability_items
from app.tools.base import ToolResult
from app.tools.tavily_search import tavily_search
from app.tools.web_fetcher import web_fetch


def _replace(items: list[dict[str, Any]], name: str, **updates: Any) -> None:
    for item in items:
        if item.get("name") == name:
            item.update(updates, checked_at=datetime.now(timezone.utc).isoformat())
            return


def _safe_llm_probe(client: LLMClient) -> dict[str, Any]:
    try:
        available = client.is_available()
    except Exception:
        return {
            "success": False,
            "error_type": "provider_unavailable",
            "detail": "模型可用性检查失败",
            "usage_parsed": False,
            "structured_output": False,
        }
    if not available:
        return {
            "success": False,
            "error_type": "missing_configuration",
            "detail": "模型配置不完整",
            "usage_parsed": False,
            "structured_output": False,
        }
    try:
        response = client.complete(
            [
                LLMMessage(role="system", content="Return only a JSON object."),
                LLMMessage(role="user", content='Return exactly {"ok":true}.'),
            ],
            temperature=0.0,
            max_tokens=16,
        )
    except Exception:
        return {
            "success": False,
            "error_type": "provider_unavailable",
            "detail": "模型最小 JSON 响应验证失败",
            "usage_parsed": False,
            "structured_output": False,
        }
    structured = False
    if response.success:
        try:
            structured = json.loads(str(response.content or "")) == {"ok": True}
        except (TypeError, ValueError):
            structured = False
    error_type = None if response.success and structured else (
        "structured_output_invalid" if response.success else response.metadata.get("error_type") or "provider_unavailable"
    )
    return {
        "success": bool(response.success and structured),
        "error_type": error_type,
        "detail": "模型最小 JSON 响应验证通过" if response.success and structured else "模型最小 JSON 响应验证失败",
        "usage_parsed": response.usage is not None,
        "structured_output": structured,
    }


def run_runtime_preflight(
    settings: Settings,
    *,
    llm_client: LLMClient | None = None,
    searcher: Callable[..., ToolResult] = tavily_search,
    fetcher: Callable[..., ToolResult] = web_fetch,
) -> dict[str, Any]:
    """Probe real LLM/search/fetch only after an explicit API request."""

    checked_at = datetime.now(timezone.utc).isoformat()
    items = local_capability_items(settings)
    warnings: list[str] = []
    blockers: list[dict[str, str]] = []
    if settings.research_profile == "offline" or settings.offline_mode:
        warnings.append("当前为离线 Profile；未调用真实模型或外部搜索。")
        return {
            "checked_at": checked_at,
            "profile": settings.research_profile,
            "ready": True,
            "verified": False,
            "capabilities": items,
            "blockers": blockers,
            "warnings": warnings,
        }

    llm_result = _safe_llm_probe(llm_client or create_llm_client(settings))
    _replace(
        items,
        "llm",
        reachable=llm_result["success"],
        usable=llm_result["success"],
        detail=llm_result["detail"],
        error_type=llm_result["error_type"],
        checks={"usage_parsed": llm_result["usage_parsed"], "structured_output": llm_result["structured_output"]},
    )
    if not llm_result["success"]:
        blockers.append({"capability": "llm", "error_type": str(llm_result["error_type"]), "message": str(llm_result["detail"])})
    elif not llm_result["usage_parsed"]:
        warnings.append("模型响应可用，但未返回可解析的 usage；Token 预算将采用本地保守估算。")

    try:
        search_result = searcher(
            {"query": "OpenAI official documentation", "max_results": 1, "search_depth": "basic"},
            settings_obj=settings,
        )
    except Exception:
        search_result = ToolResult(
            success=False,
            error_message="真实搜索验证失败。",
            metadata={"error_type": "provider_unavailable"},
        )
    search_rows = list((search_result.output or {}).get("results") or []) if isinstance(search_result.output, dict) else []
    real_urls = [
        str(row.get("url")) for row in search_rows
        if isinstance(row, dict) and urlsplit(str(row.get("url") or "")).scheme in {"http", "https"}
    ]
    real_search = bool(
        search_result.success
        and real_urls
        and search_result.metadata.get("data_source") == "tavily_api"
        and not search_result.metadata.get("fallback_used")
    )
    search_error = None if real_search else search_result.metadata.get("error_type") or "malformed_response"
    _replace(
        items,
        settings.search_provider,
        reachable=bool(search_result.success),
        usable=real_search,
        detail="真实搜索返回了可抓取 URL" if real_search else "真实搜索验证失败或返回了模拟结果",
        error_type=search_error,
    )
    if not real_search:
        blockers.append({"capability": "search", "error_type": str(search_error), "message": "真实搜索未返回可用 URL。"})

    if real_search and real_urls:
        try:
            fetch_result = fetcher(
                {"urls": real_urls[:3], "max_chars": 1200, "timeout_seconds": 10, "batch_timeout_seconds": 25},
                settings_obj=settings,
            )
        except Exception:
            fetch_result = ToolResult(
                success=False,
                error_message="网页抓取验证失败。",
                metadata={"error_type": "provider_unavailable"},
            )
        fetch_output = fetch_result.output if isinstance(fetch_result.output, dict) else {}
        fetched = bool(fetch_result.success and fetch_output.get("fetched_count"))
        fetch_error = None if fetched else fetch_result.metadata.get("error_type") or "malformed_response"
        _replace(
            items,
            "web_fetcher",
            reachable=fetched,
            usable=fetched,
            detail="静态 HTTP 与正文抽取验证通过" if fetched else "静态 HTTP 或正文抽取验证失败",
            error_type=fetch_error,
        )
        if not fetched:
            blockers.append({"capability": "web_fetcher", "error_type": str(fetch_error), "message": "网页抓取验证失败。"})
    else:
        _replace(items, "web_fetcher", reachable=False, usable=False, detail="搜索未返回 URL，未执行网页抓取", error_type="dependency_unavailable")
        blockers.append({"capability": "web_fetcher", "error_type": "dependency_unavailable", "message": "搜索不可用，无法验证网页抓取。"})

    optional = [item["name"] for item in items if item["category"] in {"academic", "mcp"} and not item["usable"]]
    if optional:
        warnings.append("可选能力未就绪：" + "、".join(optional))
    return {
        "checked_at": checked_at,
        "profile": settings.research_profile,
        "ready": not blockers,
        "verified": True,
        "capabilities": items,
        "blockers": blockers,
        "warnings": warnings,
    }
