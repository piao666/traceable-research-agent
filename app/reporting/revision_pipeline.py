"""Bounded report draft validation and revision orchestration.

Callers inject their existing budgeted LLM and persistence hooks.  This module
does not fetch evidence, mutate terminal state, or turn quality failures into
provider failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.budget import BudgetExceeded
from app.security.redaction import redact_text


class ReportGenerationCancelled(Exception):
    """Raised by the caller between attempts; never converted to a retry."""


@dataclass(frozen=True)
class ReportBuildResult:
    markdown: str | None
    answer_body: str | None
    revision_id: str | None
    validation: Any = None
    integrity: str = "incomplete"
    diagnostics: tuple[dict[str, Any], ...] = ()
    adopted: bool = False


def generate_validate_revise(
    context: dict[str, Any],
    budgeted_client: Callable[[dict[str, Any]], str],
    persist_attempt: Callable[[int, str | None, dict[str, Any]], str | None],
    check_cancelled: Callable[[], None],
    *,
    validate: Callable[[str, dict[str, Any]], Any],
    is_acceptable: Callable[[Any], bool],
    revision_feedback: Callable[[Any], dict[str, Any]],
    max_revisions: int = 2,
) -> ReportBuildResult:
    """Run one draft plus a finite number of feedback-only revisions.

    Cancellation and ``BudgetExceeded`` deliberately escape: neither permits a
    new model call.  Every candidate, including malformed output, is persisted
    before a possible next attempt.
    """
    diagnostics: list[dict[str, Any]] = []
    request = dict(context)
    last_markdown: str | None = None
    last_validation: Any = None
    last_id: str | None = None
    revision_limit = min(2, max(0, int(max_revisions)))
    for attempt in range(revision_limit + 1):
        check_cancelled()
        try:
            markdown = budgeted_client(request)
        except (BudgetExceeded, ReportGenerationCancelled):
            raise
        except Exception as exc:
            diagnostics.append({"attempt": attempt, "code": "provider_or_execution_error", "detail": redact_text(exc)[:300]})
            last_id = persist_attempt(attempt, None, diagnostics[-1])
            return ReportBuildResult(None, None, last_id, integrity="failed", diagnostics=tuple(diagnostics))
        if not isinstance(markdown, str) or not markdown.strip():
            diagnostic = {"attempt": attempt, "code": "malformed_report", "detail": "Empty or non-text report candidate."}
            diagnostics.append(diagnostic)
            last_id = persist_attempt(attempt, None, diagnostic)
            if attempt < revision_limit:
                request = {**context, "revision_feedback": diagnostic, "previous_draft": ""}
                continue
            return ReportBuildResult(None, None, last_id, integrity="incomplete", diagnostics=tuple(diagnostics))
        previous_markdown = last_markdown
        last_markdown = markdown
        # The candidate is an immutable audit artifact before validation.  A
        # validation crash must never make an already returned model draft
        # disappear from the attempt history.
        last_id = persist_attempt(attempt, markdown, {
            "attempt": attempt,
            "code": "candidate_retained",
            "content_present": True,
        })
        check_cancelled()
        try:
            last_validation = validate(markdown, context)
        except (BudgetExceeded, ReportGenerationCancelled):
            raise
        except Exception as exc:
            diagnostic = {"attempt": attempt, "code": "validation_error", "detail": redact_text(exc)[:300]}
            diagnostics.append(diagnostic)
            # A validator crash is not feedback that a writer can repair.
            # Stop before another billable synthesis call and let the caller
            # classify it as an execution/validator failure rather than an
            # evidence-quality gap.
            return ReportBuildResult(markdown, markdown, last_id, integrity="failed", diagnostics=tuple(diagnostics))
        accepted = bool(is_acceptable(last_validation))
        diagnostic = {"attempt": attempt, "code": "accepted" if accepted else "report_integrity_failed", "validation": _safe_validation(last_validation)}
        diagnostics.append(diagnostic)
        if accepted:
            return ReportBuildResult(markdown, markdown, last_id, last_validation, "passed", tuple(diagnostics), True)
        if previous_markdown is not None and markdown == previous_markdown:
            # Revalidate first: a governed evidence-window refresh can change
            # support without changing the draft. Stop only if it still fails.
            diagnostics.append({"attempt": attempt, "code": "repeated_report_draft"})
            return ReportBuildResult(markdown, markdown, last_id, last_validation,
                                     "incomplete", tuple(diagnostics), False)
        if attempt == revision_limit:
            break
        request = {**context, "revision_feedback": revision_feedback(last_validation), "previous_draft": markdown}
    return ReportBuildResult(last_markdown, last_markdown, last_id, last_validation, "incomplete", tuple(diagnostics), False)


def _safe_validation(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): value[key] for key in ("supported_occurrences", "weakly_supported_occurrences", "unsupported_occurrences") if key in value}
    return {
        key: getattr(value, key)
        for key in ("supported_occurrences", "weakly_supported_occurrences", "unsupported_occurrences")
        if hasattr(value, key)
    }
