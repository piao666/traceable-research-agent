"""Report-level completion gate based on final citation occurrences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping


REPORT_INTEGRITY_VERSION = "report-integrity-v1"


@dataclass(frozen=True)
class ReportIntegrityResult:
    version: str
    status: Literal["passed", "failed"]
    error_code: str | None
    warnings: list[str]
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
    """Render non-blocking report-gate warnings into the final audit artifact."""

    if result.status != "passed" or not result.warnings:
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
) -> ReportIntegrityResult:
    """Apply the fixed Deep Research V2 final-report citation thresholds."""

    if isinstance(occurrence_bundle, Mapping):
        occurrences = list(occurrence_bundle.get("citation_occurrences") or [])
    else:
        occurrences = list(occurrence_bundle)

    total = len(occurrences)
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
    if total == 0:
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
        occurrence_total=total,
        supported=supported,
        weakly_supported=weakly_supported,
        unsupported=unsupported,
        support_rate=support_rate,
        strict_support_rate=strict_support_rate,
    )
