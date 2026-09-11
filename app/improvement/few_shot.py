"""Few-shot example library for self-improving prompts.

High-quality runs (overall_score ≥ 7.5, citations ≥ 5) are promoted to
a JSON library. The library is injected into Planner and Reporter prompts
as reference examples, improving output quality over time.

Limits: 20 total, 5 per category. Eviction by score + recency.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.evidence.citation_validator import extract_final_answer_section
from app.improvement.models import ImprovementLog
from app.agent.outcome import trusted_run_ids, INTEGRITY_VERSION
from app.research.result_context import resolve_research_result
from app.research.scope import list_scope_nodes
from app.trace import store as trace_store

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = ROOT / "workspace" / "improvement" / "few_shot_library.json"

# Promotion thresholds
_MIN_OVERALL = 7.5
_MIN_CITATIONS = 5
_MAX_TOTAL = 20
_MAX_PER_CATEGORY = 5


def extract_final_answer_excerpt(markdown: str, max_chars: int = 1200) -> str:
    """Return a bounded excerpt from only the rendered final-answer section."""

    return extract_final_answer_section(markdown)[: max(0, int(max_chars))]


def _research_strategy_summary(db, result, plan: dict[str, Any]) -> tuple[str, int]:
    if result.is_scope and result.scope_id:
        nodes = list_scope_nodes(db, result.scope_id)
        labels = [
            "Discovery"
            if node.node_type == "discovery"
            else (node.topic.strip() or node.research_goal.strip() or node.node_type)
            for node in nodes[:8]
        ]
        return " → ".join(labels), len(nodes)
    steps = plan.get("steps") or []
    return (
        " → ".join(
            step.get("tool_name", "?")
            for step in steps[:6]
            if isinstance(step, dict)
        ),
        0,
    )


def _load_library() -> dict[str, Any]:
    """Load the few-shot library. Returns empty dict if missing."""
    if not LIBRARY_PATH.is_file():
        return {"examples": []}
    try:
        data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
        return data if data.get("integrity_version") == INTEGRITY_VERSION else {"examples": []}
    except (json.JSONDecodeError, OSError):
        return {"examples": []}


def _save_library(data: dict[str, Any]) -> None:
    data["integrity_version"] = INTEGRITY_VERSION
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def promote_to_few_shot(run_id: str) -> bool:
    """Promote a run to the few-shot library if it meets quality thresholds."""
    with SessionLocal() as db:
        try:
            result = resolve_research_result(db, run_id)
        except ValueError:
            return False
        root_run_id = result.root_run_id
        if root_run_id not in set(db.scalars(trusted_run_ids())):
            return False
        log = db.get(ImprovementLog, root_run_id)
        if log is None:
            return False
        if log.overall_score < _MIN_OVERALL:
            return False
        if log.citation_count < _MIN_CITATIONS:
            return False

        run = trace_store.get_agent_run(db, root_run_id)
        if run is None:
            return False

        try:
            plan = json.loads(run.plan_json or "{}")
        except Exception:
            plan = {}
        deep_v2 = (
            plan.get("execution_mode") == "deep_research_v2"
            or run.engine_version == "v2"
        )
        if deep_v2 and not (
            (plan.get("research_outcome") or {}).get("status") == "passed"
            and (plan.get("report_integrity") or {}).get("status") == "passed"
        ):
            return False
        plan_summary, research_node_count = _research_strategy_summary(
            db, result, plan
        )

        # Extract only the final answer; planning and audit sections are excluded.
        report_excerpt = ""
        if run.report_path:
            rp = ROOT / run.report_path
            if rp.is_file():
                text = rp.read_text(encoding="utf-8", errors="replace")
                report_excerpt = extract_final_answer_excerpt(text, max_chars=1200)

    library = _load_library()
    examples: list[dict[str, Any]] = library.get("examples", [])

    # Check if already promoted
    if any(e.get("run_id") == root_run_id for e in examples):
        return False

    category = log.question_category or "general"
    category_count = sum(1 for e in examples if e.get("category") == category)
    if category_count >= _MAX_PER_CATEGORY:
        # Evict lowest-scored in this category
        cat_examples = [e for e in examples if e.get("category") == category]
        cat_examples.sort(key=lambda e: e.get("overall_score", 0))
        examples.remove(cat_examples[0])

    # Evict if total exceeds cap
    if len(examples) >= _MAX_TOTAL:
        examples.sort(key=lambda e: e.get("overall_score", 0))
        examples.pop(0)

    examples.append({
        "run_id": root_run_id,
        "category": category,
        "question": run.task,
        "skill_composition": log.skill_composition,
        "overall_score": log.overall_score,
        "plan_summary": plan_summary,
        "report_excerpt": report_excerpt,
        "engine_version": result.engine_version,
        "scope_id": result.scope_id,
        "research_node_count": research_node_count,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    })

    library["examples"] = examples
    _save_library(library)
    logger.info(
        "Few-shot promoted run %s (score=%.1f, category=%s, total=%d)",
        root_run_id[:8], log.overall_score, category, len(examples),
    )
    return True


def load_few_shot_examples(
    category: str | None = None,
    max_examples: int = 3,
) -> list[dict[str, Any]]:
    """Load few-shot examples, optionally filtered by category."""
    library = _load_library()
    examples = library.get("examples", [])
    if category:
        examples = [e for e in examples if e.get("category") == category]
    # Sort by score descending, take top N
    examples.sort(key=lambda e: e.get("overall_score", 0), reverse=True)
    return examples[:max_examples]


def format_few_shot_for_prompt(examples: list[dict[str, Any]]) -> str:
    """Format few-shot examples for injection into a system prompt."""
    if not examples:
        return ""
    parts = ["以下是你过去高质量完成的任务示例，供参考：", ""]
    for i, ex in enumerate(examples, 1):
        parts.append(
            f"### 示例 {i}\n"
            f"**问题**：{ex.get('question', '')}\n"
            f"**执行步骤**：{ex.get('plan_summary', '')}\n"
            f"**报告片段**：\n{ex.get('report_excerpt', '')}\n"
        )
    return "\n".join(parts)
