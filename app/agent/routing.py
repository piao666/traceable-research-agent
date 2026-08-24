"""Hybrid routing for Skills and execution modes.

Keyword scoring provides a fast, deterministic first pass. When no Skill
reaches a clear threshold, an LLM classifier is invoked as a fallback to
avoid the blind spots of pure keyword matching.
"""

from __future__ import annotations

import json
import re
from typing import Any

SKILL_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "systematic_review": (
        ("系统综述", 6), ("文献综述", 6), ("meta-analysis", 6),
        ("学术文献", 4), ("论文", 2), ("doi", 2), ("arxiv", 2),
        ("文献", 2), ("学术", 2),
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
        ("对比", 4), ("比较", 4), ("优劣", 3), ("差异", 3),
        ("技术突破", 4), ("最新", 3), ("性能", 2),
        ("安全风险", 4), ("风险", 3), ("评估", 2),
        ("适用场景", 3), ("应用场景", 3),
        ("调研", 3), ("分析", 2),
    ),
    "quick_search": (
        ("快速搜索", 5), ("快速检索", 5), ("查一下", 3),
        ("搜一下", 3), ("搜索", 1), ("检索", 1), ("查找", 1),
        ("什么是", 2), ("是什么", 2), ("多少", 1),
    ),
}

SKILL_DESCRIPTIONS = {
    "systematic_review": "多源学术文献系统综述（arXiv+Semantic Scholar+OpenAlex+Crossref），适合需要文献综述、DOI核实、学术论文检索的任务",
    "local_audit": "本地资料复盘（文件读取+SQL查询），适合审计本地文档、数据库查询的任务",
    "technical_docs_research": "技术文档调研（GitHub搜索+文档抓取），适合API文档、SDK文档、框架文档调研",
    "deep_web_research": "深度网页调研（搜索发现→正文抓取→证据压缩→报告），适合技术对比、行业分析、综合调研",
    "quick_search": "快速搜索（仅搜索→报告，无抓取），适合简单事实查询、快速问答",
}

LLM_SKILL_CLASSIFIER_PROMPT = """你是一个任务分类专家。请根据用户的研究任务，从以下 Skill 中选择最合适的一个。

可用 Skill:
{skill_list}

请只输出 JSON，格式为: {{"skill_name": "<选中的skill名>", "confidence": 0.0-1.0, "rationale": "<一句话理由>"}}"""

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


def _keyword_score(task_lower: str, skill_metas: list[Any], allowed_tools: list[str] | None) -> list[dict[str, Any]]:
    """Fast keyword-based scoring pass."""
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
    return candidates


def _llm_classify(task: str, skill_metas: list[Any], llm_client=None) -> str | None:
    """LLM fallback classifier when keyword scoring is ambiguous."""
    if llm_client is None or not llm_client.is_available():
        return None

    valid_skills = [
        m for m in skill_metas
        if str(getattr(m, "name", "") or "") and str(getattr(m, "status", "valid")) == "valid"
    ]
    if not valid_skills:
        return None

    skill_list = "\n".join(
        f"- {getattr(s, 'name', '')}: {SKILL_DESCRIPTIONS.get(getattr(s, 'name', ''), '')}"
        for s in valid_skills
    )
    prompt = LLM_SKILL_CLASSIFIER_PROMPT.format(skill_list=skill_list)

    try:
        from app.llm.base import LLMMessage
        response = llm_client.complete([
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=f"研究任务: {task}"),
        ], temperature=0.0, max_tokens=200)
        if not response.success or not response.content:
            return None
        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
        result = json.loads(content)
        skill_name = str(result.get("skill_name") or "")
        if skill_name in {str(getattr(s, "name", "")) for s in valid_skills}:
            return skill_name
    except Exception:
        pass
    return None


def select_skill(
    task: str,
    skill_metas: list[Any],
    allowed_tools: list[str] | None = None,
    llm_client=None,
) -> dict[str, Any]:
    """Select a matching Skill: keyword first, LLM fallback when ambiguous."""

    task_lower = str(task or "").strip().lower()
    candidates = _keyword_score(task_lower, skill_metas, allowed_tools)

    winner = candidates[0] if candidates else None
    runner_up_score = int(candidates[1]["score"]) if len(candidates) > 1 else 0
    selected = None
    reason = ""
    source = "keyword"

    # Clear keyword winner
    if winner and int(winner["score"]) >= 4 and int(winner["score"]) > runner_up_score:
        selected = str(winner["skill_name"])
        reason = f"命中 {', '.join(winner['signals'])}，选择预定义研究流程。"
    elif winner and int(winner["score"]) >= 4:
        # Tie — try LLM to break it
        tied = [c for c in candidates if int(c["score"]) >= 4]
        if llm_client:
            llm_choice = _llm_classify(task, skill_metas, llm_client)
            if llm_choice and llm_choice in {c["skill_name"] for c in tied}:
                selected = llm_choice
                reason = f"关键词得分接近，LLM 选择 {llm_choice}。"
                source = "llm"
            else:
                reason = "多个 Skill 得分接近，交由通用 Planner 动态规划。"
        else:
            reason = "多个 Skill 得分接近，交由通用 Planner 动态规划。"
    else:
        # Low score — try LLM
        if llm_client:
            llm_choice = _llm_classify(task, skill_metas, llm_client)
            if llm_choice:
                selected = llm_choice
                reason = f"关键词未命中，LLM 分类为 {llm_choice}。"
                source = "llm"
            else:
                reason = "没有 Skill 达到明确匹配阈值，使用通用 Planner。"
        else:
            reason = "没有 Skill 达到明确匹配阈值，使用通用 Planner。"

    return {
        "requested": "auto",
        "selected_skill": selected,
        "reason": reason,
        "source": source,
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