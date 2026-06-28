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
        "## 6. Research Evidence Aggregation",
        "",
        f"* Evidence items: `{bundle.total_evidence_items}`",
        f"* Source groups: `{len(bundle.source_groups)}`",
        f"* Supported claims: `{len(bundle.claims)}`",
        f"* Unsupported claims: `{len(bundle.unsupported_claims)}`",
        "",
    ]
    if bundle.warnings:
        lines.extend(["### Source Warnings", ""])
        lines.extend(f"* {warning}" for warning in bundle.warnings)
        lines.append("")

    if bundle.source_groups:
        lines.extend(["### Source Groups", ""])
        for group in bundle.source_groups:
            lines.append(
                f"* `{group.source_type}`: {group.count} items "
                f"(mock={group.mock_count}, fallback={group.fallback_count}, "
                f"unsupported={group.unsupported_count})"
            )
        lines.append("")

    if bundle.claims:
        lines.extend(["### Claim-Evidence Map", ""])
        for claim in bundle.claims:
            evidence_refs = ", ".join(f"`{eid}`" for eid in claim.evidence_ids) or "<none>"
            lines.extend(
                [
                    f"* `{claim.claim_id}` {claim.claim}",
                    f"  Evidence: {evidence_refs}; support=`{claim.support_level}`",
                ]
            )
            if claim.notes:
                lines.append(f"  Notes: {claim.notes}")
        lines.append("")

    if bundle.unsupported_claims:
        lines.extend(["### Unsupported Or Limited Claims", ""])
        for claim in bundle.unsupported_claims:
            evidence_refs = ", ".join(f"`{eid}`" for eid in claim.evidence_ids) or "<none>"
            lines.extend(
                [
                    f"* `{claim.claim_id}` {claim.claim}",
                    f"  Evidence: {evidence_refs}; support=`{claim.support_level}`",
                ]
            )
            if claim.notes:
                lines.append(f"  Notes: {claim.notes}")
        lines.append("")

    if bundle.evidence_items:
        lines.extend(["### Evidence Items", ""])
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
            lines.append(f"* ... {len(bundle.evidence_items) - 20} more evidence items omitted.")
        lines.append("")
    else:
        lines.extend(["No structured evidence items were extracted.", ""])
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
        content = str(result.get("content") or result.get("raw_content") or "").strip()
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
        return "mcp_remote"
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
                    notes="Tool returned a failure, rejection, or empty result.",
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
                claim=f"{record.get('tool_name')} did not support a conclusion.",
                evidence_ids=[],
                support_level="unsupported",
                notes=str(record.get("error_message") or record.get("summary") or "tool_failed"),
            )
        )
    return claims, unsupported


def _claim_notes(items: list[EvidenceItem]) -> str | None:
    if any(item.is_fallback for item in items):
        return "At least one supporting item is fallback data and should not be stated as fresh external fact."
    if any(item.is_mock for item in items):
        return "At least one supporting item is mock data for offline demonstration."
    return None


def _warnings(items: list[EvidenceItem]) -> list[str]:
    warnings: list[str] = []
    if any(item.is_mock for item in items):
        warnings.append("Mock evidence is present; do not describe it as real external facts.")
    if any(item.is_fallback for item in items):
        warnings.append("Fallback evidence is present; conclusions relying on it are limited.")
    if any(item.unsupported_reason for item in items):
        warnings.append("Some planned claims are unsupported because tools failed or returned empty results.")
    if any(item.source_type == "mcp_remote" and item.status == "failed" for item in items):
        warnings.append("Remote MCP failures are visible as failed evidence and did not become API 500 errors.")
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
