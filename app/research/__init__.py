"""Research contracts, persisted scopes, and Deep Research Engine V2."""

from typing import TYPE_CHECKING

from app.research.models import (
    CoverageSnapshot,
    EvidenceGapRecord,
    EvidenceRequirement,
    NodeExecutionResult,
    ResearchNode,
    ResearchOperation,
    ResearchPlanRevision,
    ResearchQuestion,
    ResearchScope,
    RequirementClaimLink,
    SourceDiscoveryLink,
)
if TYPE_CHECKING:
    from app.research.branch_executor import SerialPearExecutor

__all__ = [
    "CoverageSnapshot",
    "EvidenceGapRecord",
    "EvidenceRequirement",
    "NodeExecutionResult",
    "ResearchNode",
    "ResearchOperation",
    "ResearchPlanRevision",
    "ResearchQuestion",
    "ResearchScope",
    "RequirementClaimLink",
    "SourceDiscoveryLink",
    "SerialPearExecutor",
]


def __getattr__(name: str):
    """Avoid importing the executor graph when consumers only need contracts.

    ReAct imports research coverage during module initialization, while the
    node executor reuses ReAct.  Eagerly importing SerialPearExecutor here
    therefore creates a cycle before either execution boundary is ready.
    """
    if name == "SerialPearExecutor":
        from app.research.branch_executor import SerialPearExecutor

        return SerialPearExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
