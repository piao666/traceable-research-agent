"""Report-level completion gate based on final citation occurrences."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Literal, Mapping


REPORT_INTEGRITY_VERSION = "report-integrity-v2"


@dataclass(frozen=True)
class ReportIntegrityResult:
    version: str
    status: Literal["passed", "failed"]
    error_code: str | None
    warnings: list[str]
    claim_total: int
    claim_with_citation: int
    claim_without_citation: int
    claim_citation_coverage_rate: float
    occurrence_total: int
    supported: int
    weakly_supported: int
    unsupported: int
    support_rate: float
    strict_support_rate: float

    def to_plan_dict(self) -> dict[str, Any]:
        """Serialize the fixed gate record persisted independently in plan_json."""

        return {
            "version": self.version,
            "status": self.status,
            "error_code": self.error_code,
            "warnings": list(self.warnings),
            "metrics": {
                "claim_total": self.claim_total,
                "claim_with_citation": self.claim_with_citation,
                "claim_without_citation": self.claim_without_citation,
                "claim_citation_coverage_rate": self.claim_citation_coverage_rate,
                "occurrence_total": self.occurrence_total,
                "supported": self.supported,
                "weakly_supported": self.weakly_supported,
                "unsupported": self.unsupported,
                "support_rate": self.support_rate,
                "strict_support_rate": self.strict_support_rate,
            },
        }


def append_report_integrity_warnings(
    markdown: str,
    result: ReportIntegrityResult,
) -> str:
    """Render report-gate warnings into the final audit artifact."""

    if not result.warnings:
        return markdown
    lines = [
        markdown.rstrip(),
        "",
        "## 13. 报告完整性警告",
        "",
        *[f"* {warning}" for warning in result.warnings],
        "",
    ]
    return "\n".join(lines)


def assess_report_integrity(
    occurrence_bundle: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    reference_report: Any | None = None,
    enforce_reference_consistency: bool = False,
    scope_bundle: Mapping[str, Any] | None = None,
) -> ReportIntegrityResult:
    """Apply the fixed Deep Research V2 final-report citation thresholds."""

    has_claim_universe = isinstance(occurrence_bundle, Mapping) and (
        "claim_occurrences" in occurrence_bundle
    )
    if isinstance(occurrence_bundle, Mapping):
        occurrences = list(occurrence_bundle.get("citation_occurrences") or [])
        claims = list(occurrence_bundle.get("claim_occurrences") or [])
    else:
        occurrences = list(occurrence_bundle)
        claims = []

    total = len(occurrences)
    citation_counts_by_claim: dict[str, int] = {}
    if not has_claim_universe:
        # Compatibility for legacy callers that predate the complete Claim
        # Universe. Deep V2 always supplies explicit claim_occurrences.
        claim_total = total
        claim_with_citation = total
        claim_without_citation = 0
    else:
        for occurrence in occurrences:
            claim_id = str(occurrence.get("claim_occurrence_id") or "")
            if claim_id:
                citation_counts_by_claim[claim_id] = (
                    citation_counts_by_claim.get(claim_id, 0) + 1
                )
        claim_total = len(claims)
        claim_with_citation = sum(
            _claim_citation_count(claim, citation_counts_by_claim) > 0
            for claim in claims
        )
        claim_without_citation = claim_total - claim_with_citation
    claim_citation_coverage_rate = (
        round(claim_with_citation / claim_total, 4) if claim_total else 0.0
    )
    supported = sum(item.get("verdict") == "supported" for item in occurrences)
    weakly_supported = sum(
        item.get("verdict") == "weakly_supported" for item in occurrences
    )
    unsupported = total - supported - weakly_supported
    support_rate = round((supported + weakly_supported) / total, 4) if total else 0.0
    strict_support_rate = round(supported / total, 4) if total else 0.0
    unsupported_rate = unsupported / total if total else 1.0
    unresolved = any(not item.get("passage_id") for item in occurrences)

    error_code: str | None = None
    warnings: list[str] = []
    asserted_conflicts = _asserted_scope_conflicts(occurrence_bundle, scope_bundle)
    uncited_claims = [
        claim
        for claim in claims
        if _claim_citation_count(claim, citation_counts_by_claim) == 0
    ] if has_claim_universe else []
    uncited_deterministic = [
        str(claim.get("claim_text") or "")
        for claim in uncited_claims
        if _is_deterministic_factual_claim(str(claim.get("claim_text") or ""))
    ]
    uncited_ambiguous = [
        str(claim.get("claim_text") or "")
        for claim in uncited_claims
        if not _is_uncertain_or_limitation(str(claim.get("claim_text") or ""))
        and not _is_operational_or_transition(str(claim.get("claim_text") or ""))
        and not _is_deterministic_factual_claim(str(claim.get("claim_text") or ""))
    ]

    if has_claim_universe and claim_total == 0:
        error_code = "no_final_claim_occurrences"
        warnings.append("The final answer contains no deterministic Claim candidates.")
    elif asserted_conflicts:
        error_code = "unresolved_scope_claim_asserted"
        warnings.append(
            f"{len(asserted_conflicts)} deterministic final claim(s) map to unresolved "
            "or requires-human Scope conflicts."
        )
    elif uncited_deterministic:
        error_code = "uncited_deterministic_claim"
        warnings.append(
            f"{len(uncited_deterministic)} deterministic factual final claim(s) have no citation marker."
        )
    elif total == 0:
        error_code = "no_citation_occurrences"
        warnings.append("The final answer contains no citation occurrences.")
    elif unresolved:
        error_code = "unresolved_citation_target"
        warnings.append("At least one final citation occurrence cannot resolve to a passage.")
    elif unsupported_rate > 0.10:
        error_code = "unsupported_citation_rate_exceeded"
        warnings.append("Unsupported final citation occurrences exceed 10%.")
    elif support_rate < 0.90:
        error_code = "citation_support_rate_below_threshold"
        warnings.append("Supported and weakly supported occurrences are below 90%.")
    elif strict_support_rate < 0.60:
        warnings.append("Strictly supported final citation occurrences are below 60%.")

    if uncited_ambiguous:
        warnings.append(
            f"{len(uncited_ambiguous)} final claim(s) have no citation marker and require review."
        )

    if reference_report is not None:
        inconsistent = int(getattr(reference_report, "inconsistent", 0) or 0)
        unresolved_refs = int(getattr(reference_report, "unresolved", 0) or 0)
        reference_total = int(getattr(reference_report, "total", 0) or 0)
        network_failures = int(getattr(reference_report, "network_failures", 0) or 0)
        if inconsistent:
            warnings.append(
                f"{inconsistent} final cited academic work(s) have inconsistent metadata."
            )
            if enforce_reference_consistency and error_code is None:
                error_code = "reference_metadata_inconsistent"
        if network_failures:
            warnings.append(
                f"{network_failures} final cited academic work(s) could not be checked due to network failures."
            )
        elif reference_total and unresolved_refs / reference_total > 0.25:
            warnings.append(
                "More than 25% of final cited academic works remain unresolved."
            )

    return ReportIntegrityResult(
        version=REPORT_INTEGRITY_VERSION,
        status="failed" if error_code else "passed",
        error_code=error_code,
        warnings=warnings,
        claim_total=claim_total,
        claim_with_citation=claim_with_citation,
        claim_without_citation=claim_without_citation,
        claim_citation_coverage_rate=claim_citation_coverage_rate,
        occurrence_total=total,
        supported=supported,
        weakly_supported=weakly_supported,
        unsupported=unsupported,
        support_rate=support_rate,
        strict_support_rate=strict_support_rate,
    )


_ENGLISH_UNCERTAINTY_RE = re.compile(
    r"\b(?:might|uncertain|unresolved|disputed|conflicting|possibly|approximately|estimated)\b"
    r"|\brequires[- ]human\b"
    r"|\bmay\s+(?:be|have|indicate|suggest|reflect|represent|reach|exceed|fall)\b",
    re.IGNORECASE,
)
_CJK_UNCERTAINTY_TERMS = (
    "可能", "或许", "不确定", "尚未解决", "存在冲突", "有待核实",
    "无法确定", "需人工", "约为", "大约", "估计", "尚无定论", "限制",
)
_DETERMINISTIC_FACT_RE = re.compile(
    r"(?:[$¥￥€£]\s*)?\d+(?:[,.]\d+)*(?:\s*%|\s*(?:USD|CNY|RMB))?"
    r"|\b(?:is|are|was|were|has|have|achieves?|reaches?|reached|reduced|"
    r"increased|decreased|exceeds?|costs?)\b"
    r"|(?:达到|增长|下降|提升|减少|超过|低于|高于|占比)",
    re.IGNORECASE,
)
_OPERATIONAL_OR_TRANSITION_TERMS = (
    "以下是", "下面", "综上", "总之", "本节", "本报告", "本回答", "主要来源",
    "生成方式", "完成限制", "说明", "in summary", "the following",
    "this report", "this answer", "sources", "according to the evidence",
)


def _claim_citation_count(
    claim: Mapping[str, Any],
    citation_counts_by_claim: Mapping[str, int],
) -> int:
    explicit = claim.get("citation_count")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return max(0, explicit)
    return citation_counts_by_claim.get(str(claim.get("claim_occurrence_id") or ""), 0)


def _is_uncertain_or_limitation(claim_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(claim_text or "")).casefold()
    return bool(
        _ENGLISH_UNCERTAINTY_RE.search(normalized)
        or any(term in normalized for term in _CJK_UNCERTAINTY_TERMS)
    )


def _is_operational_or_transition(claim_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(claim_text or "")).casefold()
    return any(term in normalized for term in _OPERATIONAL_OR_TRANSITION_TERMS)


def _is_deterministic_factual_claim(claim_text: str) -> bool:
    return bool(
        claim_text
        and not _is_uncertain_or_limitation(claim_text)
        and not _is_operational_or_transition(claim_text)
        and _DETERMINISTIC_FACT_RE.search(claim_text)
    )


def _asserted_scope_conflicts(
    occurrence_bundle: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    scope_bundle: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(occurrence_bundle, Mapping) or not scope_bundle:
        return []
    from app.evidence.scope_reasoning import scope_claim_group_key

    groups = {
        str(item.get("group_id") or ""): item
        for item in scope_bundle.get("scope_claim_groups") or []
    }
    disputed_group_ids = {
        str(item.get("group_id") or "")
        for item in scope_bundle.get("scope_resolutions") or []
        if item.get("status") in {"unresolved", "requires_human"}
    }
    disputed_group_ids.discard("")
    disputed_keys = {
        str((groups.get(str(item.get("group_id") or "")) or {}).get("normalized_key") or "")
        for item in scope_bundle.get("scope_resolutions") or []
        if item.get("status") in {"unresolved", "requires_human"}
    }
    disputed_keys.discard("")
    matches: list[str] = []
    for claim in occurrence_bundle.get("claim_occurrences") or []:
        claim_text = str(claim.get("claim_text") or "").strip()
        if not claim_text or _is_uncertain_or_limitation(claim_text):
            continue
        lineage_group_ids = {
            str(group_id)
            for group_id in claim.get("scope_group_ids") or []
            if str(group_id)
        }
        if lineage_group_ids:
            conflicts = bool(lineage_group_ids & disputed_group_ids)
        else:
            conflicts = scope_claim_group_key({"claim_text": claim_text}) in disputed_keys
        if conflicts:
            matches.append(claim_text)
    return matches
