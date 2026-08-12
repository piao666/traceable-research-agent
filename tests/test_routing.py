"""Focused contracts for automatic Skill and execution routing."""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.routing import select_execution_mode, select_skill


def _skill(name: str, tools: list[str] | None = None):
    return SimpleNamespace(name=name, status="valid", required_tools=tools or [])


def test_explicit_search_uses_planned_execution() -> None:
    decision = select_execution_mode(
        "去 GitHub 搜索最受欢迎的三个 Agent 仓库",
        {"steps": [{"tool_name": "mcp_github_search"}, {"tool_name": "report_writer"}]},
    )

    assert decision["requested"] == "planned"
    assert decision["selected"] == "planned"


def test_open_ended_multi_source_research_uses_react() -> None:
    decision = select_execution_mode(
        "深入调研 AI Agent 评测市场格局，比较不同方案、交叉验证证据并给出风险建议",
        {
            "skill_routing": {"selected_skill": "deep_web_research"},
            "steps": [
                {"tool_name": "tavily_search"},
                {"tool_name": "web_fetcher"},
                {"tool_name": "mcp_github_search"},
                {"tool_name": "report_writer"},
            ],
        },
    )

    assert decision["requested"] == "react"
    assert decision["selected"] == "react"
    assert "比较与权衡" in decision["signals"]


def test_systematic_review_skill_uses_react_even_without_extra_signal() -> None:
    decision = select_execution_mode(
        "系统综述 AI Agent 评测论文并核对 DOI",
        {"skill_routing": {"selected_skill": "systematic_review"}, "steps": []},
    )

    assert decision["requested"] == "react"
    assert decision["selected"] == "react"
    assert "复杂研究流程" in decision["signals"]


def test_react_disabled_records_planned_fallback() -> None:
    decision = select_execution_mode(
        "全面调研并比较多个来源，分析证据冲突和风险",
        {"steps": []},
        react_enabled=False,
    )

    assert decision["requested"] == "react"
    assert decision["selected"] == "planned"
    assert decision["fallback"]


def test_skill_router_selects_unique_strong_match() -> None:
    decision = select_skill(
        "系统综述近五年 AI Agent 评测论文并核对 DOI",
        [_skill("systematic_review"), _skill("quick_search")],
    )

    assert decision["selected_skill"] == "systematic_review"
    assert decision["candidates"][0]["score"] >= 4


def test_skill_router_uses_generic_planner_for_ambiguous_task() -> None:
    decision = select_skill(
        "分析这个问题并给出建议",
        [_skill("deep_web_research"), _skill("technical_docs_research")],
    )

    assert decision["selected_skill"] is None


def test_skill_router_rejects_tools_outside_allowlist() -> None:
    decision = select_skill(
        "系统综述相关论文和 DOI",
        [_skill("systematic_review", ["arxiv_search", "crossref_search"])],
        allowed_tools=["tavily_search"],
    )

    assert decision["selected_skill"] is None
