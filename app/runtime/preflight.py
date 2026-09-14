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


def _check_llm_basic(client: LLMClient) -> dict[str, Any]:
    """Probe raw JSON output: can the LLM return a parseable object at all?"""
    try:
        available = client.is_available()
    except Exception:
        return _llm_failure("provider_unavailable", "模型可用性检查失败")

    if not available:
        return _llm_failure("missing_configuration", "模型配置不完整")

    try:
        response = client.structured_complete(
            [
                LLMMessage(role="system", content="Return only a JSON object."),
                LLMMessage(role="user", content='Return exactly {"ok":true}.'),
            ],
            temperature=0.0,
            max_tokens=128,
        )
    except Exception:
        return _llm_failure("provider_unavailable", "模型最小 JSON 响应验证失败")

    if not response.success:
        return _llm_failure(
            response.metadata.get("error_type") or "provider_unavailable",
            "模型最小 JSON 响应验证失败",
            response.usage is not None,
        )

    structured = False
    try:
        structured = json.loads(str(response.content or "")) == {"ok": True}
    except (TypeError, ValueError):
        pass

    if structured:
        return {
            "success": True,
            "error_type": None,
            "detail": "模型最小 JSON 响应验证通过",
            "usage_parsed": response.usage is not None,
            "structured_output": True,
        }
    return _llm_failure(
        "structured_output_invalid",
        "模型最小 JSON 响应验证失败",
        response.usage is not None,
    )


def _check_planner_capability(
    client: LLMClient,
    task: str = "List three open-source web frameworks.",
    allowed_tools: str = "tavily_search, web_fetcher",
    source_mode: str = "real",
) -> dict[str, Any]:
    """Probe whether the LLM returns a valid research plan with steps."""
    try:
        available = client.is_available()
    except Exception:
        return _llm_failure("provider_unavailable", "Planner probe: 模型不可用")

    if not available:
        return _llm_failure("missing_configuration", "Planner probe: 模型配置不完整")

    system = (
        "Return a valid JSON research plan. Required fields: version, task, source_mode, "
        "allowed_tools, steps (array of {step_no, goal, tool_name, arguments}). "
        "Use only these tools: " + allowed_tools + ". "
        "Output only JSON, no Markdown."
    )
    user_payload = {
        "task": task,
        "source_mode": source_mode,
        "allowed_tools": [t.strip() for t in allowed_tools.split(",")],
        "required_top_level_fields": ["version", "task", "source_mode", "allowed_tools", "steps"],
    }
    try:
        response = client.structured_complete(
            [
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
            ],
            temperature=0.0,
            max_tokens=2000,
        )
    except Exception:
        return _llm_failure("provider_unavailable", "Planner probe: 请求失败")

    if not response.success:
        return _llm_failure(
            response.metadata.get("error_type") or "provider_unavailable",
            "Planner probe: 模型响应失败",
            response.usage is not None,
        )

    parsed = _extract_json(str(response.content or ""))
    if parsed is None:
        return _llm_failure(
            "structured_output_invalid",
            "Planner probe: 响应不是有效 JSON 对象",
            response.usage is not None,
        )

    steps = parsed.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        return _llm_failure(
            "structured_output_invalid",
            "Planner probe: 响应缺少 steps 数组或为空",
            response.usage is not None,
        )

    valid_steps = [
        step for step in steps
        if isinstance(step, dict) and str(step.get("tool_name") or "").strip()
    ]
    if not valid_steps:
        return _llm_failure(
            "structured_output_invalid",
            "Planner probe: steps 中没有可识别的 tool_name",
            response.usage is not None,
        )

    return {
        "success": True,
        "error_type": None,
        "detail": f"Planner probe: 返回了 {len(valid_steps)} 个有效步骤",
        "usage_parsed": response.usage is not None,
        "structured_output": True,
    }


def _check_react_capability(
    client: LLMClient,
    task: str = "Search for Python async best practices and summarize.",
    allowed_tools: str = "tavily_search, web_fetcher",
) -> dict[str, Any]:
    """Probe whether the LLM returns a valid ReAct decision with action."""
    try:
        available = client.is_available()
    except Exception:
        return _llm_failure("provider_unavailable", "ReAct probe: 模型不可用")

    if not available:
        return _llm_failure("missing_configuration", "ReAct probe: 模型配置不完整")

    system = (
        "You are a traceable research agent. Output one strict JSON object only, no Markdown. "
        'Required schema: {"thought":"short rationale","action":"MUST be one of ['
        + allowed_tools
        + ', finish]","args":{},"finish_reason":null}. '
        "Select exactly one allowed tool or finish."
    )
    user_payload = {
        "task": task,
        "allowed_tools": [t.strip() for t in allowed_tools.split(",")],
        "observation_history": [],
    }
    try:
        response = client.structured_complete(
            [
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
            ],
            temperature=0.0,
            max_tokens=800,
        )
    except Exception:
        return _llm_failure("provider_unavailable", "ReAct probe: 请求失败")

    if not response.success:
        return _llm_failure(
            response.metadata.get("error_type") or "provider_unavailable",
            "ReAct probe: 模型响应失败",
            response.usage is not None,
        )

    parsed = _extract_json(str(response.content or ""))
    if parsed is None:
        return _llm_failure(
            "structured_output_invalid",
            "ReAct probe: 响应不是有效 JSON 对象",
            response.usage is not None,
        )

    action = str(parsed.get("action") or "").strip().lower().replace("-", "_")
    if not action:
        return _llm_failure(
            "structured_output_invalid",
            "ReAct probe: 响应缺少 action 字段",
            response.usage is not None,
        )

    allowed = {t.strip().lower().replace("-", "_") for t in allowed_tools.split(",")} | {"finish"}
    if action not in allowed:
        return _llm_failure(
            "structured_output_invalid",
            f"ReAct probe: action '{action}' 不在允许列表中",
            response.usage is not None,
        )

    return {
        "success": True,
        "error_type": None,
        "detail": f"ReAct probe: action={action} 有效",
        "usage_parsed": response.usage is not None,
        "structured_output": True,
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from raw LLM text."""
    import re
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except (TypeError, ValueError):
            pass
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(stripped[first : last + 1])
        except (TypeError, ValueError):
            pass
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    return None


def _llm_failure(
    error_type: str,
    detail: str,
    usage_parsed: bool = False,
) -> dict[str, Any]:
    return {
        "success": False,
        "error_type": error_type,
        "detail": detail,
        "usage_parsed": usage_parsed,
        "structured_output": False,
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
        basic = _check_llm_basic(shared_client)
        planner = _check_planner_capability(shared_client)
        react = _check_react_capability(shared_client)
    else:
        actor_for_probe = actor_probe_client or create_llm_client(
            settings, actor_identity[0], actor_identity[1]
        )
        synth_for_probe = synthesizer_probe_client or create_llm_client(
            settings, synthesizer_identity[0], synthesizer_identity[1]
        )
        basic = _check_llm_basic(synth_for_probe)
        planner = _check_planner_capability(synth_for_probe)
        react = _check_react_capability(actor_for_probe)

    def _cap(name: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "category": "llm",
            "configured": True,
            "reachable": result["success"],
            "usable": result["success"],
            "mode": "real",
            "detail": result["detail"],
            "error_type": result["error_type"],
            "checks": {
                "usage_parsed": result["usage_parsed"],
                "structured_output": result["structured_output"],
            },
            "checked_at": checked_at,
        }

    # Replace all static LLM templates with the three granular probe results
    dynamic_llm_names = {
        "llm", "llm_actor", "llm_synthesizer",
        "llm_basic", "llm_planner", "llm_react",
    }
    items = [item for item in items if item.get("name") not in dynamic_llm_names]
    items[0:0] = [
        _cap("llm_basic", basic),
        _cap("llm_planner", planner),
        _cap("llm_react", react),
    ]

    actor_required = settings.execution_mode == "react" or settings.deep_research_enabled
    synthesizer_required = settings.report_generation_mode == "llm"
    if settings.llm_planner_enabled and not planner["success"]:
        blockers.append(
            {
                "capability": "llm_planner",
                "error_type": str(planner["error_type"]),
                "message": str(planner["detail"]),
            }
        )
    if actor_required and not react["success"]:
        blockers.append(
            {
                "capability": "llm_react",
                "error_type": str(react["error_type"]),
                "message": str(react["detail"]),
            }
        )
    if synthesizer_required and not basic["success"]:
        blockers.append(
            {
                "capability": "report_synthesis",
                "error_type": str(basic["error_type"]),
                "message": str(basic["detail"]),
            }
        )
    for role_result in {id(basic): basic, id(planner): planner, id(react): react}.values():
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
