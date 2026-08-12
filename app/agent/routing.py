"""Deterministic, auditable routing for Skills and execution modes."""

from __future__ import annotations

import re
from typing import Any


SKILL_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "systematic_review": (
        ("系统综述", 6), ("文献综述", 6), ("meta-analysis", 6),
        ("学术文献", 4), ("论文", 2), ("doi", 2), ("arxiv", 2),
    ),
    "local_audit": (
        ("本地资料", 5), ("本地文件", 5), ("内部资料", 4),
        ("sql", 3), ("数据库", 2), ("本地", 1),
    ),
    "technical_docs_research": (
        ("技术文档", 6), ("官方文档", 5), ("api 文档", 5),
        ("接口文档", 5), ("sdk", 3), ("框架", 2), ("github", 2),
    ),
    "deep_web_research": (
        ("深度调研", 6), ("深入调研", 6), ("全面调研", 5),
        ("竞品", 4), ("市场格局", 4), ("多源", 3),
        ("网页正文", 3), ("交叉验证", 3),
    ),
    "quick_search": (
        ("快速搜索", 5), ("快速检索", 5), ("查一下", 3),
        ("搜一下", 3), ("搜索", 1), ("检索", 1), ("查找", 1),
    ),
}

COMPLEXITY_SIGNAL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("开放式调研", ("深度", "深入", "全面", "系统性", "调研")),
    ("比较与权衡", ("比较", "对比", "竞品", "权衡", "选型", "差异")),
    ("综合判断", ("风险", "趋势", "建议", "策略", "路线", "格局")),
    ("多源验证", ("多源", "交叉验证", "证据", "冲突", "不同来源")),
    ("迭代探索", ("补充检索", "迭代", "追踪", "验证假设", "信息缺口")),
)

EXPLICIT_ACTION_PATTERN = re.compile(
    r"(?:去|在|用|通过)\s*[^，。；;]{0,24}?(?:搜(?:索)?|检索|查询|读取|抓取|查找)",
    re.IGNORECASE,
)


def _tool_allowed(required_tools: list[str], allowed_tools: list[str] | None) -> bool:
    if allowed_tools is None:
        return True
    allowed = set(allowed_tools)
    return all(name in allowed for name in required_tools)


def select_skill(
    task: str,
    skill_metas: list[Any],
    allowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    """Select a strongly matching valid Skill or return a generic-planner route."""

    task_lower = str(task or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for meta in skill_metas:
        name = str(getattr(meta, "name", "") or "")
        if not name or str(getattr(meta, "status", "valid")) != "valid":
            continue
        required_tools = list(getattr(meta, "required_tools", []) or [])
        if not _tool_allowed(required_tools, allowed_tools):
            continue
        score = 0
        signals: list[str] = []
        for keyword, weight in SKILL_SIGNALS.get(name, ()):
            if keyword.lower() in task_lower:
                score += weight
                signals.append(keyword)
        if name == "quick_search" and EXPLICIT_ACTION_PATTERN.search(task_lower):
            score += 4
            signals.append("明确来源与动作")
        candidates.append({"skill_name": name, "score": score, "signals": signals})

    candidates.sort(key=lambda item: (-int(item["score"]), str(item["skill_name"])))
    winner = candidates[0] if candidates else None
    runner_up_score = int(candidates[1]["score"]) if len(candidates) > 1 else 0
    selected = None
    reason = "没有 Skill 达到明确匹配阈值，使用通用 Planner。"
    if winner and int(winner["score"]) >= 4 and int(winner["score"]) > runner_up_score:
        selected = str(winner["skill_name"])
        reason = f"命中 {', '.join(winner['signals'])}，选择预定义研究流程。"
    elif winner and int(winner["score"]) >= 4:
        reason = "多个 Skill 得分接近，交由通用 Planner 动态规划。"

    return {
        "requested": "auto",
        "selected_skill": selected,
        "reason": reason,
        "candidates": candidates[:3],
    }


def select_execution_mode(
    task: str,
    plan: dict[str, Any],
    override: str | None = None,
    react_enabled: bool = True,
) -> dict[str, Any]:
    """Choose planned for explicit work and ReAct for open-ended research."""

    normalized_override = str(override or "").strip().lower()
    if normalized_override in {"planned", "react"}:
        requested = normalized_override
        selected = requested if requested != "react" or react_enabled else "planned"
        return {
            "requested": requested,
            "selected": selected,
            "reason": "调用方显式指定执行模式。",
            "signals": [],
            "fallback": "ReAct 未启用，降级为固定计划。" if selected != requested else None,
        }

    task_lower = str(task or "").strip().lower()
    signals = [
        label
        for label, keywords in COMPLEXITY_SIGNAL_GROUPS
        if any(keyword.lower() in task_lower for keyword in keywords)
    ]
    selected_skill = str((plan.get("skill_routing") or {}).get("selected_skill") or "")
    complex_skill = selected_skill in {"deep_web_research", "systematic_review"}
    if complex_skill:
        signals.append("复杂研究流程")
    tool_names = {
        str(step.get("tool_name") or "")
        for step in plan.get("steps") or []
        if isinstance(step, dict) and step.get("tool_name") != "report_writer"
    }
    if len(tool_names) >= 3:
        signals.append("多工具协作")

    signals = list(dict.fromkeys(signals))
    explicit_action = bool(EXPLICIT_ACTION_PATTERN.search(task_lower) or re.search(r"https?://", task_lower))
    requested = "react" if (
        (len(signals) >= 2 or complex_skill)
        and not explicit_action
    ) else "planned"
    selected = requested if requested != "react" or react_enabled else "planned"
    if requested == "react":
        reason = f"任务包含 {', '.join(signals)}，需要观察驱动的动态决策。"
    elif explicit_action:
        reason = "目标、来源或动作明确，使用可预测的固定计划。"
    else:
        reason = "任务复杂度较低，使用可预测的固定计划。"
    return {
        "requested": requested,
        "selected": selected,
        "reason": reason,
        "signals": signals,
        "fallback": "ReAct 未启用，降级为固定计划。" if selected != requested else None,
    }
