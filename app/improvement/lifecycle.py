"""Run the local improvement loop after the final task result is stable."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.trace import store
from app.trace.logger import record_trace_event


logger = logging.getLogger(__name__)


def finalize_improvement_cycle(db: Session, run_id: str) -> Any | None:
    """Evaluate one final completed run without changing its terminal result.

    Evaluation is intentionally dispatched from the execution router instead
    of an individual executor so planned, parallel, ReAct, adaptive, and deep
    research runs all use the same final-result boundary.
    """

    run = store.get_fresh_agent_run(db, run_id)
    if run is None or run.status != "completed":
        return None

    errors: list[str] = []
    log_entry = None
    weights_updated = False
    promoted = False

    try:
        from app.improvement.evaluator import auto_evaluate_and_log

        log_entry = auto_evaluate_and_log(db, run_id)
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        logger.exception("Improvement evaluation failed for run %s", run_id)
        errors.append(f"evaluation:{type(exc).__name__}")

    if log_entry is not None:
        try:
            from app.improvement.weight_updater import maybe_update_weights

            weights_updated = maybe_update_weights()
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            logger.exception("Routing weight update failed for run %s", run_id)
            errors.append(f"routing_weights:{type(exc).__name__}")

        try:
            from app.improvement.few_shot import promote_to_few_shot

            promoted = promote_to_few_shot(run_id)
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            logger.exception("Few-shot promotion failed for run %s", run_id)
            errors.append(f"few_shot:{type(exc).__name__}")

    if log_entry is None and not errors:
        errors.append("evaluation:no_result")

    try:
        record_trace_event(
            db=db,
            run_id=run_id,
            step_no=max(run.current_step, 0) + 1,
            tool_name="improvement_evaluation",
            status="failed" if errors else "success",
            input_data={"final_status": run.status},
            output_summary=(
                "Improvement cycle incomplete: " + ", ".join(errors)
                if errors
                else f"Final run evaluated: overall={log_entry.overall_score:.1f}."
            ),
            output_data={
                "overall_score": getattr(log_entry, "overall_score", None),
                "weights_updated": weights_updated,
                "few_shot_promoted": promoted,
                "errors": errors,
            },
            error_message=", ".join(errors) if errors else None,
        )
    except Exception:  # pragma: no cover - trace failure must not alter completion
        logger.exception("Improvement lifecycle trace failed for run %s", run_id)

    return log_entry
