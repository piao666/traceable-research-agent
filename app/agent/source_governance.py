"""Bounded source-candidate governance shared by planned executors."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.evidence.normalizers import canonicalize_url
from app.evidence.policy import (
    RetrievalProfile,
    SourceCandidate,
    SourcePolicy,
    SourceSelection,
    classify_tier,
    load_source_policy,
    select_sources_by_profile,
)
from app.tools.base import ToolResult


DISCOVERY_RESULT_FIELDS = {
    "tavily_search": "results",
    "mcp_github_search": "results",
    "arxiv_search": "papers",
    "semantic_scholar_search": "papers",
    "openalex_search": "papers",
    "crossref_search": "papers",
}
DISCOVERY_LIMIT_FIELDS = {
    "tavily_search": "max_results",
    "mcp_github_search": "limit",
    "arxiv_search": "max_results",
    "semantic_scholar_search": "limit",
    "openalex_search": "max_results",
    "crossref_search": "max_results",
}
REFETCH_SUB_QUERY_PATTERN = re.compile(r"source_refetch_round:(\d+)")


@dataclass(frozen=True)
class GovernedRefetch:
    round_no: int
    arguments: dict[str, Any]
    result: ToolResult
    latency_ms: int


def governance_enabled(plan: dict[str, Any]) -> bool:
    # evaluation profile: skip governance filtering, let LLM judge source quality
    # This follows GPT Researcher's approach: trust the LLM, don't pre-filter.
    profile = str(plan.get("retrieval_profile") or "")
    if profile == "evaluation":
        return False
    return bool(plan.get("retrieval_profile") and plan.get("profile_constraints"))


def persisted_refetch_rounds(traces: list[Any]) -> int:
    """Recover the run-level refetch budget already consumed by persisted traces."""

    highest_round = 0
    for trace in traces:
        sub_query = (
            trace.get("sub_query")
            if isinstance(trace, dict)
            else getattr(trace, "sub_query", None)
        )
        match = REFETCH_SUB_QUERY_PATTERN.fullmatch(str(sub_query or ""))
        if match:
            highest_round = max(highest_round, int(match.group(1)))
    return highest_round


def prepare_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    plan: dict[str, Any],
    settings_obj: Settings,
    *,
    refetch_round: int = 0,
) -> dict[str, Any]:
    """Apply discovery oversampling and hard candidate/fetch budgets."""

    prepared = dict(arguments or {})
    if tool_name == "web_fetcher":
        urls = prepared.get("urls")
        if isinstance(urls, list):
            prepared["urls"] = urls[: settings_obj.max_fetch_candidates]
        return prepared

    limit_field = DISCOVERY_LIMIT_FIELDS.get(tool_name)
    if limit_field is None or not governance_enabled(plan):
        return prepared

    requested = _positive_int(prepared.get(limit_field), 5)
    if refetch_round <= 0:
        requested *= settings_obj.oversample_factor
    prepared[limit_field] = min(requested, settings_obj.max_discovery_candidates)

    if refetch_round > 0 and tool_name == "tavily_search":
        preferred = list((plan.get("profile_constraints") or {}).get("prefer_domains") or [])
        if preferred:
            prepared["include_domains"] = preferred
    return prepared


def govern_tool_result(
    tool_name: str,
    result: ToolResult,
    plan: dict[str, Any],
    settings_obj: Settings,
    *,
    refetch_round: int = 0,
) -> ToolResult:
    """Select discovery candidates and expose the decision in trace metadata."""

    field = DISCOVERY_RESULT_FIELDS.get(tool_name)
    if field is None or not result.success or not isinstance(result.output, dict):
        return result

    if not governance_enabled(plan):
        # Evaluation mode: annotate tiers without filtering
        raw_items = [item for item in result.output.get(field, []) if isinstance(item, dict)]
        if not raw_items:
            return result
        policy, profile = _policy_and_profile(plan, settings_obj)
        candidates = []
        for item in raw_items[:settings_obj.max_discovery_candidates]:
            candidate = _candidate_from_item(tool_name, item)
            if candidate is not None:
                candidates.append(candidate)
        tier_counts = {"T0": 0, "T1": 0, "T2": 0}
        for c in candidates:
            tc = classify_tier(tool_name, c.uri, c.metadata, policy)
            tier_counts[tc.tier] = tier_counts.get(tc.tier, 0) + 1
        metadata = dict(result.metadata or {})
        metadata["source_governance"] = {
            "mode": "annotation_only",
            "profile": profile.name,
            "policy_version": policy.version,
            "discovery_candidate_count": len(raw_items),
            "classified_candidate_count": len(candidates),
            "selected_candidate_count": len(raw_items),
            "tier_counts": tier_counts,
            "independent_clusters": 0,
            "quota_shortfall": {},
            "shortfall_policy": "report_only",
            "selection_log": ["evaluation mode: all results passed through"],
            "oversample_factor": settings_obj.oversample_factor,
            "max_discovery_candidates": settings_obj.max_discovery_candidates,
            "max_fetch_candidates": settings_obj.max_fetch_candidates,
            "max_refetch_rounds": settings_obj.max_refetch_rounds,
            "refetch_round": refetch_round,
            "budget_limited_selection": False,
        }
        metadata["result_count"] = len(raw_items)
        return ToolResult(
            success=True,
            output=result.output,
            output_summary=result.output_summary,
            metadata=metadata,
        )

    policy, profile = _policy_and_profile(plan, settings_obj)
    raw_items = [item for item in result.output[field] if isinstance(item, dict)]
    budgeted_items = raw_items[: settings_obj.max_discovery_candidates]
    candidates: list[SourceCandidate] = []
    item_by_uri: dict[str, dict[str, Any]] = {}
    for item in budgeted_items:
        candidate = _candidate_from_item(tool_name, item)
        if candidate is None or candidate.uri in item_by_uri:
            continue
        candidates.append(candidate)
        item_by_uri[candidate.uri] = item

    selection = select_sources_by_profile(
        candidates,
        profile,
        policy,
        oversample_factor=settings_obj.oversample_factor,
        max_candidates=settings_obj.max_discovery_candidates,
    )
    selected_items = [item_by_uri[candidate.uri] for candidate in selection.selected]
    output = dict(result.output)
    output[field] = selected_items
    if "returned" in output:
        output["returned"] = len(selected_items)

    metadata = dict(result.metadata or {})
    governance = _selection_metadata(
        selection,
        profile.name,
        policy.version,
        discovered_count=len(raw_items),
        candidate_count=len(candidates),
        selected_count=len(selected_items),
        settings_obj=settings_obj,
        refetch_round=refetch_round,
    )
    metadata["source_governance"] = governance
    metadata["result_count"] = len(selected_items)
    summary = result.output_summary or f"{tool_name} completed."
    summary = f"{summary} Source governance selected {len(selected_items)}/{len(raw_items)} candidates."
    return ToolResult(
        success=True,
        output=output,
        output_summary=summary,
        metadata=metadata,
    )


def needs_targeted_refetch(result: ToolResult) -> bool:
    governance = (result.metadata or {}).get("source_governance")
    return bool(
        isinstance(governance, dict)
        and governance.get("quota_shortfall")
        and governance.get("shortfall_policy") == "targeted_refetch"
    )


def execute_targeted_refetches(
    tool_name: str,
    arguments: dict[str, Any],
    initial_result: ToolResult,
    plan: dict[str, Any],
    settings_obj: Settings,
    *,
    execute: Callable[[str, dict[str, Any]], tuple[ToolResult, int]],
    max_rounds: int | None = None,
    starting_round: int = 0,
) -> list[GovernedRefetch]:
    """Run an explicitly bounded targeted refetch loop for a shortfall."""

    if not needs_targeted_refetch(initial_result):
        return []

    refetches: list[GovernedRefetch] = []
    accumulated = initial_result
    allowed_rounds = min(
        settings_obj.max_refetch_rounds,
        settings_obj.max_refetch_rounds if max_rounds is None else max(0, max_rounds),
    )
    for offset in range(1, allowed_rounds + 1):
        round_no = starting_round + offset
        prepared = prepare_tool_arguments(
            tool_name,
            arguments,
            plan,
            settings_obj,
            refetch_round=round_no,
        )
        raw_result, latency_ms = execute(tool_name, prepared)
        combined = _combine_discovery_results(tool_name, accumulated, raw_result)
        aggregate = govern_tool_result(
            tool_name,
            combined,
            plan,
            settings_obj,
            refetch_round=round_no,
        )
        governed = _round_result_with_aggregate_governance(
            tool_name,
            raw_result,
            aggregate,
        )
        refetches.append(
            GovernedRefetch(
                round_no=round_no,
                arguments=prepared,
                result=governed,
                latency_ms=latency_ms,
            )
        )
        accumulated = aggregate
        if not needs_targeted_refetch(aggregate):
            break
    return refetches


def _combine_discovery_results(
    tool_name: str,
    accumulated: ToolResult,
    current: ToolResult,
) -> ToolResult:
    field = DISCOVERY_RESULT_FIELDS.get(tool_name)
    if field is None or not current.success:
        return current
    previous_output = accumulated.output if isinstance(accumulated.output, dict) else {}
    current_output = current.output if isinstance(current.output, dict) else {}
    previous_items = previous_output.get(field) if isinstance(previous_output.get(field), list) else []
    current_items = current_output.get(field) if isinstance(current_output.get(field), list) else []
    combined_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*previous_items, *current_items]:
        if not isinstance(item, dict):
            continue
        identity = _item_uri(tool_name, item) or repr(sorted(item.items()))
        if identity in seen:
            continue
        seen.add(identity)
        combined_items.append(item)
    output = dict(current_output)
    output[field] = combined_items
    return ToolResult(
        success=True,
        output=output,
        output_summary=current.output_summary,
        metadata=dict(current.metadata or {}),
    )


def _round_result_with_aggregate_governance(
    tool_name: str,
    current: ToolResult,
    aggregate: ToolResult,
) -> ToolResult:
    """Keep this round's output while attaching cumulative quota metadata."""

    field = DISCOVERY_RESULT_FIELDS.get(tool_name)
    if field is None or not current.success:
        return current
    current_output = dict(current.output) if isinstance(current.output, dict) else {}
    aggregate_output = aggregate.output if isinstance(aggregate.output, dict) else {}
    aggregate_selected = aggregate_output.get(field) if isinstance(aggregate_output.get(field), list) else []
    selected_uris = {
        _item_uri(tool_name, item)
        for item in aggregate_selected
        if isinstance(item, dict)
    }
    current_items = current_output.get(field) if isinstance(current_output.get(field), list) else []
    selected_current = [
        item
        for item in current_items
        if isinstance(item, dict) and _item_uri(tool_name, item) in selected_uris
    ]
    current_output[field] = selected_current
    if "returned" in current_output:
        current_output["returned"] = len(selected_current)
    metadata = dict(current.metadata or {})
    metadata["source_governance"] = (aggregate.metadata or {}).get("source_governance", {})
    metadata["result_count"] = len(selected_current)
    return ToolResult(
        success=True,
        output=current_output,
        output_summary=(
            f"{current.output_summary or f'{tool_name} refetch completed.'} "
            f"Source governance kept {len(selected_current)} new candidates."
        ),
        metadata=metadata,
    )


def _policy_and_profile(
    plan: dict[str, Any], settings_obj: Settings
) -> tuple[SourcePolicy, RetrievalProfile]:
    policy = load_source_policy(settings_obj.source_policy_path)
    profile_name = str(plan.get("retrieval_profile") or settings_obj.default_retrieval_profile)
    profile = policy.retrieval_profiles.get(profile_name)
    if profile is None:
        profile = policy.retrieval_profiles.get(settings_obj.default_retrieval_profile)
    if profile is None:
        profile = policy.retrieval_profiles["generic"]
    return policy, profile


def _candidate_from_item(tool_name: str, item: dict[str, Any]) -> SourceCandidate | None:
    uri = _item_uri(tool_name, item)
    if not uri:
        return None
    canonical_uri = canonicalize_url(uri) if uri.startswith(("http://", "https://")) else uri
    hostname = (urlsplit(canonical_uri).hostname or "").lower()
    title = str(item.get("title") or item.get("name") or item.get("full_name") or "<untitled>")
    snippet = str(
        item.get("clean_content")
        or item.get("content")
        or item.get("snippet")
        or item.get("summary")
        or item.get("abstract")
        or item.get("description")
        or ""
    )
    organization = str(
        item.get("organization")
        or item.get("publisher")
        or item.get("venue")
        or hostname
        or ""
    ).strip() or None
    metadata = dict(item)
    metadata["tool_name"] = tool_name
    return SourceCandidate(
        uri=canonical_uri,
        hostname=hostname,
        organization=organization,
        title=title,
        snippet=snippet,
        content_basis=str(item.get("content_basis") or "snippet_only"),
        metadata=metadata,
    )


def _item_uri(tool_name: str, item: dict[str, Any]) -> str:
    for key in ("url", "html_url", "abstract_url", "id", "openAccessUrl", "pdf_url"):
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    doi = str(item.get("doi") or item.get("DOI") or "").strip()
    if doi:
        doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        return f"https://doi.org/{doi}"
    paper_id = str(item.get("paperId") or "").strip()
    if tool_name == "semantic_scholar_search" and paper_id:
        return f"https://www.semanticscholar.org/paper/{paper_id}"
    return ""


def _selection_metadata(
    selection: SourceSelection,
    profile_name: str,
    policy_version: str,
    *,
    discovered_count: int,
    candidate_count: int,
    selected_count: int,
    settings_obj: Settings,
    refetch_round: int,
) -> dict[str, Any]:
    return {
        "profile": profile_name,
        "policy_version": policy_version,
        "discovery_candidate_count": discovered_count,
        "classified_candidate_count": candidate_count,
        "selected_candidate_count": selected_count,
        "selected_urls": [candidate.uri for candidate in selection.selected],
        "tier_counts": {
            "T0": selection.t0_count,
            "T1": selection.t1_count,
            "T2": selection.t2_count,
        },
        "independent_clusters": selection.independent_clusters,
        "quota_shortfall": selection.quota_shortfall,
        "shortfall_policy": (
            selection.quota_shortfall.get("shortfall_policy")
            if selection.quota_shortfall
            else None
        ),
        "selection_log": selection.selection_log,
        "oversample_factor": settings_obj.oversample_factor,
        "max_discovery_candidates": settings_obj.max_discovery_candidates,
        "max_fetch_candidates": settings_obj.max_fetch_candidates,
        "max_refetch_rounds": settings_obj.max_refetch_rounds,
        "refetch_round": refetch_round,
        "budget_limited_selection": discovered_count > settings_obj.max_discovery_candidates,
    }


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)
