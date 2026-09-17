"""Claim-neutral discovery intake before evidence materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.evidence.normalizers import canonicalize_url
from app.evidence.policy import ResearchProfile, load_source_policy
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


@dataclass(frozen=True)
class SourceConstraints:
    mode: str = "open"
    domains: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    preferred_source_classes: tuple[str, ...] = ()
    excluded_domains: tuple[str, ...] = ()

    @classmethod
    def from_plan(cls, plan: dict[str, Any]) -> "SourceConstraints":
        raw = plan.get("source_constraints") or {}
        mode = str(raw.get("mode") or "open").casefold()
        if mode not in {"open", "prioritize", "restrict"}:
            mode = "open"
        return cls(
            mode=mode,
            domains=tuple(_normalize_domain(value) for value in raw.get("domains") or [] if value),
            urls=tuple(_canonical(value) for value in raw.get("urls") or [] if value),
            preferred_source_classes=tuple(
                str(value).casefold() for value in raw.get("preferred_source_classes") or [] if value
            ),
            excluded_domains=tuple(
                _normalize_domain(value) for value in raw.get("excluded_domains") or [] if value
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "domains": list(self.domains),
            "urls": list(self.urls),
            "preferred_source_classes": list(self.preferred_source_classes),
            "excluded_domains": list(self.excluded_domains),
        }


@dataclass(frozen=True)
class SourceIntakeResult:
    selected_items: list[dict[str, Any]]
    selected_urls: list[str]
    duplicate_count: int
    blocked_count: int
    deferred_count: int
    selection_log: list[str]


def research_profile(plan: dict[str, Any], settings: Settings) -> ResearchProfile:
    policy = load_source_policy(settings.source_policy_path)
    profile_name = str(plan.get("retrieval_profile") or settings.default_retrieval_profile)
    return (
        policy.research_profiles.get(profile_name)
        or policy.research_profiles.get(settings.default_retrieval_profile)
        or policy.research_profiles.get("generic")
        or ResearchProfile(name="generic")
    )


def prepare_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    plan: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Apply explicit source constraints and configured discovery/fetch budgets."""

    prepared = dict(arguments or {})
    profile = research_profile(plan, settings)
    constraints = SourceConstraints.from_plan(plan)
    blocked_domains = load_source_policy(settings.source_policy_path).blocked_domains
    if tool_name == "web_fetcher":
        urls = prepared.get("urls")
        if isinstance(urls, list):
            selected = []
            for value in urls:
                uri = _canonical(value)
                if uri and _source_allowed(uri, constraints, blocked_domains):
                    selected.append(uri)
                if len(selected) >= profile.max_fetch_candidates:
                    break
            prepared["urls"] = selected
        return prepared

    limit_field = DISCOVERY_LIMIT_FIELDS.get(tool_name)
    if limit_field is not None:
        requested = _positive_int(prepared.get(limit_field), 5)
        prepared[limit_field] = min(
            requested * profile.oversample_factor,
            profile.max_discovery_candidates,
        )
        if tool_name == "tavily_search" and constraints.mode == "restrict" and constraints.domains:
            prepared["include_domains"] = list(constraints.domains)
    return prepared


def intake_tool_result(
    tool_name: str,
    result: ToolResult,
    plan: dict[str, Any],
    settings: Settings,
) -> ToolResult:
    """Select fetch candidates without authority tiers or source-class quotas."""

    field = DISCOVERY_RESULT_FIELDS.get(tool_name)
    if field is None or not result.success or not isinstance(result.output, dict):
        return result
    raw_items = [item for item in result.output.get(field, []) if isinstance(item, dict)]
    profile = research_profile(plan, settings)
    policy = load_source_policy(settings.source_policy_path)
    constraints = SourceConstraints.from_plan(plan)
    known = {
        _canonical(item.get("canonical_url") or item.get("url"))
        for item in (
            (plan.get("react_state") or {}).get("source_context", {}).get("sources", [])
        )
        if isinstance(item, dict)
    }
    candidates: list[tuple[dict[str, Any], str, str, float, float]] = []
    seen: set[str] = set()
    duplicate_count = 0
    blocked_count = 0
    for item in raw_items[: profile.max_discovery_candidates]:
        uri = _item_uri(tool_name, item)
        canonical = _canonical(uri)
        if not canonical:
            blocked_count += 1
            continue
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        if not _source_allowed(canonical, constraints, policy.blocked_domains):
            blocked_count += 1
            continue
        hostname = (urlsplit(canonical).hostname or "").casefold()
        relevance = _score(item.get("score"), 0.5)
        novelty = 0.0 if canonical in known else 1.0
        candidates.append((item, canonical, hostname, relevance, novelty))

    selected: list[tuple[dict[str, Any], str, str, float, float]] = []
    domain_counts: dict[str, int] = {}
    pending = list(candidates)
    while pending and len(selected) < profile.max_fetch_candidates:
        best = max(
            pending,
            key=lambda candidate: _fetch_priority(
                candidate,
                constraints,
                domain_counts.get(candidate[2], 0),
            ),
        )
        pending.remove(best)
        selected.append(best)
        domain_counts[best[2]] = domain_counts.get(best[2], 0) + 1

    selected_items = [item for item, *_rest in selected]
    selected_urls = [uri for _item, uri, *_rest in selected]
    output = dict(result.output)
    output[field] = selected_items
    output["discovery_candidates"] = [
        {
            "url": _canonical(_item_uri(tool_name, item)),
            "title": str(item.get("title") or item.get("name") or "")[:160],
            "content": str(item.get("content") or item.get("abstract") or "")[:600],
        }
        for item in raw_items[: profile.max_discovery_candidates]
        if _item_uri(tool_name, item)
    ]
    output["fetch_candidates"] = selected_urls
    if "returned" in output:
        output["returned"] = len(selected_items)
    intake = SourceIntakeResult(
        selected_items=selected_items,
        selected_urls=selected_urls,
        duplicate_count=duplicate_count,
        blocked_count=blocked_count,
        deferred_count=max(0, len(candidates) - len(selected)),
        selection_log=[
            "Candidates were ranked by relevance, novelty, domain diversity, freshness hint, and user priority.",
            "No authority tier or source-class quota was used.",
        ],
    )
    metadata = dict(result.metadata or {})
    metadata["source_intake"] = {
        "mode": constraints.mode,
        "profile": profile.name,
        "policy_version": policy.version,
        "discovery_candidate_count": len(raw_items),
        "eligible_candidate_count": len(candidates),
        "selected_candidate_count": len(selected_items),
        "selected_urls": intake.selected_urls,
        "duplicate_count": intake.duplicate_count,
        "blocked_count": intake.blocked_count,
        "deferred_count": intake.deferred_count,
        "selection_log": intake.selection_log,
    }
    metadata["result_count"] = len(selected_items)
    return ToolResult(
        success=True,
        output=output,
        output_summary=(
            f"{result.output_summary or f'{tool_name} completed.'} "
            f"Source intake selected {len(selected_items)}/{len(raw_items)} candidates."
        ),
        metadata=metadata,
    )


def _fetch_priority(
    candidate: tuple[dict[str, Any], str, str, float, float],
    constraints: SourceConstraints,
    domain_count: int,
) -> float:
    item, canonical, hostname, relevance, novelty = candidate
    diversity = 1.0 / (domain_count + 1)
    freshness = 1.0 if item.get("published_at") or item.get("publishedDate") else 0.5
    user_priority = 0.0
    if any(_domain_matches(hostname, domain) for domain in constraints.domains):
        user_priority = 1.0
    elif str((item.get("metadata") or {}).get("source_class") or "").casefold() in constraints.preferred_source_classes:
        user_priority = 1.0
    return (
        0.45 * relevance
        + 0.20 * novelty
        + 0.15 * diversity
        + 0.10 * freshness
        + 0.10 * user_priority
    )


def _source_allowed(
    uri: str,
    constraints: SourceConstraints,
    blocked_domains: tuple[str, ...],
) -> bool:
    parsed = urlsplit(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    hostname = (parsed.hostname or "").casefold()
    if any(_domain_matches(hostname, domain) for domain in (*blocked_domains, *constraints.excluded_domains)):
        return False
    if constraints.mode != "restrict":
        return True
    domain_match = any(_domain_matches(hostname, domain) for domain in constraints.domains)
    url_match = any(uri == allowed or uri.startswith(f"{allowed.rstrip('/')}/") for allowed in constraints.urls)
    return domain_match or url_match


def _item_uri(tool_name: str, item: dict[str, Any]) -> str:
    for key in ("url", "html_url", "abstract_url", "id", "openAccessUrl", "pdf_url"):
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    doi = str(item.get("doi") or item.get("DOI") or "").strip()
    if doi:
        return f"https://doi.org/{doi.removeprefix('https://doi.org/').removeprefix('http://doi.org/')}"
    paper_id = str(item.get("paperId") or "").strip()
    if tool_name == "semantic_scholar_search" and paper_id:
        return f"https://www.semanticscholar.org/paper/{paper_id}"
    return ""


def _canonical(value: Any) -> str:
    uri = str(value or "").strip()
    if not uri.startswith(("http://", "https://")):
        return ""
    try:
        return canonicalize_url(uri)
    except (TypeError, ValueError):
        return ""


def _normalize_domain(value: Any) -> str:
    raw = str(value or "").strip().casefold()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").casefold()


def _domain_matches(hostname: str, domain: str) -> bool:
    return bool(domain) and (hostname == domain or hostname.endswith(f".{domain}"))


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _score(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default
