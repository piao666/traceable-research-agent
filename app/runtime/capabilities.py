"""Non-secret local runtime capability projection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings


def local_capability_items(settings: Settings) -> list[dict[str, Any]]:
    checked_at = datetime.now(timezone.utc).isoformat()
    provider = settings.llm_provider
    provider_config = settings.get_llm_provider_config(provider)
    parsed_base = urlsplit(str(provider_config.get("base_url") or ""))
    valid_llm_base = parsed_base.scheme in {"http", "https"} and bool(parsed_base.netloc)
    llm_configured = (
        provider == "deterministic"
        or bool(settings.get_llm_api_key(provider) and valid_llm_base and provider_config.get("model"))
    )
    search_configured = settings.offline_mode or bool(
        settings.search_provider == "tavily"
        and settings.tavily_search_enabled
        and settings.tavily_api_key
    )
    return [
        {
            "name": "llm",
            "category": "llm",
            "configured": llm_configured,
            "reachable": None,
            "usable": llm_configured,
            "mode": "offline" if provider == "deterministic" else "real",
            "detail": "模型配置完整" if llm_configured else "模型地址、名称或密钥未配置完整，且地址必须为 HTTP(S)",
            "error_type": None if llm_configured else "missing_configuration",
            "checked_at": checked_at,
        },
        {
            "name": settings.search_provider,
            "category": "search",
            "configured": search_configured,
            "reachable": None,
            "usable": search_configured,
            "mode": "offline" if settings.offline_mode else "real",
            "detail": "外部搜索配置完整" if search_configured else "外部搜索密钥未配置",
            "error_type": None if search_configured else "missing_configuration",
            "checked_at": checked_at,
        },
        {
            "name": "web_fetcher",
            "category": "fetch",
            "configured": True,
            "reachable": None,
            "usable": True,
            "mode": "local_http",
            "detail": "静态网页正文抽取可用；未执行外部连通性验证",
            "error_type": None,
            "fetch_backend": None,
            "provider": None,
            "fallback_used": False,
            "attempted_backends": [],
            "checked_at": checked_at,
        },
        {
            "name": "pdf_reader",
            "category": "pdf",
            "configured": settings.pdf_reader_enabled,
            "reachable": None,
            "usable": settings.pdf_reader_enabled,
            "mode": "local",
            "detail": "PDF 原生文本读取可用" if settings.pdf_reader_enabled else "PDF 读取已禁用",
            "error_type": None if settings.pdf_reader_enabled else "capability_disabled",
            "checked_at": checked_at,
        },
        {
            "name": "academic_search",
            "category": "academic",
            "configured": any((settings.openalex_search_enabled, settings.crossref_search_enabled)),
            "reachable": None,
            "usable": any((settings.openalex_search_enabled, settings.crossref_search_enabled)),
            "mode": "real" if not settings.offline_mode else "offline",
            "detail": "公共学术检索已启用；连通性按任务验证",
            "error_type": None,
            "checked_at": checked_at,
        },
        {
            "name": "remote_mcp",
            "category": "mcp",
            "configured": settings.mcp_remote_registry_enabled,
            "reachable": None,
            "usable": settings.mcp_remote_registry_enabled,
            "mode": "optional",
            "detail": "远程 MCP 为可选能力",
            "error_type": None if settings.mcp_remote_registry_enabled else "not_configured",
            "checked_at": checked_at,
        },
    ]


def required_runtime_ready(settings: Settings, items: list[dict[str, Any]] | None = None) -> bool:
    if settings.research_profile == "offline":
        return settings.offline_mode
    rows = items or local_capability_items(settings)
    indexed = {str(row.get("name")): row for row in rows}
    required = {"llm", settings.search_provider, "web_fetcher", "pdf_reader"}
    if not required <= set(indexed):
        return False
    return bool(
        indexed["llm"].get("usable")
        and indexed["llm"].get("mode") == "real"
        and indexed[settings.search_provider].get("usable")
        and indexed[settings.search_provider].get("mode") == "real"
        and indexed["web_fetcher"].get("usable")
        and indexed["pdf_reader"].get("usable")
    )
