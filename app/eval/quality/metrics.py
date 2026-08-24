"""Five-dimension research quality metrics.

Dataclasses for scoring research reports across:
  - relevance (LLM-as-judge)
  - factual_accuracy (deterministic, from citation validation)
  - coverage (LLM-as-judge)
  - source_quality (deterministic, from tier distribution)
  - auditability (deterministic, from citation count/accuracy)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchQualityReport:
    """Per-question research quality scoring."""

    run_id: str
    question: str

    # ── Relevance (LLM judge) ──────────────────────────────────────
    relevance_score: float = 0.0          # 0-10
    relevance_rationale: str = ""

    # ── Factual accuracy (deterministic) ───────────────────────────
    factual_accuracy: float = 0.0          # 0-1
    verified_claims: int = 0
    unsupported_claims: int = 0

    # ── Coverage (LLM judge) ───────────────────────────────────────
    coverage_score: float = 0.0            # 0-10
    covered_dimensions: list[str] = field(default_factory=list)
    missing_dimensions: list[str] = field(default_factory=list)

    # ── Source quality (deterministic) ─────────────────────────────
    source_quality_score: float = 0.0      # 0-10
    t0_count: int = 0
    t1_count: int = 0
    t2_count: int = 0
    t2_ratio: float = 0.0
    source_relevance_ratio: float = 0.0    # cited sources / total sources

    # ── Auditability (deterministic) ───────────────────────────────
    auditability_score: float = 0.0        # 0-10
    citation_count: int = 0
    citation_accuracy: float = 0.0
    content_basis_full_text_ratio: float = 0.0

    # ── Composite ──────────────────────────────────────────────────
    overall_score: float = 0.0             # 0-10 weighted

    def compute_overall(self) -> float:
        """Weighted composite: deterministic metrics carry more weight."""
        self.overall_score = round(
            self.relevance_score * 0.20
            + self.factual_accuracy * 10 * 0.25
            + self.coverage_score * 0.20
            + self.source_quality_score * 0.15
            + self.auditability_score * 0.20,
            1,
        )
        return self.overall_score

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "relevance_score": self.relevance_score,
            "relevance_rationale": self.relevance_rationale,
            "factual_accuracy": self.factual_accuracy,
            "verified_claims": self.verified_claims,
            "unsupported_claims": self.unsupported_claims,
            "coverage_score": self.coverage_score,
            "covered_dimensions": self.covered_dimensions,
            "missing_dimensions": self.missing_dimensions,
            "source_quality_score": self.source_quality_score,
            "t0_count": self.t0_count,
            "t1_count": self.t1_count,
            "t2_count": self.t2_count,
            "t2_ratio": self.t2_ratio,
            "source_relevance_ratio": self.source_relevance_ratio,
            "auditability_score": self.auditability_score,
            "citation_count": self.citation_count,
            "citation_accuracy": self.citation_accuracy,
            "content_basis_full_text_ratio": self.content_basis_full_text_ratio,
            "overall_score": self.overall_score,
        }


@dataclass
class QualityEvalSummary:
    """Aggregate summary across all questions in a dataset."""

    total_questions: int
    avg_overall: float
    avg_relevance: float
    avg_factual_accuracy: float
    avg_coverage: float
    avg_source_quality: float
    avg_auditability: float
    overall_t0_count: int
    overall_t1_count: int
    overall_t2_count: int
    total_citations: int
    avg_citation_accuracy: float
    avg_source_relevance_ratio: float = 0.0
    reports: list[ResearchQualityReport] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_questions": self.total_questions,
            "avg_overall": self.avg_overall,
            "avg_relevance": self.avg_relevance,
            "avg_factual_accuracy": self.avg_factual_accuracy,
            "avg_coverage": self.avg_coverage,
            "avg_source_quality": self.avg_source_quality,
            "avg_auditability": self.avg_auditability,
            "avg_source_relevance_ratio": self.avg_source_relevance_ratio,
            "overall_t0_count": self.overall_t0_count,
            "overall_t1_count": self.overall_t1_count,
            "overall_t2_count": self.overall_t2_count,
            "total_citations": self.total_citations,
            "avg_citation_accuracy": self.avg_citation_accuracy,
            "reports": [r.to_dict() for r in self.reports],
        }