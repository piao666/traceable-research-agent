"""Research contracts, persisted scopes, and Deep Research Engine V2."""

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
