"""Structured public response models for local improvement diagnostics."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ImprovementTrendRun(BaseModel):
    run_id: str
    overall: float
    category: str | None = None
    mode: str | None = None
    citations: int = 0
    created_at: datetime | None = None


class ImprovementStatsResponse(BaseModel):
    total_runs: int = 0
    avg_overall: float = 0.0
    best_score: float = 0.0
    worst_score: float = 0.0
    latest_score: float = 0.0
    trend: list[ImprovementTrendRun] = Field(default_factory=list)


class ImprovementCategoryItem(BaseModel):
    category: str
    count: int
    avg_overall: float
    avg_factual: float
    avg_source_quality: float
    avg_auditability: float


class ImprovementCategoryResponse(BaseModel):
    categories: list[ImprovementCategoryItem] = Field(default_factory=list)


class ImprovementStrategyItem(BaseModel):
    skill: str
    mode: str
    count: int
    avg_overall: float


class ImprovementStrategyResponse(BaseModel):
    strategies: list[ImprovementStrategyItem] = Field(default_factory=list)


class ImprovementDailyPoint(BaseModel):
    date: str
    avg_score: float
    count: int


class ImprovementTrendResponse(BaseModel):
    trend: list[ImprovementDailyPoint] = Field(default_factory=list)
    direction: str = "insufficient_data"


class ImprovementRegressionItem(BaseModel):
    skill: str
    mode: str
    total_runs: int
    avg_score: float
    recent_avg: float
    drop: float


class ImprovementRegressionResponse(BaseModel):
    regressions: list[ImprovementRegressionItem] = Field(default_factory=list)


class ImprovementRunResponse(BaseModel):
    run_id: str
    category: str | None = None
    skill_composition: str | None = None
    execution_mode: str | None = None
    overall_score: float
    relevance_score: float
    factual_accuracy: float
    coverage_score: float
    source_quality_score: float
    auditability_score: float
    citation_count: int
    tier_t0: int
    tier_t1: int
    tier_t2: int
    created_at: datetime | None = None


class RoutingStateResponse(BaseModel):
    active: bool = False
    updated_at: datetime | None = None
    evaluated_run_count: int = 0
    category_count: int = 0
    strategy_count: int = 0


class FewShotStateResponse(BaseModel):
    count: int = 0
    max_total: int = 20
    max_per_category: int = 5
    by_category: dict[str, int] = Field(default_factory=dict)


class ImprovementStateResponse(BaseModel):
    total_evaluated_runs: int = 0
    last_evaluated_at: datetime | None = None
    routing: RoutingStateResponse = Field(default_factory=RoutingStateResponse)
    few_shot: FewShotStateResponse = Field(default_factory=FewShotStateResponse)
