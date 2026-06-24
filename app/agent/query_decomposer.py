"""Sub-query decomposer for complex research tasks.

Phase B optimization: before the Planner assigns tools, decompose the
original task into N independent sub-questions. Each sub-question can
be researched independently and combined into a richer report.

This mirrors GPT Researcher's core planner-executor pattern where the
planner generates sub-questions rather than directly assigning tools.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm.base import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# Tasks below this length are likely already specific enough → skip decomposition
_MIN_TASK_LEN_FOR_DECOMP = 15

# Keywords that suggest the task is broad enough to benefit from decomposition
_BROAD_KEYWORDS = (
    "调研", "研究", "分析", "了解", "学习", "介绍", "综述",
    "research", "analyze", "survey", "overview", "summarize", "explain",
)

_DECOMP_SYSTEM_PROMPT = """你是专业调研规划专家。给定一个调研任务，你需要将其分解为
若干个独立、具体、可通过文档/数据库/网络搜索回答的子问题。

规则：
- 每个子问题必须独立（不依赖其他子问题的答案）
- 子问题应覆盖原任务的不同角度或维度
- 子问题数量在 2–{n} 个之间（根据任务复杂度决定）
- 输出格式：纯 JSON 字符串数组，不要任何解释

示例输入：「请调研 Python 异步编程的最新进展」
示例输出：["Python asyncio 的核心原理和设计模式是什么？", "asyncio 在生产环境有哪些最佳实践？", "Python 异步框架（FastAPI、aiohttp 等）的性能对比如何？"]

只输出 JSON 数组，不要 Markdown 代码块。"""


def _is_broad_task(task: str) -> bool:
    """Heuristic: is this task broad enough to benefit from decomposition?"""
    if len(task) < _MIN_TASK_LEN_FOR_DECOMP:
        return False
    task_lower = task.lower()
    return any(kw in task_lower for kw in _BROAD_KEYWORDS)


def _parse_sub_queries(raw: str) -> list[str]:
    """Parse LLM output into a clean list of sub-questions."""
    raw = raw.strip()

    # Strip Markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(q).strip() for q in result if str(q).strip()]
    except json.JSONDecodeError:
        pass

    # Fallback: extract lines that look like questions
    lines = [line.strip(' -•"\'') for line in raw.splitlines()]
    questions = [l for l in lines if len(l) > 8]
    return questions


def decompose_task(
    task: str,
    llm_client: LLMClient,
    n: int = 4,
    force: bool = False,
) -> list[str]:
    """Decompose a broad task into N independent sub-questions.

    Args:
        task:       Original research task.
        llm_client: LLM client for decomposition.
        n:          Maximum number of sub-questions.
        force:      If True, skip the broad-task heuristic check.

    Returns:
        List of sub-questions. If decomposition fails or task is already
        specific, returns [task] (the original task as a single item).
    """
    if not force and not _is_broad_task(task):
        logger.debug("Task too specific for decomposition, skipping: %s", task[:60])
        return [task]

    if not llm_client.is_available():
        logger.info("LLM client unavailable, skipping sub-query decomposition.")
        return [task]

    messages = [
        LLMMessage(
            role="system",
            content=_DECOMP_SYSTEM_PROMPT.format(n=n),
        ),
        LLMMessage(
            role="user",
            content=f"请将以下调研任务分解为子问题：\n{task}",
        ),
    ]

    try:
        response = llm_client.complete(messages)
        if not response.success or not response.content:
            logger.warning("Decomposition LLM call failed: %s", response.error_message)
            return [task]

        sub_queries = _parse_sub_queries(response.content)

        if len(sub_queries) < 2:
            logger.info("Decomposition produced too few items (%d), using original task.", len(sub_queries))
            return [task]

        sub_queries = sub_queries[:n]
        logger.info("Decomposed '%s...' into %d sub-queries.", task[:40], len(sub_queries))
        return sub_queries

    except Exception as exc:
        logger.warning("Sub-query decomposition error: %s", exc)
        return [task]


def decompose_and_annotate_plan(
    task: str,
    plan: dict[str, Any],
    llm_client: LLMClient,
    n: int = 4,
) -> dict[str, Any]:
    """Decompose task and attach sub_queries to the plan for downstream use.

    The sub_queries list is stored in plan["sub_queries"] so that:
    - The reporter can use each sub-question as a section header
    - The parallel executor (Phase C) can run one retrieval per sub-query

    Returns the mutated plan dict.
    """
    if plan.get("sub_queries"):
        return plan   # already decomposed

    sub_queries = decompose_task(task, llm_client, n=n)
    plan["sub_queries"] = sub_queries
    plan["decomposed"] = len(sub_queries) > 1
    return plan
