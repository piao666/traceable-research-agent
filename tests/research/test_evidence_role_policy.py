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
