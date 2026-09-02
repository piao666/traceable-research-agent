"""Self-improvement API: final-run scores, trends, and local loop state."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.improvement.few_shot import LIBRARY_PATH, _MAX_PER_CATEGORY, _MAX_TOTAL
from app.improvement.models import ImprovementLog
from app.agent.outcome import trusted_run_ids
from app.improvement.schemas import (
    ImprovementCategoryResponse,
    ImprovementRegressionResponse,
    ImprovementRunResponse,
    ImprovementStateResponse,
    ImprovementStatsResponse,
    ImprovementStrategyResponse,
    ImprovementTrendResponse,
)
from app.improvement.weight_updater import WEIGHTS_PATH
from app.security import require_api_key


router = APIRouter(
    prefix="/improvement",
    tags=["improvement"],
    dependencies=[Depends(require_api_key)],
)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _read_json(path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("/stats", response_model=ImprovementStatsResponse)
def improvement_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ImprovementStatsResponse:
    """Return final-run score statistics inside the requested date window."""
    rows = (
        db.execute(
            select(ImprovementLog)
            .where(ImprovementLog.created_at >= _cutoff(days), ImprovementLog.run_id.in_(trusted_run_ids()))
            .order_by(ImprovementLog.created_at.desc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return ImprovementStatsResponse()

    overalls = [float(row.overall_score) for row in rows]
    return ImprovementStatsResponse(
        total_runs=len(rows),
        avg_overall=round(sum(overalls) / len(overalls), 1),
        best_score=max(overalls),
        worst_score=min(overalls),
        latest_score=overalls[0],
        trend=[
            {
                "run_id": row.run_id,
                "overall": row.overall_score,
                "category": row.question_category,
                "mode": row.execution_mode,
                "citations": row.citation_count,
                "created_at": row.created_at,
            }
            for row in rows[:20]
        ],
    )


@router.get("/by-category", response_model=ImprovementCategoryResponse)
def improvement_by_category(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ImprovementCategoryResponse:
    """Aggregate final-run scores by question category in the date window."""
    rows = db.execute(
        select(
            ImprovementLog.question_category,
            func.count().label("cnt"),
            func.avg(ImprovementLog.overall_score).label("avg_overall"),
            func.avg(ImprovementLog.factual_accuracy).label("avg_factual"),
            func.avg(ImprovementLog.source_quality_score).label("avg_source"),
            func.avg(ImprovementLog.auditability_score).label("avg_audit"),
        )
        .where(ImprovementLog.created_at >= _cutoff(days), ImprovementLog.run_id.in_(trusted_run_ids()))
        .group_by(ImprovementLog.question_category)
        .order_by(func.avg(ImprovementLog.overall_score).desc())
    ).all()
    return ImprovementCategoryResponse(
        categories=[
            {
                "category": row.question_category or "unknown",
                "count": row.cnt,
                "avg_overall": round(row.avg_overall, 1) if row.avg_overall else 0.0,
                "avg_factual": round(row.avg_factual, 2) if row.avg_factual else 0.0,
                "avg_source_quality": round(row.avg_source, 1) if row.avg_source else 0.0,
                "avg_auditability": round(row.avg_audit, 1) if row.avg_audit else 0.0,
            }
            for row in rows
        ]
    )


@router.get("/by-strategy", response_model=ImprovementStrategyResponse)
def improvement_by_strategy(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ImprovementStrategyResponse:
    """Aggregate final-run scores by skill composition and execution mode."""
    rows = db.execute(
        select(
            ImprovementLog.skill_composition,
            ImprovementLog.execution_mode,
            func.count().label("cnt"),
            func.avg(ImprovementLog.overall_score).label("avg_overall"),
        )
        .where(ImprovementLog.created_at >= _cutoff(days), ImprovementLog.run_id.in_(trusted_run_ids()))
        .group_by(ImprovementLog.skill_composition, ImprovementLog.execution_mode)
        .order_by(func.avg(ImprovementLog.overall_score).desc())
    ).all()
    return ImprovementStrategyResponse(
        strategies=[
            {
                "skill": row.skill_composition or "unknown",
                "mode": row.execution_mode or "unknown",
                "count": row.cnt,
                "avg_overall": round(row.avg_overall, 1) if row.avg_overall else 0.0,
            }
            for row in rows
        ]
    )


@router.get("/trend", response_model=ImprovementTrendResponse)
def improvement_trend(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ImprovementTrendResponse:
    """Return daily final-run score buckets inside the date window."""
    rows = (
        db.execute(
            select(ImprovementLog)
            .where(ImprovementLog.created_at >= _cutoff(days), ImprovementLog.run_id.in_(trusted_run_ids()))
            .order_by(ImprovementLog.created_at.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return ImprovementTrendResponse()

    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.created_at:
            buckets[row.created_at.strftime("%Y-%m-%d")].append(row.overall_score)
    trend = [
        {"date": day, "avg_score": round(sum(scores) / len(scores), 1), "count": len(scores)}
        for day, scores in sorted(buckets.items())
    ]

    half = len(trend) // 2
    if half >= 2:
        first_half = sum(point["avg_score"] for point in trend[:half]) / half
        second_half = sum(point["avg_score"] for point in trend[half:]) / (len(trend) - half)
        if second_half - first_half > 0.3:
            direction = "improving"
        elif first_half - second_half > 0.3:
            direction = "declining"
        else:
            direction = "stable"
    else:
        direction = "insufficient_data"
    return ImprovementTrendResponse(trend=trend, direction=direction)


@router.get("/regressions", response_model=ImprovementRegressionResponse)
def improvement_regressions(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> ImprovementRegressionResponse:
    """Detect strategies whose newest three runs trail the prior three."""
    cutoff = _cutoff(days)
    rows = db.execute(
        select(
            ImprovementLog.skill_composition,
            ImprovementLog.execution_mode,
            func.count().label("cnt"),
            func.avg(ImprovementLog.overall_score).label("avg_score"),
        )
        .where(ImprovementLog.created_at >= cutoff, ImprovementLog.run_id.in_(trusted_run_ids()))
        .group_by(ImprovementLog.skill_composition, ImprovementLog.execution_mode)
        .having(func.count() >= 6)
    ).all()

    regressions: list[dict] = []
    for row in rows:
        recent = (
            db.execute(
                select(ImprovementLog.overall_score)
                .where(
                    ImprovementLog.created_at >= cutoff,
                    ImprovementLog.run_id.in_(trusted_run_ids()),
                    ImprovementLog.skill_composition == row.skill_composition,
                    ImprovementLog.execution_mode == row.execution_mode,
                )
                .order_by(ImprovementLog.created_at.desc())
                .limit(6)
            )
            .scalars()
            .all()
        )
        if len(recent) < 6:
            continue
        recent_avg = sum(recent[:3]) / 3
        older_avg = sum(recent[3:6]) / 3
        if older_avg - recent_avg > 0.5:
            regressions.append(
                {
                    "skill": row.skill_composition or "unknown",
                    "mode": row.execution_mode or "unknown",
                    "total_runs": row.cnt,
                    "avg_score": round(row.avg_score, 1) if row.avg_score else 0.0,
                    "recent_avg": round(recent_avg, 1),
                    "drop": round(older_avg - recent_avg, 1),
                }
            )
    regressions.sort(key=lambda item: -item["drop"])
    return ImprovementRegressionResponse(regressions=regressions)


@router.get("/runs/{run_id}", response_model=ImprovementRunResponse)
def improvement_run(run_id: str, db: Session = Depends(get_db)) -> ImprovementRunResponse:
    """Return the five-dimensional final evaluation for one run."""
    row = db.get(ImprovementLog, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Improvement evaluation not found")
    return ImprovementRunResponse(
        requires_review=db.scalar(select(ImprovementLog.run_id).where(
            ImprovementLog.run_id == run_id, ImprovementLog.run_id.in_(trusted_run_ids()),
        )) is None,
        run_id=row.run_id,
        category=row.question_category,
        skill_composition=row.skill_composition,
        execution_mode=row.execution_mode,
        overall_score=row.overall_score,
        relevance_score=row.relevance_score,
        factual_accuracy=row.factual_accuracy,
        coverage_score=row.coverage_score,
        source_quality_score=row.source_quality_score,
        auditability_score=row.auditability_score,
        citation_count=row.citation_count,
        tier_t0=row.tier_t0,
        tier_t1=row.tier_t1,
        tier_t2=row.tier_t2,
        created_at=row.created_at,
    )


@router.get("/state", response_model=ImprovementStateResponse)
def improvement_state(db: Session = Depends(get_db)) -> ImprovementStateResponse:
    """Return local routing and Few-shot cold-start diagnostics."""
    total = db.execute(select(func.count()).select_from(ImprovementLog).where(ImprovementLog.run_id.in_(trusted_run_ids()))).scalar() or 0
    latest = (
        db.execute(select(ImprovementLog).where(ImprovementLog.run_id.in_(trusted_run_ids())).order_by(ImprovementLog.created_at.desc()).limit(1))
        .scalars()
        .first()
    )

    weights_payload = _read_json(WEIGHTS_PATH)
    from app.agent.outcome import INTEGRITY_VERSION
    if weights_payload.get("integrity_version") != INTEGRITY_VERSION:
        weights_payload = {}
    weights = weights_payload.get("weights") if isinstance(weights_payload.get("weights"), dict) else {}
    strategy_count = sum(len(value) for value in weights.values() if isinstance(value, dict))

    library_payload = _read_json(LIBRARY_PATH)
    examples = library_payload.get("examples") if isinstance(library_payload.get("examples"), list) else []
    by_category: dict[str, int] = {}
    for example in examples:
        if not isinstance(example, dict):
            continue
        category = str(example.get("category") or "general")
        by_category[category] = by_category.get(category, 0) + 1

    return ImprovementStateResponse(
        total_evaluated_runs=int(total),
        last_evaluated_at=latest.created_at if latest else None,
        routing={
            "active": bool(weights),
            "updated_at": weights_payload.get("updated_at"),
            "evaluated_run_count": int(weights_payload.get("total_runs") or 0),
            "category_count": len(weights),
            "strategy_count": strategy_count,
        },
        few_shot={
            "count": len(examples),
            "max_total": _MAX_TOTAL,
            "max_per_category": _MAX_PER_CATEGORY,
            "by_category": by_category,
        },
    )
