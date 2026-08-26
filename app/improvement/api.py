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


@router.get("/trend")
def improvement_trend(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Score trend over time (daily buckets)."""
    rows = (
        db.execute(
            select(ImprovementLog)
            .order_by(ImprovementLog.created_at.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"trend": [], "direction": "flat"}

    # Group by day
    from collections import defaultdict
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.created_at:
            day = r.created_at.strftime("%Y-%m-%d")
            buckets[day].append(r.overall_score)

    trend = [
        {"date": day, "avg_score": round(sum(scores) / len(scores), 1), "count": len(scores)}
        for day, scores in sorted(buckets.items())
    ]

    # Direction: compare first half vs second half
    half = len(trend) // 2
    if half >= 2:
        first_half = sum(d["avg_score"] for d in trend[:half]) / half
        second_half = sum(d["avg_score"] for d in trend[half:]) / (len(trend) - half)
        if second_half - first_half > 0.3:
            direction = "improving"
        elif first_half - second_half > 0.3:
            direction = "declining"
        else:
            direction = "stable"
    else:
        direction = "insufficient_data"

    return {"trend": trend, "direction": direction}


@router.get("/regressions")
def improvement_regressions(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Detect regressing strategies (declining scores)."""
    rows = (
        db.execute(
            select(
                ImprovementLog.skill_composition,
                ImprovementLog.execution_mode,
                func.count().label("cnt"),
                func.avg(ImprovementLog.overall_score).label("avg_score"),
                func.min(ImprovementLog.created_at).label("first_run"),
                func.max(ImprovementLog.created_at).label("last_run"),
            )
            .group_by(
                ImprovementLog.skill_composition,
                ImprovementLog.execution_mode,
            )
            .having(func.count() >= 3)
        )
        .all()
    )

    regressions = []
    for r in rows:
        # Get recent scores for this strategy
        recent = (
            db.execute(
                select(ImprovementLog.overall_score)
                .where(
                    ImprovementLog.skill_composition == r.skill_composition,
                    ImprovementLog.execution_mode == r.execution_mode,
                )
                .order_by(ImprovementLog.created_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        if len(recent) >= 3:
            # Compare most recent vs oldest in the window
            recent_avg = sum(recent[:3]) / 3
            older_avg = sum(recent[-3:]) / 3
            if older_avg - recent_avg > 0.5:
                regressions.append({
                    "skill": r.skill_composition or "unknown",
                    "mode": r.execution_mode or "unknown",
                    "total_runs": r.cnt,
                    "avg_score": round(r.avg_score, 1) if r.avg_score else 0.0,
                    "recent_avg": round(recent_avg, 1),
                    "drop": round(older_avg - recent_avg, 1),
                })

    regressions.sort(key=lambda x: -x["drop"])
    return {"regressions": regressions}