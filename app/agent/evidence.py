"""Research evidence aggregation from observations and persisted traces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.trace.models import AgentRun, ToolTrace


MOCK_SOURCES = {"mock"}
FALLBACK_SOURCES = {"fallback"}


@dataclass
class EvidenceItem:
    evidence_id: str
    run_id: str
    trace_id: str | None
    step_no: int | None
    tool_name: str
    source_type: str
    source_ref: str | None
    title: str
    snippet: str
    status: str
    confidence: str
    metadata: dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False
    is_fallback: bool = False
    unsupported_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "step_no": self.step_no,
            "tool_name": self.tool_name,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "title": self.title,
            "snippet": self.snippet,
            "status": self.status,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "is_mock": self.is_mock,
            "is_fallback": self.is_fallback,
            "unsupported_reason": self.unsupported_reason,
        }


@dataclass
class EvidenceGroup:
    source_type: str
    evidence_ids: list[str]
    count: int
    mock_count: int = 0
    fallback_count: int = 0
    unsupported_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "evidence_ids": self.evidence_ids,
            "count": self.count,
            "mock_count": self.mock_count,
            "fallback_count": self.fallback_count,
            "unsupported_count": self.unsupported_count,
        }


@dataclass
class ClaimEvidenceMap:
    claim_id: str
    claim: str
    evidence_ids: list[str]
    support_level: str
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "evidence_ids": self.evidence_ids,
            "support_level": self.support_level,
            "notes": self.notes,
        }


@dataclass
class EvidenceBundle:
    run_id: str
    task: str
    total_evidence_items: int
    source_groups: list[EvidenceGroup]
    claims: list[ClaimEvidenceMap]
    evidence_items: list[EvidenceItem]
    unsupported_claims: list[ClaimEvidenceMap]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "total_evidence_items": self.total_evidence_items,
            "source_groups": [group.to_dict() for group in self.source_groups],
            "claims": [claim.to_dict() for claim in self.claims],
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "unsupported_claims": [claim.to_dict() for claim in self.unsupported_claims],
            "warnings": self.warnings,
        }


def build_evidence_bundle(
    run: AgentRun,
    plan: dict[str, Any],
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
) -> EvidenceBundle:
    """Build grouped evidence and a lightweight claim map for a run."""

    records = _evidence_records(observations, traces)
    items: list[EvidenceItem] = []
    for record in records:
        if record["tool_name"] == "report_writer":
            continue
        extracted = _items_from_record(run.run_id, record, len(items))
        items.extend(extracted)

    groups = _group_items(items)
    claims, unsupported = _claim_maps(run, plan, items, records)
    warnings = _warnings(items)
    return EvidenceBundle(
        run_id=run.run_id,
        task=run.task,
        total_evidence_items=len(items),
        source_groups=groups,
        claims=claims,
        evidence_items=items,
        unsupported_claims=unsupported,
        warnings=warnings,
    )


def render_evidence_markdown(bundle: EvidenceBundle) -> list[str]:
    """Render a concise Markdown section for aggregated evidence."""

    lines: list[str] = [
        "## 6. 证据聚合",
        "",
        f"* 证据条目数 (`total_evidence_items`): `{bundle.total_evidence_items}`",
        f"* 来源分组数 (`source_groups`): `{len(bundle.source_groups)}`",
        f"* 已支持结论 (`supported_claims`): `{len(bundle.claims)}`",
        f"* 未支持或受限结论 (`unsupported_claims`): `{len(bundle.unsupported_claims)}`",
        "",
    ]
    if bundle.warnings:
        lines.extend(["### 来源警告", ""])
        lines.extend(f"* {warning}" for warning in bundle.warnings)
        lines.append("")

    if bundle.source_groups:
        lines.extend(["### 来源分组", ""])
        for group in bundle.source_groups:
            lines.append(
                f"* `{group.source_type}`: {group.count} 条 "
                f"(mock={group.mock_count}, fallback={group.fallback_count}, "
                f"unsupported={group.unsupported_count})"
            )
        lines.append("")

    if bundle.claims:
        lines.extend(["### 结论-证据映射", ""])
        for claim in bundle.claims:
            evidence_refs = ", ".join(f"`{eid}`" for eid in claim.evidence_ids) or "<none>"
            lines.extend(
                [
                    f"* `{claim.claim_id}` {claim.claim}",
                    f"  证据: {evidence_refs}; 支持程度=`{claim.support_level}`",
                ]
            )
            if claim.notes:
                lines.append(f"  说明: {claim.notes}")
        lines.append("")

    if bundle.unsupported_claims:
        lines.extend(["### 未支持或受限结论", ""])
        for claim in bundle.unsupported_claims:
            evidence_refs = ", ".join(f"`{eid}`" for eid in claim.evidence_ids) or "<none>"
            lines.extend(
                [
                    f"* `{claim.claim_id}` {claim.claim}",
                    f"  证据: {evidence_refs}; 支持程度=`{claim.support_level}`",
                ]
            )
            if claim.notes:
                lines.append(f"  说明: {claim.notes}")
        lines.append("")

    if bundle.evidence_items:
        lines.extend(["### 证据条目", ""])
        for item in bundle.evidence_items[:20]:
            flags = []
            if item.is_mock:
                flags.append("mock")
            if item.is_fallback:
                flags.append("fallback")
            if item.unsupported_reason:
                flags.append("unsupported")
            suffix = f" ({', '.join(flags)})" if flags else ""
            source_ref = f" source={item.source_ref}" if item.source_ref else ""
            lines.extend(
                [
                    f"* `{item.evidence_id}` step={item.step_no} tool=`{item.tool_name}` "
                    f"type=`{item.source_type}` confidence=`{item.confidence}`{source_ref}{suffix}",
                    f"  {item.title}: {item.snippet}",
                ]
            )
        if len(bundle.evidence_items) > 20:
            lines.append(f"* ... 还有 {len(bundle.evidence_items) - 20} 条证据未展示。")
        lines.append("")
    else:
        lines.extend(["未抽取到结构化证据条目。", ""])
    return lines


def _evidence_records(
    observations: list[dict[str, Any]], traces: list[ToolTrace]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    observed_keys: set[tuple[Any, str]] = set()
    for observation in observations:
        tool_name = str(observation.get("tool_name") or observation.get("action") or "unknown")
        key = (observation.get("step_no"), tool_name)
        observed_keys.add(key)
        output = observation.get("output") if isinstance(observation.get("output"), dict) else {}
        metadata = _observation_metadata(observation)
        records.append(
            {
                "trace_id": observation.get("trace_id"),
                "step_no": observation.get("step_no"),
                "tool_name": tool_name,
                "status": "success" if bool(observation.get("success")) else "failed",
                "success": bool(observation.get("success")),
                "output": output,
                "metadata": metadata,
                "summary": observation.get("output_summary") or observation.get("observation_summary"),
                "error_message": observation.get("error_message"),
            }
        )

    for trace in traces:
        key = (trace.step_no, trace.tool_name)
        if key in observed_keys:
            continue
        output = _trace_output(trace)
        metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
        records.append(
            {
                "trace_id": trace.trace_id,
                "step_no": trace.step_no,
                "tool_name": trace.tool_name,
                "status": trace.status,
                "success": trace.status == "success",
                "output": output,
                "metadata": metadata,
                "summary": trace.output_summary,
                "error_message": trace.error_message,
            }
        )
    return records


def _items_from_record(
    run_id: str,
    record: dict[str, Any],
    existing_count: int,
) -> list[EvidenceItem]:
    if not record.get("success"):
        return [
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title=f"{record['tool_name']} did not produce supported evidence",
                snippet=str(record.get("error_message") or record.get("summary") or "Tool failed.")[:600],
                source_ref=None,
                unsupported_reason=str(record.get("error_message") or "tool_failed"),
            )
        ]

    tool_name = str(record["tool_name"])
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    if tool_name == "file_reader":
        return _file_items(run_id, record, existing_count)
    if tool_name == "sql_query":
        return _sql_items(run_id, record, existing_count)
    if tool_name == "rag_search":
        return _rag_items(run_id, record, existing_count)
    if tool_name == "mcp_github_search":
        return _github_items(run_id, record, existing_count)
    if tool_name == "tavily_search":
        return _tavily_items(run_id, record, existing_count)
    if _is_remote_mcp_record(record):
        return _remote_mcp_items(run_id, record, existing_count)
    if tool_name == "finish":
        summary = str(output.get("summary") or record.get("summary") or "").strip()
        if summary:
            return [
                _make_item(
                    run_id,
                    record,
                    existing_count + 1,
                    title="ReAct finish summary",
                    snippet=summary[:700],
                    source_ref="react_finish",
                    source_type="llm_finish",
                )
            ]
        return []
    return _generic_items(run_id, record, existing_count)


def _file_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record["output"]
    content = str(output.get("content") or "").strip()
    if not content:
        return []
    source_ref = str(output.get("path") or output.get("file_path") or record.get("summary") or "file")
    return [
        _make_item(
            run_id,
            record,
            existing_count + 1,
            title=f"File evidence from {source_ref}",
            snippet=content[:800],
            source_ref=source_ref,
            source_type="file",
        )
    ]


def _sql_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record["output"]
    rows = output.get("rows") or []
    columns = output.get("columns") or []
    if not rows:
        return [
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title="SQL returned no rows",
                snippet=str(record.get("summary") or "Read-only SQL query returned no rows."),
                source_ref="sqlite",
                source_type="sql",
                unsupported_reason="empty_sql_result",
            )
        ]
    selected = rows[:5]
    snippet = json.dumps({"columns": columns, "rows": selected}, ensure_ascii=False, default=str)
    return [
        _make_item(
            run_id,
            record,
            existing_count + 1,
            title=f"Read-only SQL result ({len(rows)} rows)",
            snippet=snippet[:900],
            source_ref="sqlite",
            source_type="sql",
        )
    ]


def _rag_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record["output"]
    hits = [hit for hit in (output.get("hits") or []) if isinstance(hit, dict)]
    items: list[EvidenceItem] = []
    for offset, hit in enumerate(hits[:8], 1):
        source_ref = str(hit.get("source") or hit.get("chunk_id") or "rag")
        title = str(hit.get("title") or hit.get("chunk_id") or source_ref)
        text = str(hit.get("text") or hit.get("content") or "").strip()
        if not text:
            continue
        item = _make_item(
            run_id,
            record,
            existing_count + len(items) + 1,
            title=f"RAG hit: {title}",
            snippet=text[:700],
            source_ref=source_ref,
            source_type="rag",
        )
        item.metadata.update({k: hit.get(k) for k in ("chunk_id", "score") if k in hit})
        if isinstance(hit.get("metadata"), dict):
            item.metadata["hit_metadata"] = hit["metadata"]
        items.append(item)
    if not items:
        items.append(
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title="RAG returned no usable hits",
                snippet=str(record.get("summary") or "RAG search returned no evidence."),
                source_ref="rag",
                source_type="rag",
                unsupported_reason="empty_rag_result",
            )
        )
    return items


def _github_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record["output"]
    results = [item for item in (output.get("results") or []) if isinstance(item, dict)]
    items: list[EvidenceItem] = []
    for result in results[:8]:
        name = str(result.get("full_name") or result.get("name") or result.get("title") or "GitHub result")
        url = str(result.get("url") or result.get("html_url") or "")
        desc = str(result.get("description") or result.get("snippet") or result.get("body") or "").strip()
        snippet = desc or json.dumps(result, ensure_ascii=False, default=str)[:500]
        item = _make_item(
            run_id,
            record,
            existing_count + len(items) + 1,
            title=f"GitHub evidence: {name}",
            snippet=snippet[:700],
            source_ref=url or name,
            source_type=_source_type(record),
        )
        item.metadata.update(
            {
                k: result.get(k)
                for k in ("stars", "language", "updated_at", "type")
                if result.get(k) is not None
            }
        )
        items.append(item)
    if not items:
        items.append(
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title="GitHub search returned no usable results",
                snippet=str(record.get("summary") or "GitHub search returned no evidence."),
                source_ref="github",
                source_type=_source_type(record),
                unsupported_reason="empty_github_result",
            )
        )
    return items


def _tavily_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record["output"]
    items: list[EvidenceItem] = []
    answer = str(output.get("answer") or "").strip()
    if answer:
        items.append(
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title="Tavily synthesized answer",
                snippet=answer[:700],
                source_ref="tavily_answer",
                source_type=_source_type(record),
            )
        )
    results = [item for item in (output.get("results") or []) if isinstance(item, dict)]
    for result in results[:8]:
        title = str(result.get("title") or "Tavily result")
        url = str(result.get("url") or "")
        content = str(
            result.get("clean_content")
            or result.get("content")
            or result.get("raw_content")
            or ""
        ).strip()
        if not content:
            continue
        item = _make_item(
            run_id,
            record,
            existing_count + len(items) + 1,
            title=f"Tavily evidence: {title}",
            snippet=content[:700],
            source_ref=url or title,
            source_type=_source_type(record),
        )
        if result.get("score") is not None:
            item.metadata["score"] = result.get("score")
        items.append(item)
    if not items:
        items.append(
            _make_item(
                run_id,
                record,
                existing_count + 1,
                title="Tavily search returned no usable results",
                snippet=str(record.get("summary") or "Tavily search returned no evidence."),
                source_ref="tavily",
                source_type=_source_type(record),
                unsupported_reason="empty_tavily_result",
            )
        )
    return items


REMOTE_DISCOVERY_TOOLS = {
    "search",
    "map",
    "web_search_exa",
    "web_search_advanced_exa",
    "resolve-library-id",
    "resolve_library_id",
}
REMOTE_SUPPORT_TOOLS = {
    "scrape",
    "extract",
    "crawl",
    "fetch",
    "web_fetch_exa",
    "query-docs",
    "query_docs",
}


def _is_remote_mcp_record(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return metadata.get("tool_source") == "mcp_remote"


def _remote_tool_name(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    tool_name = str(metadata.get("remote_tool_name") or record.get("tool_name") or "")
    return tool_name.strip().lower()


def _remote_evidence_role(record: dict[str, Any]) -> str:
    if not record.get("success"):
        return "failure"
    tool_name = _remote_tool_name(record)
    if tool_name in REMOTE_SUPPORT_TOOLS:
        return "support"
    if tool_name in REMOTE_DISCOVERY_TOOLS:
        return "discovery"
    if any(token in tool_name for token in ("scrape", "extract", "fetch", "docs", "crawl")):
        return "support"
    if any(token in tool_name for token in ("search", "map", "resolve", "discover")):
        return "discovery"
    return "support"


def _remote_mcp_source_type(record: dict[str, Any]) -> str:
    role = _remote_evidence_role(record)
    if role == "failure":
        return "mcp_remote_failure"
    return f"mcp_remote_{role}"


def _remote_result_lists(output: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "documents", "docs", "items", "sources", "data"):
        value = output.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = output.get("output")
    if isinstance(nested, dict):
        return _remote_result_lists(nested)
    return []


def _remote_text_from_item(item: dict[str, Any]) -> str:
    for key in (
        "clean_content",
        "markdown",
        "content",
        "text",
        "snippet",
        "summary",
        "description",
        "body",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(item, ensure_ascii=False, default=str)[:900]


def _remote_url_from_item(item: dict[str, Any]) -> str:
    for key in ("url", "source_url", "html_url", "link"):
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _remote_title_from_item(item: dict[str, Any], fallback: str) -> str:
    for key in ("title", "name", "libraryId", "library_id", "source"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:160]
    url = _remote_url_from_item(item)
    return url or fallback


def _remote_mcp_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    remote_server = str(metadata.get("remote_server") or "remote")
    remote_tool = str(metadata.get("remote_tool_name") or record.get("tool_name") or "tool")
    role = _remote_evidence_role(record)
    source_type = _remote_mcp_source_type(record)
    prefix = "Remote MCP support" if role == "support" else "Remote MCP discovery"

    items: list[EvidenceItem] = []
    for result in _remote_result_lists(output)[:8]:
        title = _remote_title_from_item(result, f"{remote_server}.{remote_tool}")
        snippet = _remote_text_from_item(result)
        if not snippet:
            continue
        item = _make_item(
            run_id,
            record,
            existing_count + len(items) + 1,
            title=f"{prefix}: {title}",
            snippet=snippet[:800],
            source_ref=_remote_url_from_item(result) or title,
            source_type=source_type,
        )
        item.metadata["evidence_role"] = role
        items.append(item)

    if items:
        return items

    content = _remote_text_from_item(output) if output else str(record.get("summary") or "").strip()
    if not content:
        return []
    url = _remote_url_from_item(output)
    item = _make_item(
        run_id,
        record,
        existing_count + 1,
        title=f"{prefix}: {remote_server}.{remote_tool}",
        snippet=content[:800],
        source_ref=url or str(metadata.get("remote_registry_name") or record.get("tool_name") or "mcp_remote"),
        source_type=source_type,
    )
    item.metadata["evidence_role"] = role
    return [item]


def _generic_items(run_id: str, record: dict[str, Any], existing_count: int) -> list[EvidenceItem]:
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    summary = str(record.get("summary") or "").strip()
    snippet = summary or json.dumps(output, ensure_ascii=False, default=str)[:700]
    if not snippet:
        return []
    return [
        _make_item(
            run_id,
            record,
            existing_count + 1,
            title=f"{record['tool_name']} evidence",
            snippet=snippet[:700],
            source_ref=str(record["tool_name"]),
            source_type=_source_type(record),
        )
    ]


def _make_item(
    run_id: str,
    record: dict[str, Any],
    ordinal: int,
    title: str,
    snippet: str,
    source_ref: str | None,
    source_type: str | None = None,
    unsupported_reason: str | None = None,
) -> EvidenceItem:
    metadata = dict(record.get("metadata") or {})
    data_source = str(metadata.get("data_source") or "")
    is_mock = data_source in MOCK_SOURCES
    is_fallback = data_source in FALLBACK_SOURCES or bool(metadata.get("fallback_used"))
    if unsupported_reason:
        confidence = "unsupported"
    elif is_fallback:
        confidence = "low"
    elif is_mock:
        confidence = "medium"
    elif record.get("success"):
        confidence = "high"
    else:
        confidence = "unsupported"
    return EvidenceItem(
        evidence_id=f"E{ordinal:03d}",
        run_id=run_id,
        trace_id=record.get("trace_id"),
        step_no=record.get("step_no"),
        tool_name=str(record.get("tool_name") or "unknown"),
        source_type=source_type or _source_type(record),
        source_ref=source_ref,
        title=title,
        snippet=str(snippet or "")[:900],
        status=str(record.get("status") or ("success" if record.get("success") else "failed")),
        confidence=confidence,
        metadata=metadata,
        is_mock=is_mock,
        is_fallback=is_fallback,
        unsupported_reason=unsupported_reason,
    )


def _source_type(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    data_source = metadata.get("data_source")
    if data_source:
        return str(data_source)
    if metadata.get("tool_source") == "mcp_remote":
        return _remote_mcp_source_type(record)
    tool_name = str(record.get("tool_name") or "")
    if tool_name == "file_reader":
        return "file"
    if tool_name == "sql_query":
        return "sql"
    if tool_name == "rag_search":
        return "rag"
    if tool_name == "mcp_github_search":
        return "github"
    if tool_name == "tavily_search":
        return "tavily"
    if tool_name == "finish":
        return "llm_finish"
    if not record.get("success"):
        return "tool_failure"
    return tool_name or "unknown"


def _group_items(items: list[EvidenceItem]) -> list[EvidenceGroup]:
    by_source: dict[str, list[EvidenceItem]] = {}
    for item in items:
        by_source.setdefault(item.source_type, []).append(item)
    groups: list[EvidenceGroup] = []
    for source_type in sorted(by_source):
        source_items = by_source[source_type]
        groups.append(
            EvidenceGroup(
                source_type=source_type,
                evidence_ids=[item.evidence_id for item in source_items],
                count=len(source_items),
                mock_count=sum(1 for item in source_items if item.is_mock),
                fallback_count=sum(1 for item in source_items if item.is_fallback),
                unsupported_count=sum(1 for item in source_items if item.unsupported_reason),
            )
        )
    return groups


def _claim_maps(
    run: AgentRun,
    plan: dict[str, Any],
    items: list[EvidenceItem],
    records: list[dict[str, Any]],
) -> tuple[list[ClaimEvidenceMap], list[ClaimEvidenceMap]]:
    claims: list[ClaimEvidenceMap] = []
    unsupported: list[ClaimEvidenceMap] = []
    by_step: dict[int | None, list[EvidenceItem]] = {}
    for item in items:
        by_step.setdefault(item.step_no, []).append(item)

    for step in plan.get("steps") or []:
        step_no = step.get("step_no")
        step_items = by_step.get(step_no, [])
        evidence_ids = [item.evidence_id for item in step_items if not item.unsupported_reason]
        unsupported_ids = [item.evidence_id for item in step_items if item.unsupported_reason]
        tool_name = str(step.get("tool_name") or "unknown")
        goal = str(step.get("goal") or step.get("completion_criteria") or tool_name)
        if evidence_ids:
            claims.append(
                ClaimEvidenceMap(
                    claim_id=f"C{len(claims) + 1:03d}",
                    claim=f"{goal} ({tool_name})",
                    evidence_ids=evidence_ids,
                    support_level="partial" if unsupported_ids else "supported",
                    notes=_claim_notes(step_items),
                )
            )
        elif unsupported_ids:
            unsupported.append(
                ClaimEvidenceMap(
                    claim_id=f"U{len(unsupported) + 1:03d}",
                    claim=f"{goal} ({tool_name})",
                    evidence_ids=unsupported_ids,
                    support_level="unsupported",
                    notes="工具失败、被拒绝或返回空结果。",
                )
            )

    unclaimed_items = [
        item
        for item in items
        if item.step_no not in {step.get("step_no") for step in plan.get("steps") or []}
        and not item.unsupported_reason
    ]
    if unclaimed_items:
        claims.append(
            ClaimEvidenceMap(
                claim_id=f"C{len(claims) + 1:03d}",
                claim="Additional evidence captured outside the planned step list.",
                evidence_ids=[item.evidence_id for item in unclaimed_items],
                support_level="supported",
            )
        )

    failed_records = [
        record for record in records
        if not record.get("success") and not by_step.get(record.get("step_no"))
    ]
    for record in failed_records:
        unsupported.append(
            ClaimEvidenceMap(
                claim_id=f"U{len(unsupported) + 1:03d}",
                claim=f"{record.get('tool_name')} 未能支撑结论。",
                evidence_ids=[],
                support_level="unsupported",
                notes=str(record.get("error_message") or record.get("summary") or "tool_failed"),
            )
        )
    return claims, unsupported


def _claim_notes(items: list[EvidenceItem]) -> str | None:
    if any(item.is_fallback for item in items):
        return "至少一条支持证据来自 fallback 数据，不应表述为最新外部事实。"
    if any(item.is_mock for item in items):
        return "至少一条支持证据来自 mock 数据，仅用于离线演示。"
    return None


def _warnings(items: list[EvidenceItem]) -> list[str]:
    warnings: list[str] = []
    if any(item.is_mock for item in items):
        warnings.append("存在 mock 证据，不应描述为真实外部事实。")
    if any(item.is_fallback for item in items):
        warnings.append("存在 fallback 证据，依赖这些证据的结论需要保留限制。")
    if any(item.unsupported_reason for item in items):
        warnings.append("部分计划结论未被支持，因为工具失败或返回空结果。")
    if any(item.source_type.startswith("mcp_remote") and item.status == "failed" for item in items):
        warnings.append("远端 MCP 失败已记录为失败证据，没有升级为 API 500。")
    return warnings


def _observation_metadata(observation: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    output = observation.get("output")
    if isinstance(output, dict) and isinstance(output.get("metadata"), dict):
        metadata.update(output["metadata"])
    direct = observation.get("metadata") or observation.get("tool_result_metadata")
    if isinstance(direct, dict):
        metadata.update(direct)
    return metadata


def _trace_output(trace: ToolTrace) -> dict[str, Any]:
    try:
        parsed = json.loads(trace.output_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
