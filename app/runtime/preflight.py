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


def _llm_role_identity(settings: Settings, role: str) -> tuple[str, str | None]:
    provider = (
        settings.react_llm_provider or settings.llm_provider
        if role == "actor"
        else settings.llm_provider
    )
    configured = settings.get_llm_provider_config(provider)
    model = (
        settings.react_llm_model or configured.get("model")
        if role == "actor"
        else settings.llm_model or configured.get("model")
    )
    return provider, str(model) if model else None


def _llm_capability(
    template: dict[str, Any],
    name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        **template,
        "name": name,
        "reachable": result["success"],
        "usable": result["success"],
        "detail": result["detail"],
        "error_type": result["error_type"],
        "checks": {
            "usage_parsed": result["usage_parsed"],
            "structured_output": result["structured_output"],
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _successful_fetch_page(output: dict[str, Any]) -> dict[str, Any] | None:
    for page in output.get("pages") or []:
        if not isinstance(page, dict):
            continue
        status = str(page.get("fetch_status") or "")
        if status in {"success", "partial"} and str(page.get("content") or "").strip():
            return page
    return None


def _fetch_backend_check(page: dict[str, Any]) -> dict[str, Any]:
    raw_backend = str(page.get("fetch_backend") or "")
    fetch_backend = "http" if raw_backend == "cache" else raw_backend
    attempts = page.get("retrieval_attempts")
    attempted_backends = [
        str(attempt.get("backend"))
        for attempt in attempts or []
        if isinstance(attempt, dict) and attempt.get("backend")
    ]
    if not attempted_backends and fetch_backend:
        attempted_backends = [fetch_backend]
    fallback_used = fetch_backend in {"browser", "remote_extract"} or len(
        dict.fromkeys(attempted_backends)
    ) > 1
    details = {
        "http": "静态 HTTP 正文抽取验证通过",
        "browser": "网页抓取验证通过；静态 HTTP 不足，使用 Browser fallback",
        "remote_extract": "网页抓取验证通过；本地抓取不足，使用远端 Extract fallback",
        "pdf": "网页抓取验证通过",
    }
    return {
        "fetch_backend": fetch_backend,
        "provider": str(page.get("provider") or ""),
        "fallback_used": fallback_used,
        "attempted_backends": attempted_backends,
        "detail": details.get(fetch_backend, "网页抓取验证失败"),
    }


def run_runtime_preflight(
    settings: Settings,
    *,
    llm_client: LLMClient | None = None,
    actor_client: LLMClient | None = None,
    synthesizer_client: LLMClient | None = None,
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

    actor_identity = _llm_role_identity(settings, "actor")
    synthesizer_identity = _llm_role_identity(settings, "synthesizer")
    same_role_model = actor_identity == synthesizer_identity
    legacy_client = llm_client
    actor_probe_client = actor_client or legacy_client
    synthesizer_probe_client = synthesizer_client or legacy_client
    if same_role_model:
        shared_client = (
            actor_probe_client
            or synthesizer_probe_client
            or create_llm_client(settings, actor_identity[0], actor_identity[1])
        )
        actor_result = synthesizer_result = _safe_llm_probe(shared_client)
    else:
        actor_client_for_probe = actor_probe_client or create_llm_client(
            settings, actor_identity[0], actor_identity[1]
        )
        synthesizer_client_for_probe = synthesizer_probe_client or create_llm_client(
            settings, synthesizer_identity[0], synthesizer_identity[1]
        )
        actor_result = _safe_llm_probe(actor_client_for_probe)
        synthesizer_result = _safe_llm_probe(synthesizer_client_for_probe)

    llm_template = next(
        (item for item in items if item.get("name") == "llm"),
        {"category": "llm", "mode": "real"},
    )
    items = [item for item in items if item.get("name") != "llm"]
    if same_role_model:
        items.insert(0, _llm_capability(llm_template, "llm", actor_result))
    else:
        items[0:0] = [
            _llm_capability(llm_template, "llm_actor", actor_result),
            _llm_capability(
                llm_template,
                "llm_synthesizer",
                synthesizer_result,
            ),
        ]

    actor_required = settings.execution_mode == "react" or settings.deep_research_enabled
    synthesizer_required = settings.report_generation_mode == "llm"
    if actor_required and not actor_result["success"]:
        blockers.append(
            {
                "capability": "llm_actor",
                "error_type": str(actor_result["error_type"]),
                "message": str(actor_result["detail"]),
            }
        )
    if synthesizer_required and not synthesizer_result["success"]:
        blockers.append(
            {
                "capability": "report_synthesis",
                "error_type": str(synthesizer_result["error_type"]),
                "message": str(synthesizer_result["detail"]),
            }
        )
    for role_result in {id(actor_result): actor_result, id(synthesizer_result): synthesizer_result}.values():
        if role_result["success"] and not role_result["usage_parsed"]:
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
        successful_page = _successful_fetch_page(fetch_output)
        fetched = bool(fetch_result.success and fetch_output.get("fetched_count") and successful_page)
        fetch_error = None if fetched else fetch_result.metadata.get("error_type") or "malformed_response"
        backend_check = _fetch_backend_check(successful_page) if successful_page else {
            "fetch_backend": None,
            "provider": None,
            "fallback_used": False,
            "attempted_backends": [],
            "detail": "网页抓取验证失败",
        }
        _replace(
            items,
            "web_fetcher",
            reachable=fetched,
            usable=fetched,
            detail=backend_check["detail"] if fetched else "静态 HTTP 或正文抽取验证失败",
            error_type=fetch_error,
            fetch_backend=backend_check["fetch_backend"],
            provider=backend_check["provider"],
            fallback_used=backend_check["fallback_used"],
            attempted_backends=backend_check["attempted_backends"],
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
