"""Deterministic fact normalization, relation classification, and conflict resolution."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.evidence.policy import SourcePolicy, lexical_relevance


REASONING_ENGINE_VERSION = "p2-rule-1"


@dataclass(frozen=True)
class NormalizedFact:
    value: float | None
    unit: str | None
    time_scope: str | None
    polarity: str
    text: str


@dataclass(frozen=True)
class RelationDecision:
    relation: str
    rationale: str
    scope_difference: str | None = None


@dataclass(frozen=True)
class ScoredRelation:
    relation: str
    score: float
    source_cluster_id: str
    source_class: str
    time_scope: str | None = None
    scope_difference: str | None = None
    is_correction: bool = False


@dataclass(frozen=True)
class ConflictResolution:
    status: str
    confidence: float
    support_quality: float
    refute_quality: float
    independent_support_count: int
    independent_refute_count: int
    rationale: dict[str, Any]


UNIT_ALIASES: dict[str, tuple[str, float]] = {
    "%": ("percent", 1.0),
    "percent": ("percent", 1.0),
    "percentage": ("percent", 1.0),
    "usd": ("USD", 1.0),
    "cny": ("CNY", 1.0),
    "rmb": ("CNY", 1.0),
    "元": ("CNY", 1.0),
    "万元": ("CNY", 10_000.0),
    "亿元": ("CNY", 100_000_000.0),
    "thousand": ("count", 1_000.0),
    "million": ("count", 1_000_000.0),
    "billion": ("count", 1_000_000_000.0),
}
NEGATIVE_TERMS = ("decline", "decrease", "decreased", "down", "drop", "下降", "减少", "下跌", "亏损")


def normalize_fact(
    text: str,
    *,
    value: float | None = None,
    unit: str | None = None,
    time_scope: str | None = None,
    polarity: str | None = None,
) -> NormalizedFact:
    extracted_value, extracted_unit = _extract_value(text)
    raw_value = value if value is not None else extracted_value
    raw_unit = unit or extracted_unit
    normalized_unit, factor = _normalize_unit(raw_unit)
    normalized_value = raw_value * factor if raw_value is not None else None
    normalized_polarity = polarity if polarity in {"positive", "negative", "unknown"} else _polarity(text)
    if normalized_value is not None and normalized_polarity == "negative" and normalized_value > 0:
        normalized_value = -normalized_value
    return NormalizedFact(
        value=normalized_value,
        unit=normalized_unit,
        time_scope=_normalize_time(time_scope or _extract_time(text)),
        polarity=normalized_polarity,
        text=" ".join(text.split()),
    )


def classify_relation(
    claim: NormalizedFact,
    assertion: NormalizedFact,
    *,
    prior_relation: str = "supports",
) -> RelationDecision:
    relevance = lexical_relevance(claim.text, assertion.text)
    if claim.time_scope and assertion.time_scope and claim.time_scope != assertion.time_scope:
        return RelationDecision("contextualizes", "time scopes differ", "time")
    if claim.value is not None and assertion.value is not None:
        if relevance < 0.12:
            return RelationDecision(
                "contextualizes",
                "numeric values occur in semantically unrelated text",
                "subject",
            )
        if claim.unit and assertion.unit and claim.unit != assertion.unit:
            return RelationDecision("contextualizes", "units are not comparable", "unit")
        tolerance = max(1e-6, abs(claim.value) * 0.01)
        if math.isclose(claim.value, assertion.value, abs_tol=tolerance):
            return RelationDecision("supports", "normalized numeric values agree")
        return RelationDecision("refutes", "normalized numeric values conflict")
    if claim.polarity != "unknown" and assertion.polarity != "unknown":
        if claim.polarity != assertion.polarity and relevance >= 0.20:
            return RelationDecision("refutes", "fact polarities conflict")
    if relevance >= 0.12:
        return RelationDecision("supports", "claim and assertion have sufficient lexical overlap")
    return RelationDecision(prior_relation, "no deterministic contradiction was found")


def resolve_conflict(
    relations: list[ScoredRelation],
    policy: SourcePolicy,
) -> ConflictResolution:
    supports = [item for item in relations if item.relation == "supports"]
    refutes = [item for item in relations if item.relation == "refutes"]
    scoped = [item for item in relations if item.scope_difference]
    support_scores = _cluster_scores(supports)
    refute_scores = _cluster_scores(refutes)
    support_quality = _combined_quality(support_scores.values())
    refute_quality = _combined_quality(refute_scores.values())
    rationale: dict[str, Any] = {
        "support_clusters": sorted(support_scores),
        "refute_clusters": sorted(refute_scores),
        "scope_differences": sorted({item.scope_difference for item in scoped if item.scope_difference}),
    }

    if scoped and not refutes:
        status = "resolved_by_scope"
    elif not refutes:
        status = "no_conflict"
    elif not supports:
        status = "unresolved"
    elif any(item.is_correction for item in relations):
        status = "resolved_by_recency"
    else:
        top_support_item = max(supports, key=lambda item: item.score)
        top_refute_item = max(refutes, key=lambda item: item.score)
        top_support = top_support_item.score
        top_refute = top_refute_item.score
        support_authority = float(
            policy.source_classes.get(top_support_item.source_class, {}).get("authority", 0.35)
        )
        refute_authority = float(
            policy.source_classes.get(top_refute_item.source_class, {}).get("authority", 0.35)
        )
        authority_margin = float(policy.resolution.get("authority_margin", 0.15))
        high_quality = float(policy.resolution.get("high_quality_threshold", 0.75))
        if abs(support_authority - refute_authority) >= authority_margin:
            status = "resolved_by_authority"
        elif top_support >= high_quality and top_refute >= high_quality:
            status = "requires_human"
        else:
            status = "unresolved"

    winner_quality = max(support_quality, refute_quality)
    if status == "unresolved":
        confidence = min(winner_quality, float(policy.resolution.get("unresolved_confidence_cap", 0.60)))
    elif status == "requires_human":
        confidence = min(winner_quality, float(policy.resolution.get("human_confidence_cap", 0.45)))
    elif status == "resolved_by_scope":
        confidence = min(winner_quality or 0.5, 0.7)
    else:
        conflict_penalty = min(support_quality, refute_quality) * 0.35
        confidence = max(0.0, winner_quality - conflict_penalty)
    rationale["policy_version"] = policy.version
    return ConflictResolution(
        status=status,
        confidence=round(confidence, 6),
        support_quality=round(support_quality, 6),
        refute_quality=round(refute_quality, 6),
        independent_support_count=len(support_scores),
        independent_refute_count=len(refute_scores),
        rationale=rationale,
    )


def parse_llm_relation(value: Any) -> RelationDecision | None:
    if not isinstance(value, dict):
        return None
    relation = value.get("relation")
    rationale = value.get("rationale")
    if relation not in {"supports", "refutes", "contextualizes"} or not isinstance(rationale, str):
        return None
    return RelationDecision(relation, rationale[:1000], value.get("scope_difference"))


def _cluster_scores(items: list[ScoredRelation]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in items:
        scores[item.source_cluster_id] = max(scores.get(item.source_cluster_id, 0.0), item.score)
    return scores


def _combined_quality(values: Any) -> float:
    product = 1.0
    found = False
    for value in values:
        found = True
        product *= 1.0 - max(0.0, min(1.0, float(value)))
    return 1.0 - product if found else 0.0


def _extract_value(text: str) -> tuple[float | None, str | None]:
    units = r"%|percent(?:age)?|USD|CNY|RMB|亿元|万元|元|thousand|million|billion"
    match = re.search(rf"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*({units})?", text, re.IGNORECASE)
    return (float(match.group(1)), match.group(2)) if match else (None, None)


def _normalize_unit(unit: str | None) -> tuple[str | None, float]:
    if not unit:
        return None, 1.0
    return UNIT_ALIASES.get(unit.casefold(), (unit, 1.0))


def _extract_time(text: str) -> str | None:
    match = re.search(r"\b20\d{2}(?:[-/]\d{1,2}|\s*Q[1-4])?\b|\bQ[1-4]\s*20\d{2}\b", text, re.IGNORECASE)
    return match.group(0) if match else None


def _normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.upper().replace("/", "-").split())
    match = re.fullmatch(r"Q([1-4])\s*(20\d{2})", normalized)
    return f"{match.group(2)} Q{match.group(1)}" if match else normalized


def _polarity(text: str) -> str:
    lowered = text.casefold()
    return "negative" if any(term in lowered for term in NEGATIVE_TERMS) else "positive"
