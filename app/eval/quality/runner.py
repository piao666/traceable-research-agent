"""Research quality evaluation runner.

Executes research tasks and scores output across five dimensions:
  1. Relevance (LLM judge)
  2. Factual accuracy (citation validation)
  3. Coverage (LLM judge)
  4. Source quality (tier distribution from governance metadata)
  5. Auditability (citation count/accuracy from run data)

Usage:
    python -m app.eval.quality.runner --dataset research
    python -m app.eval.quality.runner --dataset all --mode mock
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.eval.quality.judges import judge_report
from app.eval.quality.metrics import ResearchQualityReport, QualityEvalSummary
from app.llm.providers import create_llm_client
from app.skills.registry import init_skill_registry
from app.tools.defaults import register_default_tools
from app.trace import store

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def load_dataset(name: str) -> list[dict[str, Any]]:
    """Load a named dataset from the datasets directory."""
    path = DATASETS_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _compute_tier_metrics(traces: list) -> dict[str, Any]:
    """Extract tier distribution from governance metadata in traces."""
    t0 = 0
    t1 = 0
    t2 = 0
    for t in traces:
        try:
            out = json.loads(t.output_json or "{}")
            gov = (out.get("metadata", {}) or {}).get("source_governance", {})
            if isinstance(gov, dict):
                tiers = gov.get("tier_counts", {})
                t0 += int(tiers.get("T0", 0))
                t1 += int(tiers.get("T1", 0))
                t2 += int(tiers.get("T2", 0))
        except Exception:
            pass
    total = t0 + t1 + t2
    return {
        "t0_count": t0,
        "t1_count": t1,
        "t2_count": t2,
        "t2_ratio": round(t2 / total, 2) if total > 0 else 0.0,
        "source_quality_score": _score_source_quality(t0, t1, t2, citation_count=0),
    }


def _score_source_quality(t0: int, t1: int, t2: int, citation_count: int = 0, relevance_ratio: float = 0.5) -> float:
    """Score source quality with calibrated weights.
    
    Weights calibrated against real-world evaluation data:
    - T0=10 (primary sources), T1=7 (authoritative), T2=5 (community)
    - Volume bonus caps at 20 sources (not 15)
    - T2 ratio > 50% triggers a mild penalty
    - Citation rate ensures only actually-used sources count
    - Relevance ratio penalizes collecting many unused sources
    """
    total = t0 + t1 + t2
    if total == 0:
        return 5.0
    # Citation rate
    citation_rate = min(citation_count / max(total, 1), 1.0) if citation_count > 0 else 0.5
    # Tier composition: T0=10, T1=7, T2=5
    tier_score = (t0 * 10 + t1 * 7 + t2 * 5) / total
    # Volume bonus: more sources is better, cap at 20
    volume_bonus = min(total / 20, 1.0) * 1.0
    # Diversity bonus
    diversity = 0.0
    if t0 > 0: diversity += 0.2
    if t1 > 0: diversity += 0.2
    if t2 > 0: diversity += 0.2
    # T2 penalty: if T2 dominates, reduce score
    t2_ratio = t2 / total if total > 0 else 0
    t2_penalty = 0.85 if t2_ratio > 0.5 else 1.0
    # Combine
    raw_score = (tier_score * citation_rate + volume_bonus + diversity) * t2_penalty
    # Relevance ratio: if many sources are collected but few cited, reduce score
    if relevance_ratio < 0.3:
        raw_score *= 0.5  # heavy penalty for low relevance
    elif relevance_ratio < 0.5:
        raw_score *= 0.7
    return round(min(raw_score, 10), 1)
def _score_auditability(citation_count: int, citation_accuracy: float, full_text_ratio: float, partial_ratio: float = 0.0) -> float:
    """Score auditability: higher citations + accuracy + content depth."""
    if citation_count == 0:
        return 3.0
    citation_score = min(citation_count / 10, 1.0) * 5
    accuracy_score = citation_accuracy * 3
    full_text_score = full_text_ratio * 1.5
    partial_score = partial_ratio * 0.5
    return round(min(citation_score + accuracy_score + full_text_score + partial_score, 10), 1)


def run_quality_eval(
    question: str,
    report_type: str = "detailed_report",
    source_mode: str = "mock",
    skill_name: str | None = None,
    retrieval_profile: str = "evaluation",
    llm_client=None,
) -> ResearchQualityReport:
    """Execute one research question and score the output."""

    init_db()
    register_default_tools()
    init_skill_registry(ROOT / "workspace" / "skills")

    with SessionLocal() as db:
        run = store.create_agent_run(
            db=db,
            task=question,
            report_type=report_type,
            source_mode=source_mode,
        )
        plan = plan_task(
            question,
            allowed_tools=None,
            source_mode=source_mode,
            skill_name=skill_name,
            retrieval_profile=retrieval_profile,
        )
        store.update_agent_run_plan(db, run.run_id, plan)
        summary = run_plan(db, run.run_id)

        final_run = store.get_agent_run(db, run.run_id)
        traces = store.list_tool_traces(db, run.run_id)

        report_text = ""
        if final_run and final_run.report_path:
            rp = ROOT / final_run.report_path
            if rp.is_file():
                report_text = rp.read_text(encoding="utf-8")

        # ── Deterministic metrics from run data ─────────────────────
        tier = _compute_tier_metrics(traces)

        citation_total = getattr(final_run, "citation_total", 0) if final_run else 0
        citation_accuracy = getattr(final_run, "citation_accuracy", 0.0) if final_run else 0.0
        verified = getattr(final_run, "citation_supported", 0) if final_run else 0
        unsupported = getattr(final_run, "citation_unsupported", 0) if final_run else 0

        # Content basis from trace metadata
        cb = _compute_content_basis(traces)
        full_text_count = cb["full_text"]
        partial_count = cb["partial"]
        snippet_count = cb["snippet_only"]
        total_content = full_text_count + partial_count + snippet_count
        full_text_ratio = round(full_text_count / total_content, 2) if total_content > 0 else 0.0
        partial_ratio = round(partial_count / total_content, 2) if total_content > 0 else 0.0

        # ── Source relevance ratio ─────────────────────────────────
        total_sources = _count_total_sources_from_traces(traces)
        cited_sources = _count_cited_sources_from_report(report_text)
        source_relevance_ratio = _compute_source_relevance_ratio(total_sources, cited_sources)

        # ── LLM judge metrics ──────────────────────────────────────
        judge_result = judge_report(question, report_text, llm_client)

        # ── Build report ───────────────────────────────────────────
        quality = ResearchQualityReport(
            run_id=run.run_id,
            question=question,
            relevance_score=float(judge_result.get("relevance_score", 6)),
            relevance_rationale=str(judge_result.get("relevance_rationale", "")),
            factual_accuracy=round(verified / citation_total, 2) if citation_total > 0 else 0.0,
            verified_claims=verified,
            unsupported_claims=unsupported,
            coverage_score=float(judge_result.get("coverage_score", 6)),
            covered_dimensions=list(judge_result.get("covered_dimensions", [])),
            missing_dimensions=list(judge_result.get("missing_dimensions", [])),
            source_quality_score=_score_source_quality(tier["t0_count"], tier["t1_count"], tier["t2_count"], citation_count=citation_total, relevance_ratio=source_relevance_ratio),
            t0_count=tier["t0_count"],
            t1_count=tier["t1_count"],
            t2_count=tier["t2_count"],
            t2_ratio=tier["t2_ratio"],
            auditability_score=_score_auditability(citation_total, citation_accuracy, full_text_ratio, partial_ratio),
            citation_count=citation_total,
            citation_accuracy=citation_accuracy,
            content_basis_full_text_ratio=full_text_ratio,
            source_relevance_ratio=source_relevance_ratio,
        )
        quality.compute_overall()

    return quality


def _compute_content_basis(traces: list) -> dict[str, int]:
    """Count content_basis from trace metadata."""
    full_text = 0
    partial = 0
    snippet_only = 0
    for t in traces:
        try:
            out = json.loads(t.output_json or "{}")
            pages = out.get("output", {}).get("pages", [])
            if isinstance(pages, list):
                for page in pages:
                    if isinstance(page, dict):
                        cb = page.get("content_basis", "")
                        if cb == "full_text":
                            full_text += 1
                        elif cb == "partial":
                            partial += 1
                        elif cb == "snippet_only":
                            snippet_only += 1
        except Exception:
            pass
    return {"full_text": full_text, "partial": partial, "snippet_only": snippet_only}


def _count_total_sources_from_traces(traces: list) -> int:
    """Count unique source documents from trace metadata."""
    source_refs: set[str] = set()
    for t in traces:
        try:
            out = json.loads(t.output_json or "{}")
            items = out.get("output", {}).get("items", [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        ref = item.get("source_ref") or item.get("url") or ""
                        if ref:
                            source_refs.add(ref)
        except Exception:
            pass
    return len(source_refs)


def _count_cited_sources_from_report(report_text: str) -> int:
    """Count unique CIT-XXX references in the report."""
    if not report_text:
        return 0
    return len(set(re.findall(r"CIT-\d+-\d+", report_text)))


def _compute_source_relevance_ratio(total_sources: int, cited_sources: int) -> float:
    """Ratio of cited sources to total sources."""
    if total_sources == 0:
        return 0.0
    return round(cited_sources / total_sources, 2)


def run_dataset(
    dataset_name: str,
    source_mode: str = "mock",
    llm_client=None,
) -> QualityEvalSummary:
    """Run all questions in a dataset and return aggregate summary."""
    questions = load_dataset(dataset_name)
    reports: list[ResearchQualityReport] = []

    for item in questions:
        skill = item.get("skill")
        profile = item.get("retrieval_profile", "evaluation")
        report = run_quality_eval(
            question=item["question"],
            report_type=item.get("report_type", "detailed_report"),
            source_mode=source_mode,
            skill_name=skill,
            retrieval_profile=profile,
            llm_client=llm_client,
        )
        reports.append(report)

    if not reports:
        return QualityEvalSummary(
            total_questions=0, avg_overall=0.0, avg_relevance=0.0,
            avg_factual_accuracy=0.0, avg_coverage=0.0, avg_source_quality=0.0,
            avg_auditability=0.0, overall_t0_count=0, overall_t1_count=0,
            overall_t2_count=0, total_citations=0, avg_citation_accuracy=0.0,
            reports=[],
        )

    n = len(reports)
    return QualityEvalSummary(
        total_questions=n,
        avg_overall=round(sum(r.overall_score for r in reports) / n, 1),
        avg_relevance=round(sum(r.relevance_score for r in reports) / n, 1),
        avg_factual_accuracy=round(sum(r.factual_accuracy for r in reports) / n, 2),
        avg_coverage=round(sum(r.coverage_score for r in reports) / n, 1),
        avg_source_quality=round(sum(r.source_quality_score for r in reports) / n, 1),
        avg_auditability=round(sum(r.auditability_score for r in reports) / n, 1),
        avg_source_relevance_ratio=round(sum(r.source_relevance_ratio for r in reports) / n, 2) if n > 0 else 0.0,
        overall_t0_count=sum(r.t0_count for r in reports),
        overall_t1_count=sum(r.t1_count for r in reports),
        overall_t2_count=sum(r.t2_count for r in reports),
        total_citations=sum(r.citation_count for r in reports),
        avg_citation_accuracy=round(sum(r.citation_accuracy for r in reports) / n, 2) if n > 0 else 0.0,
        reports=reports,
    )