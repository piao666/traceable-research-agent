"""Self-improvement API: stats, trends, category breakdowns."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.improvement.models import ImprovementLog
from app.security import require_api_key

router = APIRouter(
    prefix="/improvement",
    tags=["improvement"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/stats")
def improvement_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Overall improvement trend over the last N days."""
    rows = (
        db.execute(
            select(ImprovementLog)
            .order_by(ImprovementLog.created_at.desc())
            .limit(days * 10)  # rough cap
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"total_runs": 0, "avg_overall": 0.0, "trend": []}

    overalls = [r.overall_score for r in rows]
    return {
        "total_runs": len(rows),
        "avg_overall": round(sum(overalls) / len(overalls), 1),
        "best_score": max(overalls),
        "worst_score": min(overalls),
        "latest_score": overalls[0] if overalls else 0.0,
        "trend": [
            {
                "run_id": r.run_id,
                "overall": r.overall_score,
                "category": r.question_category,
                "mode": r.execution_mode,
                "citations": r.citation_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows[:20]
        ],
    }


@router.get("/by-category")
def improvement_by_category(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Aggregate scores by question category."""
    rows = (
        db.execute(
            select(
                ImprovementLog.question_category,
                func.count().label("cnt"),
                func.avg(ImprovementLog.overall_score).label("avg_overall"),
                func.avg(ImprovementLog.factual_accuracy).label("avg_factual"),
                func.avg(ImprovementLog.source_quality_score).label("avg_source"),
                func.avg(ImprovementLog.auditability_score).label("avg_audit"),
            )
            .group_by(ImprovementLog.question_category)
            .order_by(func.avg(ImprovementLog.overall_score).desc())
        )
        .all()
    )
    return {
        "categories": [
            {
                "category": r.question_category or "unknown",
                "count": r.cnt,
                "avg_overall": round(r.avg_overall, 1) if r.avg_overall else 0.0,
                "avg_factual": round(r.avg_factual, 2) if r.avg_factual else 0.0,
                "avg_source_quality": round(r.avg_source, 1) if r.avg_source else 0.0,
                "avg_auditability": round(r.avg_audit, 1) if r.avg_audit else 0.0,
            }
            for r in rows
        ]
    }


@router.get("/by-strategy")
def improvement_by_strategy(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Aggregate scores by skill composition / execution mode."""
    rows = (
        db.execute(
            select(
                ImprovementLog.skill_composition,
                ImprovementLog.execution_mode,
                func.count().label("cnt"),
                func.avg(ImprovementLog.overall_score).label("avg_overall"),
            )
            .group_by(
                ImprovementLog.skill_composition,
                ImprovementLog.execution_mode,
            )
            .order_by(func.avg(ImprovementLog.overall_score).desc())
        )
        .all()
    )
    return {
        "strategies": [
            {
                "skill": r.skill_composition or "unknown",
                "mode": r.execution_mode or "unknown",
                "count": r.cnt,
                "avg_overall": round(r.avg_overall, 1) if r.avg_overall else 0.0,
            }
            for r in rows
        ]
    }