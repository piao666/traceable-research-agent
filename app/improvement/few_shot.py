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
from app.improvement.models import ImprovementLog
from app.trace import store as trace_store

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = ROOT / "workspace" / "improvement" / "few_shot_library.json"

# Promotion thresholds
_MIN_OVERALL = 7.5
_MIN_CITATIONS = 5
_MAX_TOTAL = 20
_MAX_PER_CATEGORY = 5


def _load_library() -> dict[str, Any]:
    """Load the few-shot library. Returns empty dict if missing."""
    if not LIBRARY_PATH.is_file():
        return {"examples": []}
    try:
        return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"examples": []}


def _save_library(data: dict[str, Any]) -> None:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def promote_to_few_shot(run_id: str) -> bool:
    """Promote a run to the few-shot library if it meets quality thresholds."""
    with SessionLocal() as db:
        log = db.get(ImprovementLog, run_id)
        if log is None:
            return False
        if log.overall_score < _MIN_OVERALL:
            return False
        if log.citation_count < _MIN_CITATIONS:
            return False

        run = trace_store.get_agent_run(db, run_id)
        if run is None:
            return False

        # Extract plan summary
        plan_summary = ""
        try:
            plan = json.loads(run.plan_json or "{}")
            steps = plan.get("steps") or []
            plan_summary = " → ".join(
                s.get("tool_name", "?") for s in steps[:6]
                if isinstance(s, dict)
            )
        except Exception:
            pass

        # Extract report excerpt (first 500 chars after the title)
        report_excerpt = ""
        if run.report_path:
            rp = ROOT / run.report_path
            if rp.is_file():
                text = rp.read_text(encoding="utf-8", errors="replace")
                # Skip the title line, take next 500 chars
                lines = text.split("\n")
                excerpt_lines: list[str] = []
                for line in lines:
                    if line.startswith("#") and not excerpt_lines:
                        continue
                    excerpt_lines.append(line)
                    if len("\n".join(excerpt_lines)) > 500:
                        break
                report_excerpt = "\n".join(excerpt_lines)[:500]

    library = _load_library()
    examples: list[dict[str, Any]] = library.get("examples", [])

    # Check if already promoted
    if any(e.get("run_id") == run_id for e in examples):
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
        "run_id": run_id,
        "category": category,
        "question": run.task,
        "skill_composition": log.skill_composition,
        "overall_score": log.overall_score,
        "plan_summary": plan_summary,
        "report_excerpt": report_excerpt,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    })

    library["examples"] = examples
    _save_library(library)
    logger.info(
        "Few-shot promoted run %s (score=%.1f, category=%s, total=%d)",
        run_id[:8], log.overall_score, category, len(examples),
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