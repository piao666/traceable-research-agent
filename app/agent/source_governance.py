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
    _github_org_repo,
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

# ── Phase 8.x: Official-source discovery queries ──────────────────────
_TECHNICAL_QUERY_SUFFIXES = (
    " documentation",
    " github official repository",
    " reference",
)
_OFFICIAL_QUERY_SUFFIXES = (
    " official documentation",
    " official site",
    " official repository",
    " primary source",
)


def build_official_source_queries(query: str) -> list[str]:
    """Generate targeted queries for official-source discovery.

    Technical queries get extra suffixes targeting docs and repos.
    """
    base = str(query or "").strip()
    if not base:
        return []
    technical_indicators = any(
        indicator in base.casefold()
        for indicator in ("api", "sdk", "code", "framework", "library", "tool", "platform",
                           "protocol", "format", "standard", "cli", "syntax", "benchmark")
    )
    suffixes = (
        (_OFFICIAL_QUERY_SUFFIXES + _TECHNICAL_QUERY_SUFFIXES)
        if technical_indicators
        else _OFFICIAL_QUERY_SUFFIXES
    )
    seen: set[str] = {base}
    queries: list[str] = []
    for suffix in suffixes:
        full = f"{base}{suffix}"
        if full not in seen:
            seen.add(full)
            queries.append(full)
    return queries


# ── Phase 8.x: Per-run discovered official sources ────────────────────

def discovered_official_sources(plan: dict[str, Any]) -> dict[str, list[str]]:
    """Return per-run discovered official domains and repos from source_context."""
    ctx = (plan.get("react_state") or {}).get("source_context") or {}
    return {
        "domains": [str(d) for d in (ctx.get("discovered_official_domains") or []) if d],
        "repos": [str(r) for r in (ctx.get("discovered_official_repos") or []) if r],
    }


def record_discovered_official(
    plan: dict[str, Any],
    *,
    domains: list[str] | None = None,
    repos: list[str] | None = None,
) -> dict[str, Any]:
    """Record dynamically discovered official sources into the Run's source_context."""
    state = dict(plan.get("react_state") or {})
    ctx = dict(state.get("source_context") or {})
    if domains:
        existing = {d.casefold() for d in ctx.get("discovered_official_domains", [])}
        for d in domains:
            if d.casefold() not in existing:
                existing.add(d.casefold())
                ctx.setdefault("discovered_official_domains", []).append(d)
    if repos:
        existing = {r.casefold() for r in ctx.get("discovered_official_repos", [])}
        for r in repos:
            if r.casefold() not in existing:
                existing.add(r.casefold())
                ctx.setdefault("discovered_official_repos", []).append(r)
    state["source_context"] = ctx
    plan["react_state"] = state
    return plan


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
        discovered = discovered_official_sources(plan)
        if discovered["domains"]:
            # Second+ round: narrow search to verified official domains
            prepared["include_domains"] = discovered["domains"]
        elif int((plan.get("profile_constraints") or {}).get("min_t0_sources") or 0) > 0:
            # First round: broaden search to discover official sources
            query = str(prepared.get("query") or "").strip()
            if query:
                prepared["query"] = build_official_source_queries(query)[0]
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
    known = {canonicalize_url(row["url"]): row.get("fetch_status")
             for row in (plan.get("react_state") or {}).get("source_context", {}).get("sources", [])}
    # Preserve policy tiers; within each tier try unread candidates before
    # repeated or failed pages. Sorting happens before per-cluster selection.
    budgeted_items.sort(key=lambda item: {"fetched": 1, "failed": 2}.get(
        known.get(canonicalize_url(_item_uri(tool_name, item))), 0))
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
    output["discovery_candidates"] = [{"url": _item_uri(tool_name, item),
        "title": str(item.get("title") or "")[:160],
        "content": str(item.get("content") or item.get("abstract") or "")[:600]}
        for item in budgeted_items if _item_uri(tool_name, item)]
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
    if known:
        governance["selection_log"].append("Prefer unread candidates within existing tier/domain quotas; retain other discovery URLs for recovery.")
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
        and (
            governance.get("quota_shortfall", {}).get("t2_ratio_exceeded")
            or governance.get("quota_shortfall", {}).get("t0_shortfall", 0)
            or governance.get("quota_shortfall", {}).get("independent_shortfall", 0)
            or governance.get("quota_shortfall", {}).get("t2_shortfall", 0)
        )
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

        # ── Discover official sources from this round ──────────────
        field = DISCOVERY_RESULT_FIELDS.get(tool_name, "results")
        raw_items = [
            item for item in (raw_result.output or {}).get(field, [])
            if isinstance(item, dict)
        ] if isinstance(raw_result.output, dict) else []
        from app.evidence.policy import infer_official_source
        entities = _extract_entities_from_plan(plan)
        discovered_domains: set[str] = set()
        discovered_repos: set[str] = set()
        for item in raw_items:
            candidate = _candidate_from_item(tool_name, item)
            if candidate is None:
                continue
            if infer_official_source(candidate, task_entities=entities):
                if candidate.hostname:
                    discovered_domains.add(candidate.hostname)
                org_repo = _github_org_repo(candidate.hostname, candidate.uri)
                if org_repo:
                    discovered_repos.add(f"github.com/{org_repo}")
                item["metadata"] = dict(item.get("metadata") or {})
                item["metadata"]["official"] = True
                item["metadata"]["source_tier"] = "T0"
        if discovered_domains or discovered_repos:
            record_discovered_official(
                plan,
                domains=sorted(discovered_domains),
                repos=sorted(discovered_repos),
            )

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
    positions: dict[str, int] = {}
    for item in [*previous_items, *current_items]:
        if not isinstance(item, dict):
            continue
        identity = _item_uri(tool_name, item) or repr(sorted(item.items()))
        if identity in positions:
            index = positions[identity]
            combined_items[index] = _merge_discovery_item(combined_items[index], item)
            continue
        positions[identity] = len(combined_items)
        combined_items.append(item)
    output = dict(current_output)
    output[field] = combined_items
    return ToolResult(
        success=True,
        output=output,
        output_summary=current.output_summary,
        metadata=dict(current.metadata or {}),
    )


def _merge_discovery_item(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Merge duplicate discovery rows, preferring current-round governance metadata."""

    merged = dict(previous)
    for key, value in current.items():
        if key != "metadata" or not isinstance(value, dict):
            merged[key] = value
            continue
        nested = dict(merged.get("metadata") or {})
        nested.update(value)
        merged["metadata"] = nested
    # A later governance pass may upgrade a URL from T2 to T0. Never let the
    # older row overwrite that upgrade when both rows share one canonical URL.
    old_meta = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
    new_meta = current.get("metadata") if isinstance(current.get("metadata"), dict) else {}
    old_tier = str(old_meta.get("source_tier") or previous.get("source_tier") or "").upper()
    new_tier = str(new_meta.get("source_tier") or current.get("source_tier") or "").upper()
    if new_tier == "T0" or new_meta.get("official") is True or current.get("official") is True:
        merged["metadata"] = {**old_meta, **new_meta, "source_tier": "T0"}
        merged["source_tier"] = "T0" if "source_tier" in merged else merged.get("source_tier")
        merged["official"] = True
    elif old_tier == "T0" and new_tier != "T0":
        merged["metadata"] = {**new_meta, **old_meta}
    return merged


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
    # Flatten nested metadata so dynamic fields like official / source_tier propagate
    nested = item.get("metadata")
    if isinstance(nested, dict):
        for key, value in nested.items():
            if key not in metadata:
                metadata[key] = value
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


# ── Phase 8.x: T0 shortfall recovery orchestration ────────────────────

def recover_t0_shortfall(
    tool_name: str,
    arguments: dict[str, Any],
    initial_result: ToolResult,
    plan: dict[str, Any],
    settings_obj: Settings,
    *,
    execute: Callable[[str, dict[str, Any]], tuple[ToolResult, int]],
    task_entities: list[str] | None = None,
    max_rounds: int | None = None,
) -> tuple[ToolResult, list[GovernedRefetch]]:
    """Full T0 recovery: discover → verify → refetch → reclassify.

    Returns (final_aggregate, history) where final_aggregate has updated
    governance metadata reflecting the post-recovery tier distribution.
    """
    if not needs_targeted_refetch(initial_result):
        return initial_result, []

    from app.evidence.policy import infer_official_source

    entities = task_entities or _extract_entities_from_plan(plan)
    discovered_domains: set[str] = set()
    discovered_repos: set[str] = set()
    refetches: list[GovernedRefetch] = []
    accumulated = initial_result
    allowed_rounds = min(
        settings_obj.max_refetch_rounds,
        settings_obj.max_refetch_rounds if max_rounds is None else max(0, max_rounds),
    )

    for offset in range(1, allowed_rounds + 1):
        round_no = offset
        # Phase 1: Broaden search for official sources
        queries = build_official_source_queries(str(arguments.get("query") or ""))
        if not queries:
            break
        # Round-robin through queries across rounds
        query_idx = (round_no - 1) % len(queries)
        prepared = dict(arguments or {})
        prepared["query"] = queries[query_idx]
        prepared["max_results"] = min(
            _positive_int(arguments.get("max_results"), 5) * settings_obj.oversample_factor,
            settings_obj.max_discovery_candidates,
        )

        raw_result, latency_ms = execute(tool_name, prepared)

        # Phase 2: Classify newly discovered candidates
        field = DISCOVERY_RESULT_FIELDS.get(tool_name, "results")
        raw_items = [
            item for item in (raw_result.output or {}).get(field, [])
            if isinstance(item, dict)
        ] if isinstance(raw_result.output, dict) else []

        # Phase 3: Verify official candidates
        for item in raw_items:
            candidate = _candidate_from_item(tool_name, item)
            if candidate is None:
                continue
            if candidate.hostname in discovered_domains or candidate.uri in discovered_repos:
                continue
            if infer_official_source(candidate, task_entities=entities):
                if candidate.hostname:
                    discovered_domains.add(candidate.hostname)
                org_repo = _github_org_repo(candidate.hostname, candidate.uri)
                if org_repo:
                    discovered_repos.add(f"github.com/{org_repo}")
                # Assign T0 in the candidate's metadata so re-classification picks it up
                item["metadata"] = dict(item.get("metadata") or {})
                item["metadata"]["official"] = True
                item["metadata"]["source_tier"] = "T0"

        # Phase 4: Persist discovered sources into run context
        if discovered_domains or discovered_repos:
            plan = record_discovered_official(
                plan,
                domains=sorted(discovered_domains),
                repos=sorted(discovered_repos),
            )

        # Phase 5: Combine and re-classify
        combined = _combine_discovery_results(tool_name, accumulated, raw_result)
        aggregate = govern_tool_result(
            tool_name,
            combined,
            plan,
            settings_obj,
            refetch_round=round_no,
        )
        governed = _round_result_with_aggregate_governance(
            tool_name, raw_result, aggregate,
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

        # Phase 6: Check if shortfall is resolved
        if not needs_targeted_refetch(aggregate):
            break

    return accumulated, refetches


def _extract_entities_from_plan(plan: dict[str, Any]) -> list[str]:
    """Extract task entities from the plan for official-source matching."""
    task = str(plan.get("task") or "")
    # Simple entity extraction: capitalized words and known product names
    entities: list[str] = []
    import re as _re
    # Product/tech names: Capitalized words of 2+ chars
    for match in _re.finditer(r"\b([A-Z][a-zA-Z]{1,}(?:\s+[A-Z][a-zA-Z]{1,}){0,2})\b", task):
        name = match.group(1).strip()
        if name.lower() not in {"The", "A", "An", "Compare", "Research", "Explain",
                                  "Describe", "Analyze", "How", "What", "Why",
                                  "List", "Find", "Show", "Tell", "And", "Or"}:
            entities.append(name)
    # Also extract known all-caps acronyms
    for match in _re.finditer(r"\b([A-Z]{2,})\b", task):
        acronym = match.group(1)
        if acronym not in {"HTTP", "API", "SDK", "URL", "JSON", "SQL", "SSH"}:
            entities.append(acronym)
    return list(dict.fromkeys(entities))  # deduplicate while preserving order
