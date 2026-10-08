import hashlib

import pytest

from app.agent.budget import BudgetExceeded
from app.agent.report_generation import ReportGenerationAudit
from app.reporting.revision_pipeline import generate_validate_revise


def test_deterministic_prune_is_a_new_validated_revision_and_keeps_required_answer():
    from app.agent.reporter import _prune_unsupported_standalone_lines
    from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport

    original = "## Answer\nRequired mechanism [CIT-001-01].\nExtra unsupported claim [CIT-002-01]."
    attempts, judgments, model_calls = [], [], []
    def validate(value, _):
        return CitationValidationReport(
            occurrence_total=2 if "Extra unsupported" in value else 1,
            supported_occurrences=1,
            unsupported_occurrences=int("Extra unsupported" in value),
            details=[CitationValidationDetail(
                citation_label="CIT-002-01", verdict="unsupported",
                sentence="Extra unsupported claim [CIT-002-01].",
                passage_text="No such claim in the source.", keyword_overlap=0.0,
            )] if "Extra unsupported" in value else [],
        )
    result = generate_validate_revise(
        {}, lambda _: model_calls.append(True) or original,
        lambda attempt, text, diagnostic: attempts.append((attempt, text, diagnostic)) or f"revision-{attempt}",
        lambda: None, validate=validate,
        is_acceptable=lambda report: report.unsupported == 0 and "Required mechanism" in attempts[-1][1],
        revision_feedback=lambda _: {}, max_revisions=4,
        persist_validation=lambda *args: judgments.append(args),
        repair_candidate=_prune_unsupported_standalone_lines,
    )
    assert result.adopted and result.revision_id == "revision-1"
    assert len(model_calls) == 1
    assert len(attempts) == len(judgments) == 2
    assert attempts[0][1] == original
    assert attempts[1][2]["code"] == "deterministic_unsupported_line_prune"
    assert "Extra unsupported" not in result.answer_body
    assert "Required mechanism" in result.answer_body
from app.trace import store


def test_identical_draft_is_revalidated_after_window_refresh():
    validations = iter([False, True])
    calls = []
    result = generate_validate_revise(
        {}, lambda _: calls.append("writer") or "unchanged cited fact",
        lambda attempt, *_: f"revision-{attempt}", lambda: None,
        validate=lambda *_: {"ok": next(validations)},
        is_acceptable=lambda value: value["ok"],
        revision_feedback=lambda _: {"refresh": True}, max_revisions=2,
    )
    assert calls == ["writer", "writer"]
    assert result.adopted and result.integrity == "passed"
    assert result.revision_id == "revision-1"


def test_revision_adopts_second_valid_draft_and_persists_both():
    drafts = iter(["bad", "good"])
    saved = []
    result = generate_validate_revise(
        {}, lambda _: next(drafts), lambda attempt, text, diagnostic: saved.append((attempt, text)) or f"r{attempt}", lambda: None,
        validate=lambda text, _: {"ok": text == "good"}, is_acceptable=lambda report: report["ok"],
        revision_feedback=lambda _: {"code": "missing_citation"}, max_revisions=2,
    )
    assert result.adopted and result.markdown == "good"
    assert saved == [(0, "bad"), (1, "good")]


def test_budget_exhaustion_never_starts_a_revision():
    calls = []
    with pytest.raises(BudgetExceeded):
        generate_validate_revise(
            {}, lambda _: calls.append(1) or (_ for _ in ()).throw(BudgetExceeded("test")), lambda *_: None, lambda: None,
            validate=lambda *_: {}, is_acceptable=lambda _: False, revision_feedback=lambda _: {},
        )
    assert calls == [1]


def test_identical_second_draft_stops_before_third_provider_call():
    calls = []
    saved = []
    result = generate_validate_revise(
        {}, lambda _: calls.append(1) or "same draft",
        lambda attempt, text, diagnostic: saved.append((attempt, text)) or f"r{attempt}",
        lambda: None,
        validate=lambda *_: {"ok": False}, is_acceptable=lambda _: False,
        revision_feedback=lambda _: {"uncited_claims": ["claim"]}, max_revisions=2,
    )
    assert len(calls) == 2
    assert saved == [(0, "same draft"), (1, "same draft")]
    assert result.integrity == "incomplete" and not result.adopted
    assert result.diagnostics[-1]["code"] == "repeated_report_draft"


def test_validator_exception_stops_without_a_second_model_call():
    calls = []
    saved = []

    result = generate_validate_revise(
        {},
        lambda _: calls.append("model") or "candidate",
        lambda attempt, text, diagnostic: saved.append((attempt, text, diagnostic["code"])) or f"r{attempt}",
        lambda: None,
        validate=lambda *_: (_ for _ in ()).throw(RuntimeError("validator unavailable")),
        is_acceptable=lambda _: False,
        revision_feedback=lambda _: {},
        max_revisions=2,
    )

    assert calls == ["model"]
    assert saved == [(0, "candidate", "candidate_retained")]
    assert result.integrity == "failed"
    assert result.adopted is False
    assert result.diagnostics[-1]["code"] == "validation_error"


def test_audit_artifact_redacts_draft_and_hashes_saved_bytes(db, tmp_path, monkeypatch):
    import app.agent.report_generation as report_generation

    root = tmp_path / "repo"
    module_dir = root / "app" / "agent"
    module_dir.mkdir(parents=True)
    monkeypatch.setattr(report_generation, "__file__", str(module_dir / "report_generation.py"))
    run = store.create_agent_run(db, "audit fixture", "summary", "real")
    audit = ReportGenerationAudit(db, run.run_id, [])

    secret = "sk-this-must-not-be-persisted"
    audit.persist_attempt(0, f"Draft credential={secret}", {"code": "candidate_retained"})

    entry = audit.attempts[0]
    artifact = __import__("pathlib").Path(entry["artifact_path"])
    saved = artifact.read_bytes()
    assert secret not in saved.decode("utf-8")
    assert "[REDACTED]" in saved.decode("utf-8")
    assert entry["content_sha256"] == hashlib.sha256(saved).hexdigest()
