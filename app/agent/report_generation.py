"""Resolve report synthesis dependencies from explicit runtime policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.llm.base import LLMClient, LLMResponse
from app.llm.cost import estimate_cost
from app.llm.errors import LLM_ERROR_TYPES
from app.llm.providers import create_llm_client
from app.security.redaction import redact_text
from app.trace.logger import record_trace_event
from app.reporting.revision_pipeline import ReportGenerationCancelled


@dataclass
class ReportGenerationAudit:
    """Shared, append-only audit hooks for all report-generation entries.

    Candidate bytes are deliberately written before validation.  The caller
    may later select a candidate, but it never rewrites or discards one.
    """

    db: Any
    run_id: str
    traces: list[Any]
    check_cancelled: Callable[[], None] = lambda: None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    responses: list[LLMResponse] = field(default_factory=list)

    def __post_init__(self) -> None:
        for trace in self.traces:
            if trace.tool_name == "report_revision_attempt":
                entry = json.loads(trace.output_json or "{}")
                if isinstance(entry, dict) and "attempt" in entry:
                    self.attempts.append(entry)
        self._attempt_base = max((int(a["attempt"]) for a in self.attempts), default=-1) + 1

    def usage_callback(self, response: LLMResponse) -> None:
        self.responses.append(response)
        record_report_synthesis_trace(
            self.db, self.run_id, self.traces, response,
            success=bool(response.success and str(response.content or "").strip()
                         and not response.metadata.get("error_type")),
        )

    def persist_attempt(
        self, attempt: int, text: str | None, diagnostic: dict[str, Any]
    ) -> str | None:
        # Audit artifacts are durable; do not persist an LLM draft verbatim
        # because it may echo credentials or private source material.  The
        # caller continues to validate the original in-memory candidate.
        attempt += self._attempt_base
        audit_text = redact_text(text) if text is not None else None
        content_hash = (
            hashlib.sha256(audit_text.encode("utf-8")).hexdigest()
            if audit_text is not None else None
        )
        artifact_path = None
        if audit_text is not None:
            root = Path(__file__).resolve().parents[2] / "workspace" / "reports" / "audit"
            root.mkdir(parents=True, exist_ok=True)
            # Hash makes the artifact immutable/reusable; no overwrite occurs.
            path = root / f"{self.run_id}-attempt-{int(attempt)}-{content_hash}.md"
            if not path.exists():
                path.write_text(audit_text, encoding="utf-8", newline="\n")
            artifact_path = str(path)
        entry = {
            "attempt": int(attempt),
            "content_sha256": content_hash,
            "artifact_path": artifact_path,
            "diagnostic": dict(diagnostic),
        }
        self.attempts.append(entry)
        record_trace_event(
            db=self.db, run_id=self.run_id,
            step_no=max((trace.step_no for trace in self.traces), default=0) + len(self.attempts),
            tool_name="report_revision_attempt",
            status="success" if text is not None else "failed",
            input_data={"attempt": int(attempt)},
            output_summary="Report draft retained for local audit.",
            output_data={"attempt": int(attempt), "content_sha256": content_hash,
                         "artifact_path": artifact_path, "diagnostic": dict(diagnostic)},
        )
        return f"attempt-{attempt}-{content_hash}" if content_hash else f"attempt-{attempt}"

    def persist_validation(self, attempt: int, candidate_id: str | None, diagnostic: dict[str, Any]) -> dict[str, Any]:
        from app.evidence.decision_audit import retain_decision
        attempt += self._attempt_base
        reference = retain_decision("report_revision_decision",
            {"attempt": attempt, "candidate_id": candidate_id}, diagnostic,
            db=self.db, run_id=self.run_id)
        for entry in self.attempts:
            if entry["attempt"] == attempt:
                entry["decision"] = reference
                entry["outcome"] = diagnostic.get("code")
        return reference

    def manifest(self) -> dict[str, Any]:
        payload = {"version": "report-generation-audit-v1", "attempts": self.attempts}
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # sha256 is explicit because this crosses the A0/A5 persisted contract.
        # Keep the older spelling as a read-compatible alias for in-flight runs.
        return {**payload, "manifest_sha256": digest, "manifest_hash": digest}


def load_resume_candidate(plan: dict[str, Any]) -> str | None:
    reference = plan.get("report_resume_candidate") or {}
    if not reference.get("artifact_path"):
        return None
    root = Path(__file__).resolve().parents[2] / "workspace" / "reports" / "audit"
    path = Path(reference["artifact_path"])
    try:
        path.resolve().relative_to(root.resolve())
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference.get("content_sha256"):
            return None
        return raw.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return None


def check_report_generation_not_cancelled(db: Any, run_id: str) -> None:
    """Stop the bounded revision loop at the existing run cancellation edge."""
    from app.trace import store
    if store.is_agent_run_cancelled(db, run_id):
        raise ReportGenerationCancelled("Run cancelled before report revision.")


def resolve_report_llm_client(
    settings: Settings,
    injected_client: LLMClient | None = None,
) -> LLMClient | None:
    """Return an LLM client only when report synthesis is explicitly enabled."""

    if settings.report_generation_mode != "llm":
        return None
    from app.agent.budget import budget_client
    return budget_client(
        injected_client
        or create_llm_client(
            settings,
            settings.llm_provider,
            settings.llm_model,
        )
    )


def record_report_synthesis_trace(
    db,
    run_id: str,
    traces: list,
    response: LLMResponse,
    *,
    success: bool,
):
    """Persist one non-secret Synthesizer outcome for either executor path."""

    usage = response.usage
    error_type = str(response.metadata.get("error_type") or "").strip()
    if not success and error_type not in LLM_ERROR_TYPES:
        error_type = "malformed_response" if response.success else "provider_unavailable"
    provider = str(response.provider or "unknown")[:80]
    model = str(response.model or "")[:160] or None
    phase = str(response.metadata.get("report_phase") or "report_synthesis")
    is_adjudication = phase == "citation_adjudication"
    message = None if success else f"{'Citation adjudication' if is_adjudication else 'Report synthesis'} failed ({error_type})."
    metadata = {
        "provider": provider,
        "model": model,
        "error_type": error_type or None,
        "retryable": bool(response.metadata.get("retryable", False)),
        **{key: response.metadata[key] for key in ("admission_reason", "invalid_citation_ids")
           if key in response.metadata},
    }
    return record_trace_event(
        db=db,
        run_id=run_id,
        step_no=max((trace.step_no for trace in traces), default=0) + 1,
        tool_name="citation_adjudication" if is_adjudication else "report_synthesis",
        status="success" if success else "failed",
        input_data={"provider": provider, "model": model, "phase": phase},
        output_summary=(
            "LLM multilingual citation adjudication completed."
            if is_adjudication and success
            else "LLM report synthesis completed."
            if success
            else "LLM multilingual citation adjudication did not produce an admissible result."
            if is_adjudication
            else "LLM report synthesis did not produce an admissible report."
        ),
        output_data={"metadata": metadata, "decision_audit": response.metadata.get("decision_audit")},
        error_message=message,
        token_in=usage.prompt_tokens if usage else 0,
        token_out=usage.completion_tokens if usage else 0,
        estimated_cost=estimate_cost(provider, model, usage),
    )
