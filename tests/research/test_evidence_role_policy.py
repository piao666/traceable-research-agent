"""Pre-R13.1 fail-closed metadata evidence policy regressions."""

from __future__ import annotations

import pytest

from app.evidence.policy import (
    MetadataClaimKind,
    classify_metadata_claim_kind,
    evidence_role_supports_claim,
)


@pytest.mark.parametrize(
    "claim",
    [
        "The paper was published in 2025.",
        "The DOI is 10.xxxx/xxxx.",
        "Smith is an author of the paper.",
    ],
)
def test_explicit_bibliographic_identity_is_supported_by_metadata(claim: str) -> None:
    assert classify_metadata_claim_kind(claim) == MetadataClaimKind.BIBLIOGRAPHIC
    assert evidence_role_supports_claim("official_metadata", claim) is True
    assert evidence_role_supports_claim("discovery_index", claim) is True


@pytest.mark.parametrize(
    "claim",
    [
        "The paper published in 2025 reduced latency by 17%.",
        "The indexed paper achieves 92% accuracy.",
        "The DOI record shows that the method outperforms the baseline.",
        "该论文发表于 2025 年，并将准确率提升到 92%。",
    ],
)
def test_mixed_bibliographic_and_substantive_claim_fails_closed(claim: str) -> None:
    assert classify_metadata_claim_kind(claim) == MetadataClaimKind.SUBSTANTIVE
    assert evidence_role_supports_claim("official_metadata", claim) is False
    assert evidence_role_supports_claim("discovery_index", claim) is False


def test_ambiguous_metadata_claim_fails_closed() -> None:
    claim = "The paper is important to the field."
    assert classify_metadata_claim_kind(claim) == MetadataClaimKind.AMBIGUOUS
    assert evidence_role_supports_claim("official_metadata", claim) is False


def test_primary_content_policy_is_unchanged() -> None:
    assert evidence_role_supports_claim(
        "primary_content", "The method reduced latency by 17%."
    ) is True


@pytest.mark.parametrize("role", ["unknown", "", "malformed", "discovery", "index"])
def test_unknown_and_legacy_discovery_roles_fail_closed_for_substantive_claims(role: str) -> None:
    assert evidence_role_supports_claim(role, "The method reduced latency by 17%.") is False


def test_legacy_discovery_alias_keeps_bibliographic_capability() -> None:
    assert evidence_role_supports_claim("discovery", "The paper was published in 2025.") is True


def test_official_search_snippet_stays_contextual_through_materialization_and_reasoning(db, tmp_path):
    from pathlib import Path
    from app.agent.evidence import EvidenceItem, EvidenceBundle, ClaimEvidenceMap
    from app.evidence.artifact_store import ArtifactStore
    from app.evidence.service import materialize_provenance_bundle, get_provenance_bundle
    from app.evidence.reasoning_service import materialize_reasoning
    from .conftest import create_root

    run = create_root(db, "Verify performance")
    item = EvidenceItem(
        evidence_id="E1", run_id=run.run_id, trace_id=None, step_no=1,
        tool_name="tavily_search", source_type="tavily_api",
        source_ref="https://www.sec.gov/official", title="Official result",
        snippet="The method reduced latency by 17%.", status="success", confidence="high",
        metadata={"content_basis": "snippet_only", "evidence_role": "primary_content"},
    )
    bundle = EvidenceBundle(run.run_id, run.task, 1, [],
        [ClaimEvidenceMap("C1", item.snippet, ["E1"], "high")], [item], [])
    materialized = materialize_provenance_bundle(db, run, bundle, [],
        ArtifactStore(tmp_path), extractor_version="discovery-regression")
    assert materialized["passages"][0]["metadata"]["evidence_role"] == "discovery_index"
    assert {edge["relation"] for edge in materialized["edges"]} == {"contextualizes"}
    materialize_reasoning(db, run.run_id, Path(__file__).resolve().parents[2] / "config/source_policy.v1.json")
    reasoned = get_provenance_bundle(db, run.run_id)
    assert {edge["relation"] for edge in reasoned["edges"]} == {"contextualizes"}
