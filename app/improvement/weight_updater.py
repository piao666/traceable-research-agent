"""Update routing weights from improvement_log data.

Reads the improvement_logs table, groups runs by question_category ×
skill_composition, computes average scores, and writes normalized
weights to workspace/improvement/routing_weights.json.

Weights are used by routing.py to bias keyword scoring toward
historically effective strategies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.database import SessionLocal
from app.improvement.models import ImprovementLog

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = ROOT / "workspace" / "improvement" / "routing_weights.json"

# Minimum runs per (category, strategy) before weight is considered reliable
_MIN_RUNS_FOR_WEIGHT = 3
# Weight decay: older runs contribute less
_DECAY_DAYS = 90
# Default weight for cold-start categories
_DEFAULT_WEIGHT = 0.5


def _parse_skill_name(skill_composition: str | None) -> str:
    """Normalize skill_composition to a canonical skill name."""
    if not skill_composition:
        return "unknown"
    # skill_composition is either a single skill name or a JSON array
    if skill_composition.startswith("["):
        try:
            names = json.loads(skill_composition)
            if isinstance(names, list) and names:
                return "+".join(sorted(names))
        except (json.JSONDecodeError, TypeError):
            pass
    return skill_composition


def update_routing_weights() -> dict:
    """Read improvement_log, compute weights, write to file. Returns the new weights dict."""
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(
                    ImprovementLog.question_category,
                    ImprovementLog.skill_composition,
                    func.count().label("cnt"),
                    func.avg(ImprovementLog.overall_score).label("avg_score"),
                    func.max(ImprovementLog.created_at).label("last_run"),
                )
                .group_by(
                    ImprovementLog.question_category,
                    ImprovementLog.skill_composition,
                )
                .having(func.count() >= _MIN_RUNS_FOR_WEIGHT)
            )
            .all()
        )

    weights: dict[str, dict[str, float]] = {}
    for row in rows:
        category = row.question_category or "general"
        skill_name = _parse_skill_name(row.skill_composition)
        avg_score = float(row.avg_score) if row.avg_score else _DEFAULT_WEIGHT * 10

        # Time decay: weight drops as last run ages
        if row.last_run:
            days_ago = (datetime.now(timezone.utc) - row.last_run).days
            decay = max(0.3, 1.0 - days_ago / _DECAY_DAYS)
        else:
            decay = 1.0

        weight = round((avg_score / 10) * decay, 2)
        weight = max(0.1, min(weight, 1.0))  # clamp

        weights.setdefault(category, {})[skill_name] = weight

    total_runs = _count_total_runs()
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": total_runs,
        "weights": weights,
    }

    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Routing weights updated: %d categories, %d total runs",
        len(weights), total_runs,
    )
    return payload


def _count_total_runs() -> int:
    with SessionLocal() as db:
        result = db.execute(
            select(func.count()).select_from(ImprovementLog)
        ).scalar()
        return int(result) if result else 0


def load_routing_weights() -> dict:
    """Load current weights from file. Returns empty dict if file missing."""
    if not WEIGHTS_PATH.is_file():
        return {}
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        return data.get("weights", {})
    except (json.JSONDecodeError, OSError):
        return {}


def maybe_update_weights(force: bool = False) -> bool:
    """Update weights if enough new data has accumulated (or force=True)."""
    if force:
        update_routing_weights()
        return True
    count = _count_total_runs()
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8")) if WEIGHTS_PATH.is_file() else {}
        last_count = data.get("total_runs", 0)
    except Exception:
        last_count = 0
    if count - last_count >= 10:  # trigger every 10 new runs
        update_routing_weights()
        return True
    return False