"""Deterministic comparison requirements and conservative source coverage.

The matrix is a routing aid and a false-success guard, not a semantic oracle.
Only fetched source excerpts can mark a requirement covered; discovery snippets
remain partial until the underlying source is read.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit


_DIMENSION_ALIASES = {
    "架构": ("架构", "architecture", "orchestration", "runtime"),
    "沙箱": ("沙箱", "sandbox", "isolation", "container"),
    "记忆": ("记忆", "memory", "context", "persistence"),
    "工具调用": ("工具调用", "tool calling", "tool use", "function calling", "tools"),
    "插件": ("插件", "plugin", "extension", "mcp", "skills"),
}


def _terms(value: str, aliases: dict[str, tuple[str, ...]] | None = None) -> tuple[str, ...]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ()
    configured = (aliases or {}).get(normalized)
    if configured:
        return tuple(term.lower() for term in configured)
    candidates = {normalized, re.sub(r"[\s_-]+", "", normalized)}
    return tuple(term for term in candidates if term)


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    compact = re.sub(r"[\s_-]+", "", lowered)
    return any(term in lowered or re.sub(r"[\s_-]+", "", term) in compact for term in terms)


def comparison_requirements(entities: list[str], dimensions: list[str]) -> list[dict]:
    return [
        {
            "requirement_id": f"cmp-{entity_index + 1}-{dimension_index + 1}",
            "question_id": f"q-{entity_index + 1}",
            "kind": "comparison",
            "predicate": "compare",
            "entity": entity,
            "dimension": dimension,
            "acceptable_content_basis": ["full_text", "table", "structured"],
            "min_independent_sources": 1,
            "mandatory": True,
        }
        for entity_index, entity in enumerate(entities)
        for dimension_index, dimension in enumerate(dimensions)
    ]


def _fetched_text_by_url(traces) -> dict[str, str]:
    """Recover full fetched text from Trace for coverage without persisting it in plan_json."""

    from app.agent.execution_policy import _contains_demonstration
    from app.agent.source_context import source_url
    from app.tools.web_content_cleaner import page_content_issue

    documents: dict[str, list[str]] = {}
    for trace in traces or []:
        if trace.status != "success" or trace.tool_name not in {"web_fetcher", "pdf_reader"}:
            continue
        try:
            output = json.loads(trace.output_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(output, dict) or _contains_demonstration(output):
            continue
        if trace.tool_name == "pdf_reader":
            rows = [
                {
                    "url": document.get("path"),
                    "content": "\n".join(
                        str(page.get("text") or "")
                        for page in document.get("pages") or []
                        if isinstance(page, dict) and not page.get("error")
                    ),
                }
                for document in output.get("documents") or []
                if isinstance(document, dict)
            ]
        else:
            rows = output.get("pages") or []
        for row in rows:
            if not isinstance(row, dict) or row.get("error"):
                continue
            url = source_url(row.get("url"))
            content = str(row.get("content") or row.get("text") or "")
            if url and content.strip() and not page_content_issue(content):
                documents.setdefault(url, []).append(content[:50000])
    return {url: "\n".join(parts) for url, parts in documents.items()}


def assess_comparison_coverage(
    contract: dict | None,
    source_context: dict | None,
    traces=None,
) -> dict:
    contract = contract or {}
    from app.agent.source_context import source_url

    from app.research.contracts import normalize_requirements

    requirements = [item.model_dump(mode="json") for item in normalize_requirements(contract)]
    if contract.get("goal_kind") != "comparison" or not requirements:
        return {"applicable": False, "complete": True, "requirements": [], "gaps": []}

    sources = list((source_context or {}).get("sources") or [])
    authoritative_traces = traces is not None
    fetched_documents = _fetched_text_by_url(traces)
    rows: list[dict] = []
    for requirement in requirements:
        entity = str(requirement.get("entity") or "")
        dimension = str(requirement.get("dimension") or "")
        entity_terms = _terms(entity)
        dimension_terms = _terms(dimension, _DIMENSION_ALIASES)
        matching: list[dict] = []
        for source in sources:
            text = " ".join(
                str(source.get(key) or "")
                for key in ("title", "url", "snippet", "search_snippet")
            )
            source_url_value = source_url(source.get("url"))
            fetched_content = fetched_documents.get(source_url_value or "", "")
            content_basis = str(source.get("content_basis") or "full_text").casefold()
            if source.get("fetch_status") == "fetched" and (
                fetched_content or not authoritative_traces
            ):
                text += " " + fetched_content
            if (
                _matches(text, entity_terms)
                and _matches(text, dimension_terms)
                and content_basis in set(requirement.get("acceptable_content_basis") or ("full_text",))
                and (fetched_content or not authoritative_traces)
            ):
                matching.append({
                    **source,
                    # Legacy callers without a trace list supplied only an
                    # already-materialized source context.  Preserve their
                    # diagnostic behavior; all authoritative Deep/PEAR calls
                    # pass traces and therefore require actual fetched text.
                    "_fetched_content": bool(fetched_content or not authoritative_traces),
                })
        fetched = [
            source for source in matching
            if source.get("fetch_status") == "fetched" and source.get("_fetched_content")
        ]
        independent_keys = {
            str(source.get("independence_group") or "").strip()
            or urlsplit(str(source.get("url") or "")).netloc
            for source in fetched
        }
        independent_keys.discard("")
        min_independent = int(requirement.get("min_independent_sources") or 0)
        reliability_values = [
            float(source.get("reliability_score"))
            for source in fetched
            if source.get("reliability_score") is not None
        ]
        min_reliability = float(requirement.get("min_reliability") or 0.0)
        reliability_ok = not min_reliability or any(
            score >= min_reliability for score in reliability_values
        )
        status = (
            "covered"
            if fetched and len(independent_keys) >= min_independent and reliability_ok
            else "partial"
            if fetched or matching
            else "uncovered"
        )
        rows.append({
            **requirement,
            "status": status,
            "source_ids": [source.get("source_id") for source in fetched[:4]],
            "independent_hosts": len(independent_keys),
            "independence_satisfied": len(independent_keys) >= min_independent,
            "reliability_satisfied": reliability_ok,
        })
    gaps = [
        f"{row['entity']} × {row['dimension']} ({row['status']})"
        for row in rows
        if row["status"] != "covered"
    ]
    return {
        "applicable": True,
        "complete": not gaps,
        "covered": sum(row["status"] == "covered" for row in rows),
        "total": len(rows),
        "requirements": rows,
        "gaps": gaps[:24],
        "instruction": (
            "For each uncovered product × dimension cell, search that product's official "
            "documentation or official repository, then fetch the source before finishing."
        ),
    }
