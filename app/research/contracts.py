"""Typed contracts shared by the PEAR controller and its evidence assessor.

The persisted task contract remains a JSON-compatible dictionary for backwards
compatibility.  These models provide a single validation boundary at the edges
of the controller so a planner cannot silently invent requirement states or
lower evidence thresholds.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


RequirementKind = Literal[
    "fact",
    "comparison",
    "identity",
    "metric",
    "causal",
    "scope",
]
RequirementStatus = Literal[
    "uncovered",
    "discovered",
    "partial",
    "covered",  # legacy spelling retained for old plan payloads
    "satisfied",
    "conflicted",
    "blocked",
]
EvidenceGapType = Literal[
    "missing_evidence",
    "missing_mapping",
    "missing_independence",
    "missing_freshness",
    "unknown_evidence_role",
    "conflict",
    "unmapped_claim",
]


class NodeExecutionResult(TypedDict, total=False):
    """Controller-neutral result returned by every node runner."""

    run_id: str
    status: str
    node_id: str
    operation_id: str
    waiting_reason: str | None
    error_message: str | None
    evidence_revision: str | None


class EvidenceRequirement(BaseModel):
    """One auditable question-to-evidence obligation."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(default="q-1", min_length=1, max_length=160)
    kind: RequirementKind = "fact"
    predicate: str | None = None
    entity: str | None = None
    dimension: str | None = None
    time_scope: str | None = None
    min_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    min_independent_sources: int = Field(default=1, ge=0, le=100)
    acceptable_content_basis: tuple[str, ...] = ("full_text", "table", "structured")
    required: bool = True
    # Legacy comparison contracts used ``mandatory``; retain it while the
    # canonical field is ``required``.
    mandatory: bool | None = None

    @field_validator("acceptable_content_basis")
    @classmethod
    def validate_content_basis(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"full_text", "table", "structured", "snippet_only", "metadata"}
        normalized = tuple(dict.fromkeys(str(item).strip().casefold() for item in values if str(item).strip()))
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise ValueError(f"unsupported acceptable_content_basis: {', '.join(invalid)}")
        return normalized or ("full_text",)


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    requirement_ids: tuple[str, ...] = ()


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = Field(min_length=1, max_length=160)
    requirement_id: str = Field(min_length=1, max_length=160)
    type: EvidenceGapType
    missing_dimensions: tuple[str, ...] = ()
    related_claims: tuple[str, ...] = ()
    related_groups: tuple[str, ...] = ()
    suggested_action: str = "Fetch and verify an eligible source."
    status: Literal["open", "closed"] = "open"


class RequirementClaimLink(BaseModel):
    """Typed contract for the durable requirement-to-claim mapping ledger."""

    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1, max_length=160)
    requirement_id: str = Field(min_length=1, max_length=160)
    claim_occurrence_id: str | None = None
    scope_group_id: str | None = None
    mapping_source: Literal["assessor", "citation_lineage", "scope_reasoning", "manual"] = "assessor"


class SourceDiscoveryLink(BaseModel):
    """Typed contract separating source discovery from evidence support."""

    model_config = ConfigDict(extra="forbid")

    discovery_id: str = Field(min_length=1, max_length=160)
    requirement_id: str = Field(min_length=1, max_length=160)
    source_identity: str = Field(min_length=1, max_length=512)
    source_url: str | None = None
    link_type: Literal["discovered", "fetched", "rejected", "contextual"] = "discovered"


class ResearchOperationContract(BaseModel):
    """Controller-facing operation identity used before external invocation."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=160)
    logical_key: str = Field(min_length=1, max_length=256)
    attempt: int = Field(default=1, ge=1)
    status: Literal[
        "reserved", "running", "succeeded", "failed", "waiting", "cancelled",
        "interrupted", "unknown",
    ] = "reserved"


def normalize_requirements(contract: dict[str, Any] | None) -> list[EvidenceRequirement]:
    """Validate and normalize legacy requirement dictionaries.

    Older plans omit ``question_id`` and ``kind``.  They are assigned stable
    defaults here; invalid requirements are omitted from the assessor rather
    than being treated as completed evidence.
    """

    result: list[EvidenceRequirement] = []
    for index, raw in enumerate((contract or {}).get("requirements") or [], 1):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload.setdefault("requirement_id", f"requirement-{index}")
        payload.setdefault("question_id", f"q-{index}")
        payload.setdefault("kind", "comparison" if payload.get("dimension") else "fact")
        if "required" not in payload and "mandatory" in payload:
            payload["required"] = bool(payload.get("mandatory"))
        if "acceptable_content_basis" not in payload and payload.get("content_basis"):
            payload["acceptable_content_basis"] = [payload["content_basis"]]
        payload.pop("content_basis", None)
        try:
            result.append(EvidenceRequirement.model_validate(payload))
        except Exception:
            # A malformed requirement is an unresolved obligation, not a free
            # pass.  Keep a safe placeholder so completion remains incomplete.
            result.append(
                EvidenceRequirement(
                    requirement_id=str(payload["requirement_id"]),
                    question_id=str(payload.get("question_id") or f"q-{index}"),
                    kind="fact",
                    predicate="invalid_requirement",
                    min_independent_sources=1,
                )
            )
    return result


def requirements_to_dict(requirements: list[EvidenceRequirement]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in requirements]


def normalize_questions(contract: dict[str, Any] | None) -> list[ResearchQuestion]:
    result: list[ResearchQuestion] = []
    for index, raw in enumerate((contract or {}).get("questions") or [], 1):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload.setdefault("question_id", f"q-{index}")
        try:
            result.append(ResearchQuestion.model_validate(payload))
        except Exception:
            continue
    return result
