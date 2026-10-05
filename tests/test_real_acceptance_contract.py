"""Offline contract tests for the real acceptance harness.

These tests exercise only the pure decision boundary; they do not claim that
the providers or the service are reachable.
"""
from __future__ import annotations

import argparse
from scripts.validate_real_runtime import _load_dispatched_child_observations, _valid_page, evaluate_acceptance, main
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.validate_real_runtime import AcceptanceError, ApiAcceptanceClient


def _good(*, execution: str = "deep_research_v2", mode: str = "deep", sha: str = "abc", validation_identity: str | None = "validation-real", fetch_in_child: bool = False) -> dict:
    text = "x" * 250
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    revision = "revision-real"
    manifest = "manifest-real"
    evidence_snapshot = "evidence-real"
    writing_manifest = "writing-real"
    validator = "citation-validator-writing-window-v1"
    fetch_trace = {"tool_name": "web_fetcher", "status": "success", "output": {"pages": [{"content": text, "source_content": text, "content_hash": text_hash, "source_content_hash": text_hash, "quality": {"usable": True}, "fetch_status": "success", "provider": "httpx", "evidence_role": "primary", "content_basis": "full_text"}]}}
    fetch_evidence = {"tool_name": "web_fetcher", "status": "success", "metadata": {"provider": "httpx", "evidence_role": "primary", "content_basis": "full_text", "content_hash": text_hash}}
    return evaluate_acceptance(
        research_mode=mode,
        status={"status": "completed", "source_mode": "real", "research_mode": mode, "execution_mode": execution, "citation_evaluated": True, "terminal_decision": {"status": "completed", "report_sha256": sha, "report_revision_id": revision, "manifest_sha256": manifest, "validation_identity": validation_identity, "evidence_snapshot_id": evidence_snapshot, "writing_manifest_hash": writing_manifest, "validator_version": validator}},
        plan={"research_mode": mode, "execution_mode": execution, "report_revision_id": revision, "research_outcome": {"evidence_assessment": {"passed": True}}, "report_integrity": {"status": "passed"}, "report_generation": {"report_revision_id": revision, "manifest_sha256": manifest, "validation_identity": validation_identity, "evidence_snapshot_id": evidence_snapshot, "writing_manifest_hash": writing_manifest, "validator_version": validator, "adopted": True}},
        traces=([{"tool_name": "report_synthesis", "status": "success", "token_in": 10, "token_out": 5, "metadata": {"provider": "deepseek"}}] if fetch_in_child else [fetch_trace, {"tool_name": "report_synthesis", "status": "success", "token_in": 10, "token_out": 5, "metadata": {"provider": "deepseek"}}]),
        evidence={"evidence_items": [] if fetch_in_child else [fetch_evidence]},
        report={"availability": "available", "exists": True, "terminal_decision": {"status": "completed", "report_sha256": sha, "report_revision_id": revision, "manifest_sha256": manifest, "validation_identity": validation_identity, "evidence_snapshot_id": evidence_snapshot, "writing_manifest_hash": writing_manifest, "validator_version": validator}},
        download_sha256=sha,
        child_observations=[({"evidence_items": [fetch_evidence]}, [fetch_trace])] if fetch_in_child else None,
    )


def test_completed_real_deep_with_full_text_and_matching_download_passes() -> None:
    assert _good()["passed"] is True


def test_deep_run_accepts_verified_child_fetch_evidence() -> None:
    assert _good(fetch_in_child=True)["passed"] is True


def test_dispatched_child_evidence_requires_matching_parent_lineage() -> None:
    parent_trace = [{"run_id": "parent", "tool_name": "research_node_dispatch", "status": "success", "output": {"child_run_id": "child"}}]
    with TemporaryDirectory() as directory, patch("scripts.validate_real_runtime.ApiAcceptanceClient") as client_type:
        client = client_type.return_value
        client.json.side_effect = [
            {"run_id": "child", "parent_run_id": "parent"},
            [{"tool_name": "web_fetcher", "status": "success"}],
            {"evidence_items": []},
        ]
        observations = _load_dispatched_child_observations(client, "parent", parent_trace, Path(directory))
        assert len(observations) == 1
        assert (Path(directory) / "child-trace.json").exists()
        assert (Path(directory) / "child-evidence.json").exists()
    with TemporaryDirectory() as directory, patch("scripts.validate_real_runtime.ApiAcceptanceClient") as client_type:
        client = client_type.return_value
        client.json.return_value = {"run_id": "child", "parent_run_id": "different"}
        try:
            _load_dispatched_child_observations(client, "parent", parent_trace, Path(directory))
            assert False, "unrelated child must not contribute acceptance evidence"
        except AcceptanceError as exc:
            assert "parent link" in str(exc)


def test_public_api_projection_accepts_a_traceable_partial_body() -> None:
    body = "The event loop runs asynchronous tasks and callbacks. " * 6
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    terminal = {
        "status": "completed", "report_sha256": "report-hash", "report_revision_id": "rev",
        "manifest_sha256": "manifest", "validation_identity": "validation",
        "evidence_snapshot_id": "snapshot", "writing_manifest_hash": "writing",
        "validator_version": "citation-validator-writing-window-v1", "decision_input_hash": "decision",
    }
    status = {
        "status": "completed", "source_mode": "real", "research_mode": "quick",
        "execution_mode": "planned", "citation_evaluated": True,
        "citation_unsupported": 0, "terminal_decision": terminal,
        "research_outcome": {"evidence_assessment": {"passed": True}},
    }
    plan = {
        "research_mode": "quick", "execution_mode": "planned", "report_revision_id": "rev",
        "report_generation": {
            "manifest_sha256": "manifest", "validation_identity": "validation",
            "evidence_snapshot_id": "snapshot", "writing_manifest_hash": "writing",
            "validator_version": "citation-validator-writing-window-v1", "adopted": True,
        },
    }
    traces = [
        {"tool_name": "web_fetcher", "status": "success", "output": {"pages": [{
            "content": body, "content_hash": body_hash, "source_content_hash": body_hash,
            "source_content_length": len(body), "quality": {"usable": True},
            "fetch_status": "partial", "content_basis": "partial", "provider": "local_http",
            "fetch_backend": "http", "cache_hit": False,
        }]}},
        {"tool_name": "report_synthesis", "status": "success", "token_in": 10,
         "metadata": {"provider": "deepseek"}},
    ]
    evidence = {"evidence_items": [{
        "tool_name": "web_fetcher", "status": "success", "is_mock": False,
        "metadata": {"provider": "local_http", "content_basis": "partial", "content_hash": body_hash},
    }]}
    result = evaluate_acceptance(
        research_mode="quick", status=status, plan=plan, traces=traces,
        evidence=evidence, report={"availability": "available", "exists": True, "terminal_decision": terminal},
        download_sha256="report-hash",
    )
    assert result["passed"] is True
    evidence["evidence_items"][0]["metadata"]["content_hash"] = "wrong"
    rejected = evaluate_acceptance(
        research_mode="quick", status=status, plan=plan, traces=traces,
        evidence=evidence, report={"availability": "available", "exists": True, "terminal_decision": terminal},
        download_sha256="report-hash",
    )
    assert rejected["passed"] is False


def test_public_trace_truncated_view_uses_full_source_hash_without_accepting_tampering() -> None:
    source = "The event loop runs tasks and callbacks. " * 350
    view = source[:6000]
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    page = {
        "content": view,
        "content_hash": source_hash,
        "source_content_hash": source_hash,
        "source_content_length": len(source),
        "view_truncated": True,
        "quality": {"usable": True},
        "fetch_status": "partial",
        "content_basis": "partial",
        "provider": "local_http",
        "fetch_backend": "http",
        "cache_hit": False,
    }
    assert _valid_page(page)
    assert not _valid_page({**page, "content_hash": "wrong"})
    assert not _valid_page({**page, "source_content_length": len(view)})
    assert not _valid_page({**page, "view_truncated": False})
    assert not _valid_page({**page, "cache_hit": True})
    assert not _valid_page({**page, "content_basis": "snippet_only"})
    assert not _valid_page({**page, "source_content": "changed source"})


def test_http_or_provider_failure_cannot_be_called_success() -> None:
    result = _good()
    result["terminal"] = "failed"
    result = evaluate_acceptance(
        research_mode="deep",
        status={"status": "failed", "source_mode": "real", "research_mode": "deep", "execution_mode": "deep_research_v2", "report_hash": "abc"},
        plan={"research_mode": "deep", "execution_mode": "deep_research_v2"},
        traces=[], evidence={}, report={"availability": "available", "exists": True}, download_sha256="abc",
    )
    assert result["passed"] is False
    assert any("terminal" in reason for reason in result["reasons"])


def test_download_hash_mismatch_fails_even_when_report_exists() -> None:
    result = evaluate_acceptance(
        research_mode="deep",
        status={"status": "completed", "source_mode": "real", "research_mode": "deep", "execution_mode": "deep_research_v2", "terminal_decision": {"status": "completed", "report_sha256": "abc"}},
        plan={"research_mode": "deep", "execution_mode": "deep_research_v2"},
        traces=[{"tool_name": "web_fetcher", "status": "success", "output": {"source_content": {"text": "x" * 250}, "metadata": {"network_request": True, "tool_source": "local_http"}}}, {"tool_name": "report_synthesis", "status": "success", "token_in": 1, "token_out": 1, "metadata": {"provider": "deepseek"}}],
        evidence={}, report={"availability": "available", "exists": True}, download_sha256="def",
    )
    assert result["passed"] is False
    assert any("sha256" in reason for reason in result["reasons"])


def test_discovery_only_completed_fails_substantive_case() -> None:
    result = evaluate_acceptance(
        research_mode="quick",
        status={"status": "completed", "source_mode": "real", "research_mode": "quick", "execution_mode": "planned", "report_hash": "abc"},
        plan={"research_mode": "quick", "execution_mode": "planned"},
        traces=[{"tool_name": "tavily_search", "status": "success", "output_json": '{"results":[{"snippet":"summary"}]}' }],
        evidence={"evidence": []}, report={"availability": "available", "exists": True}, download_sha256="abc",
    )
    assert result["passed"] is False
    assert any("full-text" in reason for reason in result["reasons"])


def test_deep_request_recorded_as_planned_fails_mode_contract() -> None:
    result = _good(execution="planned")
    assert result["passed"] is False
    assert any("planned" in reason for reason in result["reasons"])


def test_zero_citation_report_can_pass_when_validation_identity_is_present() -> None:
    assert _good()["passed"] is True


def test_missing_validation_identity_cannot_claim_citation_evaluated() -> None:
    result = _good(validation_identity=None)
    assert result["passed"] is False
    assert any("validation identity" in reason for reason in result["reasons"])


def test_confirmation_is_required_before_any_api_call(capsys) -> None:
    assert main(["--acceptance-path", "api"]) == 2
    assert "confirmation_required" in capsys.readouterr().out


def test_acceptance_does_not_reapprove_a_plan_that_returns_to_review() -> None:
    from scripts.validate_real_runtime import run_api_acceptance

    with TemporaryDirectory() as directory, patch("scripts.validate_real_runtime.ApiAcceptanceClient") as client_type:
        client = client_type.return_value
        client.json.side_effect = [
            {"status": "waiting_human_plan"},
            {"status": "waiting_human_plan"},
            {"status": "running"},
            {"status": "waiting_human_plan"},
            {"steps": []},
            [],
        ]
        args = argparse.Namespace(
            base_url="http://127.0.0.1:18007", artifact_dir=Path(directory),
            timeout_seconds=30, resume_run_id="run-review", research_mode="deep", question="unused",
        )
        assert run_api_acceptance(args) == 2
        approvals = [call for call in client.json.call_args_list if "approve-plan" in str(call)]
        assert len(approvals) == 1
        assert (Path(directory) / "run-review-plan.json").exists()


def test_http_409_is_persisted_as_replay_artifact() -> None:
    with TemporaryDirectory() as directory:
        client = ApiAcceptanceClient("http://127.0.0.1", Path(directory), 5)
        error = HTTPError("http://127.0.0.1/x", 409, "Conflict", {}, io.BytesIO(b'{"detail":"conflict"}'))
        with patch("scripts.validate_real_runtime.urlopen", side_effect=error):
            status, payload, _ = client.request("GET", "/x")
        assert status == 409
        assert payload == {"detail": "conflict"}
        artifact = json.loads((Path(directory) / "01-get-x.json").read_text(encoding="utf-8"))
        assert artifact == {"http_status": 409, "payload": {"detail": "conflict"}}


def test_query_string_is_removed_from_windows_artifact_filename() -> None:
    with TemporaryDirectory() as directory:
        client = ApiAcceptanceClient("http://127.0.0.1", Path(directory), 5)
        error = HTTPError("http://127.0.0.1/x", 409, "Conflict", {}, io.BytesIO(b'{"detail":"conflict"}'))
        with patch("scripts.validate_real_runtime.urlopen", side_effect=error):
            client.request("POST", "/api/tasks/id/approve-plan?start_async=true", {"approved": True})
        names = [path.name for path in Path(directory).iterdir()]
        assert names == ["01-post-api_tasks_id_approve-plan.json"]


def test_expired_global_deadline_rejects_without_network_call_and_is_recorded() -> None:
    with TemporaryDirectory() as directory:
        client = ApiAcceptanceClient("http://127.0.0.1", Path(directory), 5)
        client.deadline = time.monotonic() - 1
        with patch("scripts.validate_real_runtime.urlopen") as network:
            try:
                client.request("GET", "/slow")
                assert False, "expired deadline must reject"
            except AcceptanceError as exc:
                assert "deadline exceeded" in str(exc)
        network.assert_not_called()
        artifact = json.loads((Path(directory) / "01-get-slow.json").read_text(encoding="utf-8"))
        assert artifact["http_status"] == 0
