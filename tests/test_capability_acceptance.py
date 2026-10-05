"""Admission acceptance through the same entry point used by executors."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.capability_requirements import admit_task_capabilities
from app.agent.preflight import check_plan_readiness
from app.config import Settings
from app.runtime.preflight import _PROBE_CACHE, _config_fingerprint, latest_runtime_probe
from app.tools.defaults import register_default_tools


@pytest.mark.parametrize("task,tool,arguments", [
    ("查找官方链接", "tavily_search", {"query": "official links"}),
    ("查找学术论文", "openalex_search", {"query": "research papers"}),
    ("读取CSV数据表", "file_reader", {"path": "demo_research_note.md"}),
    ("查询数据库中的结构化数据", "sql_query", {"query": "SELECT id, title FROM documents"}),
])
def test_valid_task_specific_admission(task, tool, arguments):
    register_default_tools()
    settings = Settings(offline_mode=False, tavily_api_key="fixture-only",
        report_generation_mode="deterministic", llm_planner_enabled=False)
    plan = {"task_contract": {"original_task": task}, "allowed_tools": [tool],
            "steps": [{"step_no": 1, "tool_name": tool, "required": True, "arguments": arguments}]}
    assert check_plan_readiness(plan, settings)["ready"]


def test_fulltext_uses_configured_remote_after_static_failure():
    settings = Settings(fetch_browser_enabled=False, web_fetcher_playwright_enabled=False)
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["full_text"]}}
    rows = [
        {"name": "http", "configured": True, "verification": "probed", "usable": False},
        {"name": "remote_extract", "configured": True, "verification": "probed", "usable": True},
    ]
    with patch("app.retrieval.remote_extract.configured_remote_providers",
               return_value=[SimpleNamespace(available=lambda: True)]):
        assert admit_task_capabilities(plan, settings, rows)[0] == []
        plan["allowed_tools"] = []
        assert admit_task_capabilities(plan, settings, rows)[0]


def test_probe_cache_reaches_production_admission_and_expires():
    settings = Settings(fetch_router_enabled=False, report_generation_mode="deterministic")
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["full_text"]}}
    key = _config_fingerprint(settings)
    probe = {"capabilities": [{"name": "web_fetcher", "configured": True,
        "verification": "probed", "usable": False, "fetch_backend": "http"}]}
    with patch.dict(_PROBE_CACHE, clear=True):
        _PROBE_CACHE[key] = (datetime.now(timezone.utc).timestamp(), probe)
        assert not check_plan_readiness(plan, settings)["ready"]
        # Caller mutations cannot corrupt the trusted cached assessment.
        latest_runtime_probe(settings)["capabilities"][0]["usable"] = True
        assert not check_plan_readiness(plan, settings)["ready"]
        _PROBE_CACHE[key] = (0, probe)
        assert latest_runtime_probe(settings) is None
        assert check_plan_readiness(plan, settings)["ready"]


def test_crossref_is_independent_of_openalex_configuration():
    settings = Settings(openalex_search_enabled=False, crossref_search_enabled=True)
    plan = {"allowed_tools": ["crossref_search"], "task_contract": {"required_capabilities": ["academic"]}}
    assert admit_task_capabilities(plan, settings, [])[0] == []


def test_unknown_explicit_requirement_is_not_silently_dropped():
    plan = {"allowed_tools": [], "task_contract": {"required_capabilities": ["unsupported_capability"]}}
    assert admit_task_capabilities(plan, Settings(), [])[0]


def test_generic_file_permission_cannot_mask_unavailable_web_body():
    settings = Settings(fetch_router_enabled=False)
    plan = {"allowed_tools": ["web_fetcher", "file_reader"],
            "task_contract": {"required_capabilities": ["full_text"]}}
    unavailable = [{"name": "http", "verification": "probed", "usable": False}]
    assert admit_task_capabilities(plan, settings, unavailable)[0]
    plan["steps"] = [{"tool_name": "file_reader", "required": True}]
    assert not admit_task_capabilities(plan, settings, unavailable)[0]


def test_optional_search_does_not_block_local_task():
    settings = Settings(tavily_search_enabled=False, tavily_api_key="")
    plan = {"allowed_tools": ["file_reader", "tavily_search"], "steps": [
        {"tool_name": "file_reader", "required": True},
        {"tool_name": "tavily_search", "required": False}]}
    assert not admit_task_capabilities(plan, settings, [])[0]


def test_unknown_router_row_does_not_erase_verified_backend_failure():
    settings = Settings(fetch_router_enabled=False)
    plan = {"allowed_tools": ["web_fetcher"],
            "task_contract": {"required_capabilities": ["full_text"]}}
    rows = [{"name": "http", "verification": "probed", "usable": False},
            {"name": "web_fetcher", "verification": "unknown", "usable": True}]
    assert admit_task_capabilities(plan, settings, rows)[0]
