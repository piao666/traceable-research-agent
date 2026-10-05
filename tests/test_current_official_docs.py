"""Explicit current-docs requests must not use stale official sibling pages."""

from __future__ import annotations

import hashlib

from app.agent.evidence_requirements import assess_required_evidence
from app.agent.react_executor import _early_finish_rejection_reason
from app.agent.research_goal import build_task_contract
from app.agent.source_intake import intake_tool_result
from app.config import Settings
from app.evidence.policy import current_documentation_channel, load_source_policy
from app.reporting.writing_evidence import build_writing_evidence
from app.tools.base import ToolResult


POLICY_PATH = "config/evidence_policy.v3.json"
CURRENT = "https://docs.python.org/3/library/asyncio-eventloop.html"
LOCALE = "https://docs.python.org/fr/3/library/asyncio-eventloop.html"
DEV = "https://docs.python.org/dev/library/asyncio-eventloop.html"
OLD_ONLY = "https://docs.python.org/3.11/library/asyncio-task.html"


def _contract() -> dict:
    return build_task_contract("Explain asyncio using current official Python documentation.")


def _bundle(*, verified: bool) -> dict:
    body = "Python asyncio event loops run asynchronous tasks and callbacks."
    return {
        "source_documents": [{"document_id": "d", "title": "Python asyncio event loop",
                              "canonical_uri": CURRENT,
                              "metadata": {"source_class": "official", "evidence_role": "primary_content"}}],
        "source_snapshots": [{"snapshot_id": "s", "document_id": "d", "content_hash": "snapshot-hash",
                              "metadata": {"evidence_role": "primary_content", "current_channel_verified": verified}}],
        "passages": [{"passage_id": "p", "snapshot_id": "s", "text": body,
                      "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                      "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"}}],
        "citations": [{"citation_label": "CIT-001-01", "passage_id": "p"}],
    }


def test_configured_current_channel_and_sibling_identity():
    policy = load_source_policy(POLICY_PATH)
    is_current, sibling = current_documentation_channel(CURRENT, policy)
    assert is_current and sibling == ("docs.python.org", "library/asyncio-eventloop.html")
    assert current_documentation_channel(LOCALE, policy) == (False, sibling)
    assert current_documentation_channel(DEV, policy) == (False, sibling)
    assert current_documentation_channel(OLD_ONLY, policy)[0] is False
    assert current_documentation_channel("https://docs.python.org.evil.example/3/library/asyncio-eventloop.html", policy) == (False, None)


def test_intake_defers_only_matching_version_locale_siblings():
    plan = {"task_contract": _contract(), "source_constraints": {"mode": "open"}}
    result = intake_tool_result("tavily_search", ToolResult(
        success=True, output={"results": [
            {"url": LOCALE, "title": "Localized event loop", "score": 0.99},
            {"url": DEV, "title": "Development event loop", "score": 0.98},
            {"url": CURRENT, "title": "Current event loop", "score": 0.50},
            {"url": OLD_ONLY, "title": "Older tasks page", "score": 0.60},
        ]}, output_summary="search completed", metadata={"data_source": "live"},
    ), plan, Settings(source_policy_path=POLICY_PATH))
    selected = result.output["fetch_candidates"]
    assert CURRENT in selected and OLD_ONLY in selected
    assert selected[0] == CURRENT
    assert LOCALE not in selected and DEV not in selected
    assert result.metadata["source_intake"]["current_sibling_deferred_count"] == 2


def test_current_gate_and_writer_require_verified_fetched_snapshot():
    contract = _contract()
    stale = _bundle(verified=False)
    assessment = assess_required_evidence(contract, stale)
    assert assessment.gaps[0].code == "current_source_channel_unmet"
    assert not build_writing_evidence(stale, contract).factual_units

    current = _bundle(verified=True)
    assert assess_required_evidence(contract, current).passed
    assert [unit.citation_id for unit in build_writing_evidence(current, contract).factual_units] == ["CIT-001-01"]


def test_react_requires_trace_fetched_final_url_on_current_channel():
    plan = {"task_contract": _contract()}
    source = {"source_id": "S1", "url": CURRENT, "final_url": CURRENT,
              "title": "Python asyncio event loop", "snippet": "Python asyncio event loops run tasks.",
              "fetch_status": "fetched", "content_basis": "full_text", "content_hash": "trace-hash"}
    settings = Settings(source_policy_path=POLICY_PATH)
    assert _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [source]}}, settings) is None
    redirected = {**source, "final_url": LOCALE}
    reason = _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [redirected]}}, settings)
    assert reason is not None and "current_source_channel_unmet" in reason
    missing_final = {**source, "final_url": None}
    reason = _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [missing_final]}}, settings)
    assert reason is not None and "current_source_channel_unmet" in reason
