"""Auto-evaluate a completed run and write to improvement_log.

Deterministic scoring — no LLM calls. Reuses the same formulas as the
L3 quality evaluation module (app/eval/quality/).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.improvement.models import ImprovementLog
from app.trace import store as trace_store

logger = logging.getLogger(__name__)

# ── Reuse scoring formulas from quality module ──────────────────────────


def _score_source_quality(t0: int, t1: int, t2: int, citation_count: int = 0) -> float:
    total = t0 + t1 + t2
    if total == 0:
        return 5.0
    citation_rate = min(citation_count / max(total, 1), 1.0) if citation_count > 0 else 0.5
    tier_score = (t0 * 10 + t1 * 7 + t2 * 5) / total
    volume_bonus = min(total / 20, 1.0) * 1.0
    diversity = 0.0
    if t0 > 0: diversity += 0.2
    if t1 > 0: diversity += 0.2
    if t2 > 0: diversity += 0.2
    t2_ratio = t2 / total if total > 0 else 0
    t2_penalty = 0.85 if t2_ratio > 0.5 else 1.0
    raw = (tier_score * citation_rate + volume_bonus + diversity) * t2_penalty
    return round(min(raw, 10), 1)


def _score_auditability(citation_count: int, citation_accuracy: float, full_text_ratio: float) -> float:
    if citation_count == 0:
        return 3.0
    citation_score = min(citation_count / 10, 1.0) * 5
    accuracy_score = citation_accuracy * 3
    full_text_score = full_text_ratio * 1.5
    return round(min(citation_score + accuracy_score + full_text_score, 10), 1)


def _compute_overall(
    relevance: float, factual: float, coverage: float,
    source_quality: float, auditability: float,
) -> float:
    return round(
        relevance * 0.20 + factual * 10 * 0.25 + coverage * 0.20
        + source_quality * 0.15 + auditability * 0.20, 1,
    )


# ── Tier extraction from traces ────────────────────────────────────────


def _extract_tiers(traces: list[Any]) -> dict[str, int]:
    t0 = t1 = t2 = 0
    for t in traces:
        try:
            out = json.loads(getattr(t, "output_json", None) or "{}")
            gov = (out.get("metadata") or {}).get("source_governance", {})
            if isinstance(gov, dict):
                tiers = gov.get("tier_counts", {})
                t0 += int(tiers.get("T0", 0))
                t1 += int(tiers.get("T1", 0))
                t2 += int(tiers.get("T2", 0))
        except Exception:
            pass
    return {"T0": t0, "T1": t1, "T2": t2}


def _extract_content_basis(traces: list[Any]) -> dict[str, int]:
    full_text = partial = snippet = 0
    for t in traces:
        try:
            out = json.loads(getattr(t, "output_json", None) or "{}")
            pages = out.get("output", {}).get("pages", [])
            if isinstance(pages, list):
                for page in pages:
                    if isinstance(page, dict):
                        cb = page.get("content_basis", "")
                        if cb == "full_text": full_text += 1
                        elif cb == "partial": partial += 1
                        elif cb == "snippet_only": snippet += 1
        except Exception:
            pass
    return {"full_text": full_text, "partial": partial, "snippet_only": snippet}


# ── Question classification (reuse routing signals) ────────────────────


def _classify_question(task: str) -> str:
    """Classify task into a category using keyword signals."""
    from app.agent.routing import SKILL_SIGNALS
    task_lower = task.lower()
    scores: dict[str, int] = {}
    for skill_name, signals in SKILL_SIGNALS.items():
        for keyword, weight in signals:
            if keyword.lower() in task_lower:
                scores[skill_name] = scores.get(skill_name, 0) + weight
    if not scores:
        return "general"
    best = max(scores, key=lambda k: scores[k])
    # Map skill names to question categories
    category_map = {
        "systematic_review": "academic_literature",
        "local_audit": "local_audit",
        "technical_docs_research": "technical_docs",
        "deep_web_research": "deep_research",
        "quick_search": "quick_fact",
        "hybrid_research": "technical_comparison",
    }
    return category_map.get(best, "general")


# ── Main entry point ───────────────────────────────────────────────────


def auto_evaluate_and_log(db: Session, run_id: str) -> ImprovementLog | None:
    """Evaluate a completed run and persist to improvement_log."""
    run = trace_store.get_agent_run(db, run_id)
    if run is None or run.status != "completed":
        return None

    # Check if already logged (idempotent)
    existing = db.get(ImprovementLog, run_id)
    if existing is not None:
        return existing

    traces = trace_store.list_tool_traces(db, run_id)
    citations = getattr(run, "citation_total", 0) or 0
    accuracy = getattr(run, "citation_accuracy", 0.0) or 0.0
    verified = getattr(run, "citation_supported", 0) or 0
    unsupported = getattr(run, "citation_unsupported", 0) or 0

    tiers = _extract_tiers(traces)
    cb = _extract_content_basis(traces)
    total_content = cb["full_text"] + cb["partial"] + cb["snippet_only"]
    full_text_ratio = round(cb["full_text"] / total_content, 2) if total_content > 0 else 0.0

    # Deterministic 5-dimension scoring
    relevance = 6.0  # deterministic default (no LLM judge)
    factual = round(verified / citations, 2) if citations > 0 else 0.0
    coverage = 6.0   # deterministic default
    source_quality = _score_source_quality(tiers["T0"], tiers["T1"], tiers["T2"], citations)
    auditability = _score_auditability(citations, accuracy, full_text_ratio)
    overall = _compute_overall(relevance, factual, coverage, source_quality, auditability)

    # Skill composition
    try:
        plan = json.loads(run.plan_json or "{}")
        skill_routing = plan.get("skill_routing") or {}
        composed_from = skill_routing.get("composed_from")
        skill_comp = json.dumps(composed_from) if composed_from else skill_routing.get("selected_skill")
    except Exception:
        skill_comp = None

    execution_mode = None
    try:
        plan = json.loads(run.plan_json or "{}")
        execution_mode = plan.get("execution_mode") or "planned"
        if plan.get("adaptive_upgrade"):
            execution_mode = "adaptive"
    except Exception:
        pass

    log_entry = ImprovementLog(
        run_id=run_id,
        question_category=_classify_question(run.task),
        skill_composition=str(skill_comp) if skill_comp else None,
        execution_mode=execution_mode,
        overall_score=overall,
        relevance_score=relevance,
        factual_accuracy=factual,
        coverage_score=coverage,
        source_quality_score=source_quality,
        auditability_score=auditability,
        citation_count=citations,
        tier_t0=tiers["T0"],
        tier_t1=tiers["T1"],
        tier_t2=tiers["T2"],
    )
    db.add(log_entry)
    db.commit()
    logger.info("Improvement log written for run %s: overall=%.1f", run_id, overall)
    return log_entry