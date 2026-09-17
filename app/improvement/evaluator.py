"""Deterministically evaluate completed runs with claim-centric evidence metrics."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent.outcome import trusted_run_ids
from app.evidence.quality import calculate_evidence_quality
from app.improvement.models import ImprovementLog
from app.reporting.integrity import REPORT_INTEGRITY_VERSION
from app.research.result_context import get_result_provenance_bundle, list_result_traces, resolve_research_result
from app.trace import store as trace_store


logger = logging.getLogger(__name__)


def _score_auditability(citation_count: int, citation_accuracy: float, full_text_ratio: float) -> float:
    if citation_count == 0:
        return 3.0
    return round(min(min(citation_count / 10, 1.0) * 5 + citation_accuracy * 3 + full_text_ratio * 2, 10), 1)


def _compute_overall(relevance: float, factual: float, coverage: float, source_quality: float, auditability: float) -> float:
    return round(
        relevance * 0.20 + factual * 10 * 0.25 + coverage * 0.20
        + source_quality * 0.15 + auditability * 0.20,
        1,
    )


def _classify_question(task: str) -> str:
    from app.agent.routing import SKILL_SIGNALS

    scores: dict[str, int] = {}
    lowered = task.casefold()
    for skill_name, signals in SKILL_SIGNALS.items():
        for keyword, weight in signals:
            if keyword.casefold() in lowered:
                scores[skill_name] = scores.get(skill_name, 0) + weight
    if not scores:
        return "general"
    return {
        "systematic_review": "academic_literature",
        "local_audit": "local_audit",
        "technical_docs_research": "technical_docs",
        "deep_web_research": "deep_research",
        "quick_search": "quick_fact",
        "hybrid_research": "technical_comparison",
    }.get(max(scores, key=lambda key: scores[key]), "general")


def auto_evaluate_and_log(db: Session, run_id: str) -> ImprovementLog | None:
    try:
        result = resolve_research_result(db, run_id)
    except ValueError:
        return None
    run = trace_store.get_agent_run(db, result.root_run_id)
    if run is None or run.status != "completed":
        return None
    if result.root_run_id not in set(db.scalars(trusted_run_ids())):
        return None
    existing = db.get(ImprovementLog, result.root_run_id)
    if existing is not None:
        return existing

    bundle = get_result_provenance_bundle(db, result)
    list_result_traces(db, result)
    citations = int(getattr(run, "citation_total", 0) or 0)
    accuracy = float(getattr(run, "citation_accuracy", 0.0) or 0.0)
    verified = int(getattr(run, "citation_supported", 0) or 0)
    quality = calculate_evidence_quality(bundle, citation_accuracy=accuracy)
    metrics = bundle.get("metrics") or {}
    effective_source_count = int(metrics.get("independent_source_count", quality["independent_source_count"]))
    effective_passage_count = int(metrics.get("effective_unique_passage_count", len(bundle.get("passages") or [])))
    quality["independent_source_count"] = effective_source_count
    quality["unique_resource_count"] = max(
        int(quality.get("unique_resource_count") or 0),
        int(metrics.get("unique_resource_count") or 0),
    )
    passages = [item for item in bundle.get("passages") or [] if isinstance(item, dict)]
    full_text_ratio = (
        sum(str(item.get("content_basis") or "") == "full_text" for item in passages) / len(passages)
        if passages else 0.0
    )
    relevance = 6.0
    factual = round(verified / citations, 2) if citations else 0.0
    coverage = round(float(quality["claim_support_coverage"]) * 10, 1)
    source_quality = round(float(quality["evidence_quality_score"]), 1)
    auditability = _score_auditability(citations, accuracy, full_text_ratio)

    try:
        plan = json.loads(run.plan_json or "{}")
    except (json.JSONDecodeError, TypeError):
        plan = {}
    routing = plan.get("skill_routing") or {}
    composition = routing.get("composed_from") or routing.get("selected_skill")
    mode = "deep_research_v2" if plan.get("execution_mode") == "deep_research_v2" or run.engine_version == "v2" else plan.get("execution_mode")

    entry = ImprovementLog(
        run_id=result.root_run_id,
        question_category=_classify_question(run.task),
        skill_composition=json.dumps(composition, ensure_ascii=False) if isinstance(composition, list) else composition,
        execution_mode=mode,
        overall_score=_compute_overall(relevance, factual, coverage, source_quality, auditability),
        relevance_score=relevance,
        factual_accuracy=factual,
        coverage_score=coverage,
        source_quality_score=source_quality,
        auditability_score=auditability,
        citation_count=citations,
        quality_schema_version=quality["quality_schema_version"],
        evidence_quality_score=quality["evidence_quality_score"],
        claim_support_coverage=quality["claim_support_coverage"],
        strong_claim_coverage=quality["strong_claim_coverage"],
        independent_claim_coverage=quality["independent_claim_coverage"],
        mean_cited_reliability=quality["mean_cited_reliability"],
        p25_cited_reliability=quality["p25_cited_reliability"],
        independent_source_count=quality["independent_source_count"],
        unique_resource_count=quality["unique_resource_count"],
        unresolved_conflict_count=quality["unresolved_conflict_count"],
        evaluation_metadata_json=json.dumps(
            {
                **quality,
                "result_scope": "research_scope" if result.is_scope else "run",
                "scope_id": result.scope_id,
                "engine_version": result.engine_version,
                "report_integrity_version": REPORT_INTEGRITY_VERSION,
                "independent_source_count": effective_source_count,
                "effective_source_count": effective_source_count,
                "effective_passage_count": effective_passage_count,
                "coverage_evaluable": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    db.add(entry)
    db.commit()
    logger.info("Improvement log written for result %s: overall=%.1f", result.root_run_id, entry.overall_score)
    return entry
