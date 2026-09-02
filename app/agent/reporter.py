"""Markdown report generation from deterministic run observations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

try:
    from app.llm.base import LLMClient
    from app.llm.base import LLMMessage
except ImportError:
    LLMClient = None   # type: ignore[assignment,misc]
    LLMMessage = None  # type: ignore[assignment,misc]

from dataclasses import dataclass, field

from app.trace.models import AgentRun, ToolTrace
from app.agent.context_compressor import compress_evidence, has_useful_evidence
from app.agent.evidence import (
    _items_from_record, build_evidence_bundle, is_eligible_evidence,
    is_research_record, render_evidence_markdown,
)
from app.security.redaction import redact_text


# ── Phase 3: Sub-query grouping ───────────────────────────────────────

CONTENT_BASIS_LABELS: dict[str, str] = {
    "full_text": "🌐 全文",
    "partial": "📄 部分截断",
    "snippet_only": "📎 仅摘要",
}

# ── Phase 8.1: Tier badges ──────────────────────────────────────────────
TIER_BADGES: dict[str, str] = {
    "T0": "🟢 T0",
    "T1": "🟡 T1",
    "T2": "🟠 T2",
}
TIER_LABELS: dict[str, str] = {
    "T0": "一手/原始来源",
    "T1": "机构/权威二手",
    "T2": "社区/个人",
}


@dataclass
class SubQueryGroup:
    """A group of steps, traces, and claims belonging to one sub-query."""
    sub_query: str
    step_nos: list[int] = field(default_factory=list)
    traces: list[ToolTrace] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    passages: list[dict[str, Any]] = field(default_factory=list)


def _build_sub_query_groups(
    plan: dict[str, Any],
    traces: list[ToolTrace],
    provenance_bundle: dict[str, Any] | None,
) -> list[SubQueryGroup]:
    """Group traces by sub_query; fall back to single group if no sub_query labels."""
    # Index passages and citations from provenance
    passages_by_id: dict[str, dict[str, Any]] = {}
    citations_by_claim: dict[str, list[dict[str, Any]]] = {}
    claims_by_id: dict[str, dict[str, Any]] = {}
    if provenance_bundle:
        for p in provenance_bundle.get("passages") or []:
            passages_by_id[str(p.get("passage_id"))] = p
        for c in provenance_bundle.get("citations") or []:
            claim_id = str(c.get("report_claim_id") or "")
            citations_by_claim.setdefault(claim_id, []).append(c)
        for rc in provenance_bundle.get("report_claims") or []:
            claims_by_id[str(rc.get("report_claim_id"))] = rc

    # Group traces by sub_query
    groups: dict[str, list[ToolTrace]] = {}
    for trace in traces:
        key = (trace.sub_query or "").strip()
        groups.setdefault(key, []).append(trace)

    if not groups:
        return []

    result: list[SubQueryGroup] = []
    for sq, sq_traces in groups.items():
        step_nos = sorted({t.step_no for t in sq_traces})
        trace_ids = {t.trace_id for t in sq_traces}

        # Find passages matching this group's traces
        group_passages = [
            p for p in passages_by_id.values()
            if str(p.get("trace_id")) in trace_ids
        ]
        passage_ids = {str(p.get("passage_id")) for p in group_passages}

        # Find citations referencing these passages
        group_citations: list[dict[str, Any]] = []
        seen_citation_ids: set[str] = set()
        for claim_citations in citations_by_claim.values():
            for cit in claim_citations:
                cid = str(cit.get("citation_id") or "")
                if cid in seen_citation_ids:
                    continue
                if str(cit.get("passage_id")) in passage_ids:
                    seen_citation_ids.add(cid)
                    group_citations.append(cit)

        # Find claims referenced by these citations
        claim_ids = {str(c.get("report_claim_id")) for c in group_citations}
        group_claims = [
            claims_by_id[cid] for cid in claim_ids if cid in claims_by_id
        ]

        result.append(SubQueryGroup(
            sub_query=sq,
            step_nos=step_nos,
            traces=sq_traces,
            claims=group_claims,
            citations=group_citations,
            passages=group_passages,
        ))

    return result


def _content_basis_label(passage: dict[str, Any]) -> str:
    cb = str(passage.get("content_basis") or "snippet_only")
    return CONTENT_BASIS_LABELS.get(cb, f"📎 {cb}")


def _build_content_basis_map(
    provenance_bundle: dict[str, Any] | None,
) -> dict[str, str]:
    """Build a mapping of trace_id → content_basis label."""
    if not provenance_bundle:
        return {}
    result: dict[str, str] = {}
    for p in provenance_bundle.get("passages") or []:
        tid = str(p.get("trace_id") or "")
        if tid:
            result[tid] = _content_basis_label(p)
    return result


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "workspace" / "reports"
GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"


def _json_preview(data: Any, max_chars: int = 500) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _requested_result_count(task: str, default: int = 5) -> int:
    patterns = (
        r"(?:top|前)\s*(\d{1,2})",
        r"(?:输出|返回|列出|找出|检索|显示|要)\s*(\d{1,2})\s*(?:个|条|项)?",
        r"(\d{1,2})\s*(?:个|条|项|篇|款)\s*(?:项目|仓库|结果|来源)?",
        r"(\d{1,2})\s*(?:projects|repositories|repos|results|sources)",
    )
    for pattern in patterns:
        match = re.search(pattern, task, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return max(1, min(int(match.group(1)), 20))
        except (TypeError, ValueError):
            continue
    return default


def _is_github_trending_task(task_lower: str) -> bool:
    if "github" not in task_lower or "star" not in task_lower:
        return False
    time_terms = ("today", "daily", "今日", "今天", "当天", "日榜")
    growth_terms = ("growth", "growing", "trending", "增长", "增量", "增长量", "飙升")
    ranking_terms = ("top", "最大", "最多", "排名", "排行", "榜")
    return (
        any(term in task_lower for term in time_terms)
        and any(term in task_lower for term in growth_terms)
        and any(term in task_lower for term in ranking_terms)
    )


def _remote_result_lists(output: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "documents", "docs", "items", "sources", "data"):
        value = output.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = output.get("output")
    if isinstance(nested, dict):
        return _remote_result_lists(nested)
    return []


def _remote_url(item: dict[str, Any]) -> str:
    for key in ("url", "source_url", "html_url", "link"):
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _remote_title(item: dict[str, Any]) -> str:
    for key in ("title", "name", "full_name", "libraryId", "library_id", "source"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:160]
    return _remote_url(item) or "Remote MCP result"


def _remote_content(item: dict[str, Any], max_chars: int = 320) -> str:
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
            return value.strip()[:max_chars]
    return json.dumps(item, ensure_ascii=False, default=str)[:max_chars]


def _record_text_chunks(record: dict[str, Any]) -> list[str]:
    chunks: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in (
                "markdown",
                "content",
                "clean_content",
                "text",
                "summary",
                "description",
                "snippet",
                "body",
            ):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    chunks.append(item.strip())
            for key in ("results", "items", "data", "sources", "documents"):
                nested = value.get(key)
                if isinstance(nested, list):
                    for child in nested:
                        visit(child)
                elif isinstance(nested, dict):
                    visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(record.get("output") or {})
    return chunks


def _extract_github_trending_repositories(records: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    text = "\n".join(
        chunk
        for record in records
        if record.get("success")
        for chunk in _record_text_chunks(record)
    )
    if not text:
        return []

    candidates: list[tuple[str, int]] = []
    patterns = (
        r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        r"\[([A-Za-z0-9_.-]+\s*/\s*[A-Za-z0-9_.-]+)\]\(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+[^)]*\)",
        r"(?:^|\n)\s*#{1,4}\s*([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if len(match.groups()) == 2:
                name = f"{match.group(1)}/{match.group(2)}"
            else:
                name = match.group(1).replace(" ", "")
            if name.lower().startswith(("topics/", "collections/", "features/")):
                continue
            candidates.append((name, match.start()))

    seen: set[str] = set()
    repositories: list[dict[str, str]] = []
    for name, position in sorted(candidates, key=lambda item: item[1]):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        window = re.sub(r"\s+", " ", text[position : position + 700]).strip()
        description = ""
        name_pattern = re.escape(name).replace("/", r"\s*/\s*")
        desc_match = re.search(
            rf"{name_pattern}\s+(.+?)(?:\d[\d,]*\s+stars?\s+today|Built by|Language:|$)",
            window,
            flags=re.IGNORECASE,
        )
        if desc_match:
            description = desc_match.group(1).strip(" -:|")
        repositories.append(
            {
                "name": name,
                "url": f"https://github.com/{name}",
                "description": description[:260] or "页面证据未返回稳定简介字段，请以 GitHub 页面为准。",
            }
        )
        if len(repositories) >= limit:
            break
    return repositories


def _github_trending_final_answer(
    records: list[dict[str, Any]],
    task: str,
    requested_limit: int,
) -> list[str] | None:
    repositories = _extract_github_trending_repositories(records, requested_limit)
    if not repositories:
        return None
    lines = [
        f"以下是根据 GitHub Trending 今日页面证据整理的前 {len(repositories)} 个项目：",
        "",
    ]
    for index, item in enumerate(repositories, 1):
        lines.extend(
            [
                f"{index}、**{item['name']}**",
                "",
                f"* 项目地址：[{item['url']}]({item['url']})",
                f"* 项目简介：{item['description']}",
                "",
            ]
        )
    lines.extend(
        [
            f"> **数据来源说明：** 优先使用 `{GITHUB_TRENDING_URL}` 的页面读取证据；"
            "如果实际返回少于请求数量，说明工具抓取到的页面证据不足。",
            "",
        ]
    )
    return lines


def _remote_selected_evidence(tool_name: str, output: dict[str, Any]) -> str:
    results = _remote_result_lists(output)
    if results:
        return _json_preview(
            [
                {
                    "title": _remote_title(item),
                    "url": _remote_url(item),
                    "content": _remote_content(item),
                }
                for item in results[:5]
            ],
            max_chars=1400,
        )
    selected = {
        "tool": tool_name,
        "url": _remote_url(output),
        "content": _remote_content(output, max_chars=700),
    }
    return _json_preview(selected, max_chars=1000)


def _selected_evidence(tool_name: str, output: Any) -> str:
    if not isinstance(output, dict):
        return _json_preview(output)

    if tool_name == "file_reader":
        content = str(output.get("content") or "")
        return content[:500] + ("..." if len(content) > 500 else "")
    if tool_name == "sql_query":
        columns = output.get("columns") or []
        rows = output.get("rows") or []
        return _json_preview({"columns": columns, "rows": rows[:5]}, max_chars=900)
    if tool_name == "mcp_github_search":
        results = output.get("results") or []
        selected = [
            {
                "title": result.get("title"),
                "full_name": result.get("full_name"),
                "name": result.get("name"),
                "url": result.get("url"),
                "stars": result.get("stars"),
                "description": result.get("description"),
                "language": result.get("language"),
                "updated_at": result.get("updated_at"),
                "type": result.get("type"),
                "source": result.get("source"),
                "snippet": str(result.get("snippet") or "")[:240],
            }
            for result in results[:5]
        ]
        return _json_preview(selected, max_chars=1200)
    if tool_name == "tavily_search":
        results = output.get("results") or []
        return _json_preview(
            {
                "answer": output.get("answer"),
                "results": [
                    {
                        "title": result.get("title"),
                        "url": result.get("url"),
                        "content": str(result.get("clean_content") or result.get("content") or "")[:300],
                        "content_quality": result.get("content_quality"),
                        "score": result.get("score"),
                    }
                    for result in results[:5]
                    if isinstance(result, dict)
                ],
            },
            max_chars=1400,
        )
    if "." in tool_name:
        return _remote_selected_evidence(tool_name, output)
    if tool_name in ("arxiv_search", "semantic_scholar_search"):
        papers = output.get("papers") or []
        return _json_preview(
            [
                {
                    "title": p.get("title"),
                    "authors": p.get("authors", [])[:3],
                    "year": p.get("year"),
                    "abstract": str(p.get("abstract") or "")[:200],
                }
                for p in papers[:5]
            ],
            max_chars=1400,
        )
    return _json_preview(output)


def _observation_metadata(observation: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    output = observation.get("output")
    if isinstance(output, dict) and isinstance(output.get("metadata"), dict):
        metadata.update(output["metadata"])
    direct = observation.get("metadata") or observation.get("tool_result_metadata")
    if isinstance(direct, dict):
        metadata.update(direct)
    return metadata


def _failure_category(error_message: str | None, metadata: dict[str, Any]) -> str:
    category = str(metadata.get("error_category") or "").lower()
    category_labels = {
        "timeout": "远程调用超时",
        "rate_limited": "远程服务限流",
        "auth_error": "认证或凭据错误",
        "provider_error": "远程服务失败",
        "invalid_result": "工具结果格式无效",
        "internal_error": "系统内部错误",
        "invalid_request": "工具参数无效",
        "policy_error": "安全策略拒绝",
        "unavailable": "工具或服务不可用",
        "not_found": "目标资源不存在",
        "unknown": "未分类工具失败",
    }
    if category in category_labels:
        return category_labels[category]
    error_type = str(metadata.get("error_type") or "").lower()
    text = f"{error_type} {error_message or ''}".lower()
    if "api_key is not configured" in text:
        return "远端服务未配置"
    if "same_tool_max_calls" in text or error_type == "tool_call_limit":
        return "调用次数上限保护"
    if any(term in text for term in ("safety_rejected", "read-only", "readonly", "sql")):
        return "安全限制"
    if any(term in text for term in ("no_hit", "no hit", "empty")):
        return "检索为空"
    return "工具失败"


def _is_remote_mcp_trace(trace: ToolTrace) -> bool:
    tool_name = (trace.tool_name or "").lower()
    return (
        "source_pack." in tool_name
        or tool_name.startswith(("firecrawl.", "exa.", "context7."))
    )


def _degradation_state(plan: dict[str, Any], traces: list[ToolTrace]) -> tuple[str, str]:
    react_state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
    if bool(react_state.get("fallback_used")):
        return "已降级", "ReAct 触发 fallback，运行由可追踪的兜底执行完成。"
    remote_failures = [
        trace
        for trace in traces
        if trace.status in {"failed", "rejected"} and _is_remote_mcp_trace(trace)
    ]
    if remote_failures:
        return "部分降级", f"{len(remote_failures)} 个远端 MCP 调用失败，报告保留失败证据并继续使用可用来源。"
    return "未降级", "未记录远端 MCP 失败或 fallback。"


def _friendly_report_error(error_message: str | None) -> str:
    text = redact_text(error_message or "<none>")
    if "EXA_API_KEY is not configured" in text:
        return "Exa 远端服务凭证未配置。"
    if "FIRECRAWL_API_KEY is not configured" in text:
        return "Firecrawl 远端服务凭证未配置。"
    return text


def _trace_output(trace: ToolTrace) -> dict[str, Any]:
    try:
        parsed = json.loads(trace.output_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _trace_metadata(trace: ToolTrace) -> dict[str, Any]:
    output = _trace_output(trace)
    metadata = output.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _parallel_metadata_lines(metadata: dict[str, Any]) -> list[str]:
    if metadata.get("parallel") is not True:
        return []
    return [
        "Parallel execution metadata:",
        "",
        f"* parallel_group_id: `{metadata.get('parallel_group_id')}`",
        f"* parallel_worker_id: `{metadata.get('parallel_worker_id')}`",
        f"* parallel_group_size: `{metadata.get('parallel_group_size')}`",
        f"* execution_mode: `{metadata.get('execution_mode')}`",
        f"* started_at: `{metadata.get('started_at')}`",
        f"* finished_at: `{metadata.get('finished_at')}`",
        f"* latency_ms: `{metadata.get('latency_ms')}`",
        "",
    ]


def _evidence_records(
    observations: list[dict[str, Any]], traces: list[ToolTrace]
) -> list[dict[str, Any]]:
    """Return tool evidence, preferring live observations over persisted trace JSON."""

    records: list[dict[str, Any]] = []
    observed_keys: set[tuple[Any, str]] = set()
    for observation in observations:
        tool_name = str(observation.get("tool_name") or observation.get("action") or "unknown")
        key = (observation.get("step_no"), tool_name)
        observed_keys.add(key)
        records.append(
            {
                "step_no": observation.get("step_no"),
                "tool_name": tool_name,
                "success": bool(observation.get("success")),
                "output": observation.get("output") if isinstance(observation.get("output"), dict) else {},
                "metadata": _observation_metadata(observation),
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
                "step_no": trace.step_no,
                "tool_name": trace.tool_name,
                "success": trace.status == "success",
                "output": output,
                "metadata": metadata,
                "summary": trace.output_summary,
                "error_message": trace.error_message,
            }
        )
    return records


def _github_final_answer(record: dict[str, Any], task: str) -> list[str] | None:
    results = record["output"].get("results") or []
    repositories = [item for item in results if isinstance(item, dict)]
    if not repositories:
        return None

    def stars(item: dict[str, Any]) -> int:
        value = item.get("stars", item.get("stargazers_count", 0))
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    repositories.sort(key=stars, reverse=True)
    requested_limit = _requested_result_count(task, 5)
    repositories = repositories[:requested_limit]
    source = str(record["metadata"].get("data_source") or "unknown")
    source_intro = {
        "public_api": "以下是根据真实 GitHub Public API 为你整理的仓库结果：",
        "cache": "以下是根据 GitHub API 缓存为你整理的仓库结果：",
        "mock": "以下是根据 mock 离线数据整理的演示结果：",
        "fallback": "以下是 GitHub API 请求失败后使用降级数据整理的结果：",
    }.get(source, "以下是根据本次 GitHub 工具证据整理的仓库结果：")
    lines = [source_intro, ""]
    for index, item in enumerate(repositories, 1):
        name = item.get("full_name") or item.get("name") or item.get("title") or "未命名仓库"
        url = item.get("url") or item.get("html_url")
        description = item.get("description") or item.get("snippet") or "工具未返回简介。"
        lines.extend([f"{index}、**{name}**", ""])
        if "stars" in item or "stargazers_count" in item:
            lines.append(f"* Star 数：{stars(item):,}")
        if url:
            lines.append(f"* 地址：[{url}]({url})")
        if item.get("language"):
            lines.append(f"* 主要语言：{item['language']}")
        lines.extend([f"* 简介：{description}", ""])
    source_note = {
        "public_api": "以上结果来自真实 GitHub Public API。",
        "cache": "以上结果来自 GitHub API 缓存。",
        "mock": "当前为 mock 数据，仅用于演示，不代表真实排名。",
        "fallback": "真实 API 失败后已降级，以上结果不能作为真实排名依据。",
    }.get(source, "数据来源以本报告后续 Evidence 和 Metadata 为准。")
    lines.extend([f"> **数据来源说明：** {source_note}", ""])
    return lines


def _tavily_final_answer(record: dict[str, Any], task: str) -> list[str] | None:
    output = record["output"]
    results = [item for item in (output.get("results") or []) if isinstance(item, dict)
               and str(item.get("url") or "").startswith(("https://", "http://"))
               and str(item.get("clean_content") or item.get("content") or item.get("raw_content") or "").strip()]
    if not results:
        return None
    source = str(record["metadata"].get("data_source") or "unknown")
    intro = {
        "tavily_api": "以下是根据真实 Tavily Search API 检索证据整理的资料：",
        "mock": "以下是根据 Tavily mock 离线数据整理的演示资料：",
        "fallback": "以下是 Tavily API 请求失败后使用降级数据整理的资料：",
    }.get(source, "以下是根据本次 Tavily 工具证据整理的资料：")
    lines = [intro, ""]
    requested_limit = _requested_result_count(task, 5)
    for index, item in enumerate(results[:requested_limit], 1):
        title = item.get("title") or "未命名来源"
        lines.extend([f"{index}、**{title}**", ""])
        if item.get("url"):
            lines.append(f"* 链接：{item['url']}")
        content = item.get("clean_content") or item.get("content")
        if content:
            lines.append(f"* 摘要：{str(content)[:500]}")
        if item.get("score") is not None:
            lines.append(f"* 相关性分数：{item['score']}")
        lines.append("")
    source_note = {
        "tavily_api": "以上结果来自真实 Tavily Search API。",
        "mock": "当前为 mock 数据，仅用于离线演示，不代表实时互联网搜索结果。",
        "fallback": "真实 Tavily API 失败后已降级，以上结果不能作为实时互联网资料依据。",
    }.get(source, "数据来源以本报告后续 Evidence 和 Metadata 为准。")
    lines.extend([f"> **数据来源说明：** {source_note}", ""])
    return lines


def _learning_route_final_answer(records: list[dict[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for record in records:
        if not record["success"] or record["tool_name"] not in {"file_reader", "tavily_search"}:
            continue
        output = record["output"]
        if record["tool_name"] == "file_reader":
            snippets.append(str(output.get("content") or "").strip())
        else:
            snippets.extend(str(item.get("clean_content") or item.get("content") or "").strip() for item in output.get("results") or [] if isinstance(item, dict))
    snippets = [snippet.replace("\n", " ")[:500] for snippet in snippets if snippet]
    learning_terms = (
        "python", "pytorch", "machine learning", "deep learning", "transformer",
        "attention", "tokenizer", "prompt", "微调", "预训练", "机器学习", "深度学习",
        "学习路线", "学习路径", "课程", "大模型", "llm",
    )
    relevant = []
    for snippet in snippets:
        if any(term in snippet.lower() for term in learning_terms) and snippet not in relevant:
            relevant.append(snippet)
    if len(relevant) < 3:
        return [
            "本次未获得足够证据来生成完整、可信的 LLM 学习路线。",
            "",
            "当前检索结果主要来自项目内部工程文档，不足以支撑完整 LLM 学习路线；建议接入 Tavily 或补充 LLM 学习资料语料后重新运行。",
            "",
        ]
    stage_rules = [
        ("基础准备", ("python", "machine learning", "deep learning", "机器学习", "深度学习", "pytorch")),
        ("核心原理", ("transformer", "attention", "tokenizer", "预训练", "微调")),
        ("大模型应用", ("prompt", "agent", "function calling", "工具调用")),
        ("工程实践", ("fastapi", "向量数据库", "部署", "日志", "监控", "评测")),
        ("项目实战", ("项目", "实战", "问答", "报告生成")),
    ]
    lines = ["以下是基于本次成功检索证据整理的 LLM 学习路线：", ""]
    used: set[str] = set()
    for index, (stage_name, terms) in enumerate(stage_rules, 1):
        snippet = next(
            (
                candidate
                for candidate in relevant
                if candidate not in used and any(term in candidate.lower() for term in terms)
            ),
            None,
        )
        if snippet:
            used.add(snippet)
            detail = snippet
        else:
            detail = "本次成功工具结果未提供足够证据，建议补充该阶段资料后再细化。"
        lines.append(f"{index}、**{stage_name}阶段：** {detail}")
        lines.append("")
    lines.append("> 以上路线仅归纳本次工具实际返回的资料；详细来源见后续“证据与工具观察结果”。")
    lines.append("")
    return lines



# ── Phase A: LLM-Synthesized Answer ──────────────────────────────────────────

_SYNTHESIS_SYSTEM = """你是专业调研报告撰写人。请基于工具采集的证据，
为给定的调研任务生成一份结构清晰、有来源标注的中文回答。

要求：
- 不少于 300 字，条理清晰
- 每个关键结论后必须标注真实来源标题和 URL，格式为：来源：标题（URL）
- 不要只写来源：[工具名]，工具名不能替代真实 URL
- 如果某个工具返回空结果，明确说明"未找到相关证据"，不要编造
- 不要重复输出证据原文，用自己的语言综合表达
- 忽略网页导航、登录、分享、联系我们、重复菜单等页面壳文本
- 语气专业，适合调研报告"""

_SYNTHESIS_USER_TMPL = """调研任务：{task}

工具采集的证据：
{evidence}

请基于以上证据撰写综合回答："""


def _llm_synthesize_answer(
    task: str,
    observations: list[dict[str, Any]],
    llm_client: "LLMClient",
    provenance_bundle: dict[str, Any] | None = None,
    usage_callback: Callable[[Any], None] | None = None,
) -> str | None:
    """Call LLM to synthesize tool evidence into a coherent answer.
    Returns synthesized text, or None if LLM call fails / no useful evidence.
    """
    if LLMClient is None or not llm_client.is_available():
        return None
    if not has_useful_evidence(observations):
        return None
    evidence = (
        _provenance_llm_context(provenance_bundle)
        if provenance_bundle
        else compress_evidence(observations, max_total_chars=5000)
    )
    if not evidence.strip():
        return None
    messages = [
        LLMMessage(role="system", content=_SYNTHESIS_SYSTEM),
        LLMMessage(
            role="system",
            content=(
                "When CIT-* identifiers are present, every factual conclusion must cite one or more "
                "of those exact identifiers in square brackets. Never invent a citation identifier. "
                "When conflict_status is unresolved or requires_human, state the conflict explicitly "
                "and do not select one side as a definitive fact."
            ),
        ),
    ]
    # ── Phase 9: Few-shot injection ──
    try:
        from app.improvement.few_shot import load_few_shot_examples, format_few_shot_for_prompt
        from app.improvement.evaluator import _classify_question
        category = _classify_question(task)
        examples = load_few_shot_examples(category=category, max_examples=1)
        if examples:
            messages.append(
                LLMMessage(
                    role="system",
                    content=format_few_shot_for_prompt(examples),
                )
            )
    except Exception:
        pass
    messages.append(
        LLMMessage(
            role="user",
            content=_SYNTHESIS_USER_TMPL.format(task=task, evidence=evidence),
        ),
    )
    try:
        response = llm_client.complete(messages)
        if usage_callback is not None and response.usage is not None:
            usage_callback(response)
        if response.success and response.content:
            content = response.content.strip()
            if provenance_bundle and not _valid_synthesis_citations(content, provenance_bundle):
                return None
            return content
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("LLM synthesis failed: %s", redact_text(exc))
    return None


def _provenance_llm_context(bundle: dict[str, Any]) -> str:
    passages = {item["passage_id"]: item for item in bundle.get("passages") or []}
    report_claims = {
        item["report_claim_id"]: item for item in bundle.get("report_claims") or []
    }
    resolutions = {
        item["claim_id"]: item for item in bundle.get("resolutions") or []
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for citation in bundle.get("citations") or []:
        claim = report_claims.get(citation.get("report_claim_id"))
        passage = passages.get(citation.get("passage_id"))
        if not claim or not passage:
            continue
        grouped.setdefault(claim["report_claim_id"], []).append(
            {
                "citation_id": citation.get("citation_label"),
                "passage_id": passage.get("passage_id"),
                "text": str(passage.get("text") or "")[:1200],
                "locator": passage.get("locator"),
                "trace_id": passage.get("trace_id"),
            }
        )
    payload = {
        "schema_version": bundle.get("schema_version"),
        "claims": [
            {
                "claim": claim.get("claim_text"),
                "conflict_status": (resolutions.get(claim.get("claim_id")) or {}).get("status"),
                "confidence": (resolutions.get(claim.get("claim_id")) or {}).get("confidence"),
                "citations": grouped.get(claim_id, []),
            }
            for claim_id, claim in report_claims.items()
            if grouped.get(claim_id)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)[:7000]


def _valid_synthesis_citations(content: str, bundle: dict[str, Any]) -> bool:
    available = {
        str(item.get("citation_label"))
        for item in bundle.get("citations") or []
        if item.get("citation_label")
    }
    used = set(re.findall(r"CIT-\d{3}-\d{2}", content))
    return bool(used) and used.issubset(available)


def _repair_synthesis_citations(content: str, bundle: dict[str, Any]) -> str:
    """Mark invalid references as unsupported; never invent a source mapping."""
    available = {
        str(item.get("citation_label"))
        for item in bundle.get("citations") or []
        if item.get("citation_label")
    }
    return re.sub(r"\[?(CIT-\d{3}-\d{2})\]?",
                  lambda match: match.group(0) if match.group(1) in available else "[引用无效，待核验]",
                  content)


def _render_provenance_markdown(bundle: dict[str, Any] | None) -> list[str]:
    if not bundle:
        return []
    passages = {item["passage_id"]: item for item in bundle.get("passages") or []}
    report_claims = {
        item["report_claim_id"]: item for item in bundle.get("report_claims") or []
    }
    citations_by_claim: dict[str, list[dict[str, Any]]] = {}
    for citation in bundle.get("citations") or []:
        citations_by_claim.setdefault(str(citation.get("report_claim_id")), []).append(citation)
    lines = [
        "## 7. Claim Provenance V2",
        "",
        f"* Schema: `{bundle.get('schema_version')}`",
        f"* Extractor: `{bundle.get('extractor_version')}`",
        f"* Citation integrity: `{(bundle.get('integrity') or {}).get('all_citations_resolve')}`",
        "",
    ]
    for claim_id, claim in sorted(
        report_claims.items(),
        key=lambda item: int(item[1].get("ordinal") or 0),
    ):
        lines.extend([f"### {claim.get('claim_text')}", ""])
        claim_citations = citations_by_claim.get(claim_id, [])
        if not claim_citations:
            lines.extend(["* No supporting citation was materialized.", ""])
            continue
        for citation in claim_citations:
            passage = passages.get(citation.get("passage_id")) or {}
            locator = json.dumps(passage.get("locator") or {}, ensure_ascii=False, default=str)
            lines.extend(
                [
                    f"* [{citation.get('citation_label')}] passage=`{citation.get('passage_id')}` "
                    f"trace=`{passage.get('trace_id') or '<none>'}`",
                    f"  Locator: `{locator}`",
                    f"  Evidence: {str(passage.get('text') or '')[:500]}",
                ]
            )
        lines.append("")
    return lines


def _render_tier_distribution(
    bundle: dict[str, Any] | None,
    plan: dict[str, Any],
) -> list[str]:
    """Phase 8.1: Render source tier distribution and quota analysis."""
    if not bundle:
        return []
    documents = [doc for doc in bundle.get("source_documents") or []
                 if (doc.get("metadata") or {}).get("research_eligible")
                 and not (doc.get("metadata") or {}).get("is_mock")
                 and not (doc.get("metadata") or {}).get("is_fallback")]
    documents = list({doc.get("canonical_uri"): doc for doc in documents}.values())
    if not documents:
        return []

    # Count tiers
    tier_counts: dict[str, int] = {"T0": 0, "T1": 0, "T2": 0}
    for doc in documents:
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        tier = metadata.get("source_tier", "T2") if isinstance(metadata, dict) else "T2"
        if tier in tier_counts:
            tier_counts[tier] += 1

    total = sum(tier_counts.values())
    if total == 0:
        return []

    profile_constraints = plan.get("profile_constraints") or {}
    min_t0 = profile_constraints.get("min_t0_sources", 1)

    lines = [
        "## 10. 信源层级分布",
        "",
        "| 层级 | 含义 | 数量 | 占比 |",
        "|------|------|------|------|",
    ]
    for tier in ("T0", "T1", "T2"):
        count = tier_counts[tier]
        pct = f"{count / total * 100:.0f}%" if total > 0 else "0%"
        badge = TIER_BADGES.get(tier, tier)
        label = TIER_LABELS.get(tier, tier)
        lines.append(f"| {badge} | {label} | {count} | {pct} |")

    lines.append("")

    # Profile quota
    if profile_constraints:
        profile_name = plan.get("retrieval_profile", "generic")
        shortfall = max(0, min_t0 - tier_counts["T0"])
        lines.extend([
            f"* **检索配置**：`{profile_name}`",
            f"* **T0 最低要求**：{min_t0}，**实际达成**：{tier_counts['T0']}",
        ])
        if shortfall > 0:
            shortfall_policy = profile_constraints.get("shortfall_policy", "report_only")
            lines.extend([
                f"* **⚠️ T0 缺口**：缺少 {shortfall} 个 T0 来源（策略：`{shortfall_policy}`）",
                "",
                "> 本报告的部分结论可能仅依赖非一手来源，请在使用时注意交叉验证。",
            ])
        else:
            lines.append("* ✅ T0 来源满足最低要求")
        lines.append("")

    # T2-only claims detection
    t2_only_claims: list[str] = []
    report_claims = {rc["report_claim_id"]: rc for rc in bundle.get("report_claims") or []}
    passages = {p["passage_id"]: p for p in bundle.get("passages") or []}
    for citation in bundle.get("citations") or []:
        claim_id = citation.get("report_claim_id", "")
        passage_id = citation.get("passage_id", "")
        passage = passages.get(passage_id) or {}
        pmeta = passage.get("metadata") or {}
        if isinstance(pmeta, str):
            try:
                pmeta = json.loads(pmeta)
            except Exception:
                pmeta = {}
        tier = pmeta.get("source_tier", "T2") if isinstance(pmeta, dict) else "T2"
        if tier == "T2" and claim_id and claim_id not in t2_only_claims:
            claim = report_claims.get(claim_id) or {}
            claim_text = str(claim.get("claim_text") or "")[:120]
            if claim_text:
                t2_only_claims.append(claim_text)

    if t2_only_claims:
        lines.append("### 仅由 T2 来源支撑的结论")
        lines.append("")
        lines.append("> 以下结论的支撑证据仅来自社区/个人来源（T2），建议在关键决策中谨慎使用：")
        lines.append("")
        for i, claim_text in enumerate(t2_only_claims[:10], 1):
            lines.append(f"{i}. {claim_text}")
        lines.append("")

    return lines


def _render_citation_index(bundle: dict[str, Any] | None) -> list[str]:
    """Build a citation reference index for inline citation badges.

    Each entry maps citation_label → passage text + source info + relation.
    """
    if not bundle:
        return []
    passages = {item["passage_id"]: item for item in bundle.get("passages") or []}
    citations = bundle.get("citations") or []
    if not citations:
        return []

    lines = [
        "## 9. 引用索引",
        "",
        "| 引用编号 | 信源层级 | 关系 | 证据质量 | 来源 | 原文片段 |",
        "|----------|----------|------|----------|------|----------|",
    ]

    for citation in citations:
        label = citation.get("citation_label", "?")
        passage = passages.get(citation.get("passage_id")) or {}
        passage_text = str(passage.get("text") or "")[:120].replace("\n", " ").replace("|", "\\|")
        cb = _content_basis_label(passage)
        source_uri = str(passage.get("locator", {}).get("uri") or "")
        source_display = source_uri[:60] if source_uri else "—"
        tier_meta = (passage.get("metadata") or {})
        if isinstance(tier_meta, str):
            try:
                tier_meta = json.loads(tier_meta)
            except Exception:
                tier_meta = {}
        tier = tier_meta.get("source_tier", "T2") if isinstance(tier_meta, dict) else "T2"
        tier_badge = TIER_BADGES.get(tier, "🟠 T2")

        # Find relation from edges
        edge_id = citation.get("edge_id")
        relation = "supports"
        for edge in bundle.get("edges") or []:
            if edge.get("edge_id") == edge_id:
                relation = edge.get("relation", "supports")
                break
        relation_icon = {"supports": "✅", "refutes": "❌", "contextualizes": "ℹ️"}.get(relation, "—")

        lines.append(
            f"| [{label}] | {tier_badge} | {relation_icon} {relation} | {cb} | {source_display} | {passage_text} |"
        )

    lines.append("")
    return lines


def _conflict_alert_lines(bundle: dict[str, Any] | None) -> list[str]:
    if not bundle:
        return []
    claims = {item.get("claim_id"): item for item in bundle.get("claims") or []}
    disputed = [
        item
        for item in bundle.get("resolutions") or []
        if item.get("status") in {"unresolved", "requires_human"}
    ]
    if not disputed:
        return []
    lines = ["> **冲突提示：** 以下结论存在未解决的高影响证据冲突，不得作为确定性事实使用："]
    for resolution in disputed:
        claim = claims.get(resolution.get("claim_id")) or {}
        lines.append(
            f"> * {claim.get('claim_text') or resolution.get('claim_id')} "
            f"(status=`{resolution.get('status')}`, confidence=`{resolution.get('confidence')}`)"
        )
    return [*lines, ""]


def _render_reasoning_markdown(bundle: dict[str, Any] | None) -> list[str]:
    if not bundle or not bundle.get("reasoning"):
        return []
    claims = {item.get("claim_id"): item for item in bundle.get("claims") or []}
    score_by_edge = {
        item.get("edge_id"): item for item in bundle.get("reliability_scores") or []
    }
    edges_by_claim: dict[str, list[dict[str, Any]]] = {}
    for edge in bundle.get("edges") or []:
        edges_by_claim.setdefault(str(edge.get("claim_id")), []).append(edge)
    reasoning = bundle["reasoning"]
    lines = [
        "## 8. 可靠性、冲突与限制",
        "",
        f"* 策略版本: `{reasoning.get('policy_version')}`",
        f"* 策略哈希: `{reasoning.get('policy_hash')}`",
        "",
    ]
    for resolution in bundle.get("resolutions") or []:
        claim_id = str(resolution.get("claim_id"))
        claim = claims.get(claim_id) or {}
        status = str(resolution.get("status"))
        gate = (resolution.get("rationale") or {}).get("quality_gate") or {}
        lines.extend(
            [
                f"### {claim.get('claim_text') or claim_id}",
                "",
                f"* 冲突状态: `{status}`",
                f"* 聚合置信度: `{resolution.get('confidence')}`",
                f"* 独立支持/反驳来源: `{resolution.get('independent_support_count')}` / "
                f"`{resolution.get('independent_refute_count')}`",
                f"* 高置信质量门禁: `{'passed' if gate.get('passed') else 'not_passed'}`",
            ]
        )
        if status in {"unresolved", "requires_human"}:
            lines.append("* **结论限制：该冲突尚未解决，报告不得选择单一确定答案。**")
        for edge in edges_by_claim.get(claim_id, []):
            score = score_by_edge.get(edge.get("edge_id")) or {}
            lines.append(
                f"* `{edge.get('relation')}` score=`{score.get('total_score')}` "
                f"source_class=`{score.get('source_class')}` "
                f"cluster=`{score.get('source_cluster_id')}`"
            )
        lines.append("")
    return lines


def _source_references(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(title: Any, url: Any) -> None:
        link = str(url or "").strip()
        if not link or not link.startswith(("http://", "https://")) or link in seen:
            return
        seen.add(link)
        label = str(title or link).strip()[:120]
        references.append((label, link))

    for record in records:
        if not record.get("success"):
            continue
        output = record.get("output") if isinstance(record.get("output"), dict) else {}
        tool_name = str(record.get("tool_name") or "")
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if tool_name in {"tavily_search", "mcp_github_search"}:
            for item in output.get("results") or []:
                if not isinstance(item, dict):
                    continue
                title = (
                    item.get("title")
                    or item.get("full_name")
                    or item.get("name")
                    or item.get("url")
                )
                add(title, item.get("url") or item.get("html_url"))
        elif metadata.get("tool_source") == "mcp_remote":
            direct_title = (
                output.get("title")
                or output.get("name")
                or metadata.get("remote_registry_name")
                or tool_name
            )
            add(direct_title, output.get("url") or output.get("source_url") or output.get("html_url"))
            for item in _remote_result_lists(output):
                add(_remote_title(item), _remote_url(item))
    return references


def _source_reference_lines(records: list[dict[str, Any]]) -> list[str]:
    references = _source_references(records)
    if not references:
        return []
    lines = ["### 主要来源", ""]
    lines.extend(f"* [{title}]({url})" for title, url in references[:10])
    lines.append("")
    return lines


def _repair_tool_only_sources(answer: str, records: list[dict[str, Any]]) -> str:
    references = _source_references(records)
    if not references:
        return answer
    title, url = references[0]
    replacement = f"来源：{title}（{url}）"
    tool_names = [
        re.escape(str(record.get("tool_name") or ""))
        for record in records
        if record.get("tool_name")
        and (
            str(record.get("tool_name")) in {"tavily_search", "mcp_github_search"}
            or (isinstance(record.get("metadata"), dict) and record["metadata"].get("tool_source") == "mcp_remote")
        )
    ]
    pattern = "|".join(sorted(set(tool_names), key=len, reverse=True))
    if not pattern:
        return answer
    repaired = re.sub(rf"来源[:：]\s*\[`?(?:{pattern})`?\]", replacement, answer)
    repaired = re.sub(rf"来源[:：]\s*(?:{pattern})\b", replacement, repaired)
    return repaired


def _render_final_answer(
    task: str, observations: list[dict[str, Any]], traces: list[ToolTrace]
) -> list[str]:
    """Build a user-facing answer strictly from successful tool evidence."""

    records = [record for record in _evidence_records(observations, traces) if is_research_record(record)]
    successful = [record for record in records if record["success"]]
    requested_limit = _requested_result_count(task, 5)

    # Handle ReAct finish action as first-class answer
    finish_record = next(
        (r for r in successful if r["tool_name"] == "finish"), None
    )
    if finish_record:
        raw_summary = (
            finish_record.get("output", {}).get("summary")
            or finish_record.get("summary")
            or ""
        )
        if raw_summary.strip():
            return [
                raw_summary.strip(),
                "",
                "> **说明：** 本回答由 ReAct 模式 LLM 直接基于知识生成，未调用外部工具。"
                "如需工具验证，请使用包含 Web、GitHub 或本地文件工具的场景模板重新提问。",
                "",
            ]

    github_record = next(
        (r for r in successful if r["tool_name"] == "mcp_github_search"), None
    )
    tavily_record = next(
        (r for r in successful if r["tool_name"] == "tavily_search"), None
    )
    task_lower = task.lower()
    learning_route = any(
        term in task_lower
        for term in ("学习路线", "学习路径", "roadmap", "curriculum")
    )

    if _is_github_trending_task(task_lower):
        answer = _github_trending_final_answer(successful, task, requested_limit)
    elif github_record:
        answer = _github_final_answer(github_record, task)
    elif learning_route:
        answer = _learning_route_final_answer(records)
    elif tavily_record:
        answer = _tavily_final_answer(tavily_record, task)
    else:
        answer = None

    if not answer:
        summaries = [f"{item.title}：{item.snippet}（来源：{item.source_ref}）"
                     for record in successful for item in _items_from_record("report", record, 0)
                     if is_eligible_evidence(item)]
        if summaries:
            answer = ["以下是根据本次成功工具结果整理的内容：", ""]
            answer.extend(
                f"{i}、{s}" for i, s in enumerate(summaries[:8], 1)
            )
            answer.append("")
        else:
            _knowledge_keywords = [
                "什么", "是什么", "什么是", "怎么", "为什么",
                "how", "what is", "explain", "define",
            ]
            _data_keywords = [
                "github", "repo", "仓库", "数据库", "文件",
                "搜索", "查询", "star", "search",
            ]
            _task_lower = task.lower()
            _is_knowledge_q = (
                any(kw in _task_lower for kw in _knowledge_keywords)
                and not any(kw in _task_lower for kw in _data_keywords)
            )
            if _is_knowledge_q:
                answer = [
                    "⚠️ 当前已启用的工具无法回答通识性问题。",
                    "",
                    f"「{task}」是一个知识性问题，建议：",
                    "1. 切换到包含 `tavily_search` 的联网调研场景；",
                    "2. 或者切换到包含 `file_reader` 的场景，直接读取相关文档；",
                    "3. 或者直接向 LLM 提问，不走 Agent 工具流程。",
                    "",
                ]
            else:
                answer = ["本次未获得足够证据，无法生成可信的最终答案。", ""]

    failed = [r for r in records if not r["success"]]
    if failed:
        details = []
        for r in failed[:5]:
            reason = (
                r.get("error_message") or r.get("summary") or "工具未返回成功结果"
            )
            details.append(f"第 {r.get('step_no')} 步 `{r['tool_name']}`：{reason}")
        answer.extend(
            [
                "> **完成限制：** 本次任务部分完成，结论仅基于已成功的工具结果。",
                *[f"> * {d}" for d in details],
                "",
            ]
        )

    limit_text = " ".join(
        str(r.get("error_message") or r.get("summary") or "") for r in records
    )
    if "same_tool_max_calls" in limit_text:
        answer.extend(
            [
                "> **安全保护说明：** ReAct 模式连续选择同一工具达到调用上限，"
                "系统停止继续调用，这是安全保护，不是程序崩溃。",
                "",
            ]
        )

    return answer or ["本次执行未产生可用证据，请检查工具配置后重试。", ""]


def _runtime_limitations(plan: dict[str, Any]) -> list[str]:
    planner_source = plan.get("planner_source") or "deterministic"
    if planner_source == "llm":
        planner_lines = [
            "本次运行启用了 LLM Planner。",
            "系统仍保留 deterministic fallback，以提高运行可靠性。",
            "本报告基于工具观察结果和持久化 Trace 生成。",
        ]
    elif planner_source == "deterministic_fallback":
        planner_lines = [
            "本次运行尝试了 LLM Planner，但最终使用 deterministic fallback。",
            "如有可用信息，降级原因记录在规划备注中。",
            "本报告基于工具观察结果和持久化 Trace 生成。",
        ]
    else:
        planner_lines = [
            "本次运行使用 deterministic planner。",
            "本次运行未启用 LLM 规划。",
            "本报告基于工具观察结果和持久化 Trace 生成。",
        ]
    execution_mode = plan.get("execution_mode") or "planned"
    react_state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
    if execution_mode == "react":
        planner_lines += [
            "ReAct 决策受 max_steps 和 same_tool_max_calls 限制。",
            "Thought 仅保存简短决策理由，不保存模型的长篇原始推理。",
        ]
    if react_state.get("fallback_used"):
        planner_lines.append("ReAct 的 fallback_used=true，运行由持久化的 planned executor 完成。")
    if react_state.get("completed_with_limitation"):
        planner_lines.append("ReAct 已生成带限制说明的报告，限制原因记录在决策过程和 Trace 中。")
    finish_reason = str(react_state.get("finish_reason") or "")
    observations = react_state.get("observation_history") or []
    same_tool_limited = "same_tool_max_calls" in finish_reason or any(
        "same_tool_max_calls" in str(item.get("error_message") or "")
        for item in observations
        if isinstance(item, dict)
    )
    if same_tool_limited:
        limited_tool = next(
            (
                str(item.get("action"))
                for item in reversed(observations)
                if isinstance(item, dict)
                and "same_tool_max_calls" in str(item.get("error_message") or "")
            ),
            "同一工具",
        )
        planner_lines.append(
            f"ReAct 模式连续多次选择 {limited_tool}，达到 same_tool_max_calls 上限后"
            "停止继续调用该工具，并生成 limitation report。"
        )
    return planner_lines + [
        "GitHub/MCP 工具遵循只读边界，并支持 mock/public_api 模式；mock 数据仅用于离线演示。",
        "HITL 是最小化人工确认流程，不是生产级权限系统。",
        "运行时报告和索引是本地 ignored artifacts，不进入版本控制。",
    ]


def _render_grouped_final_answer(
    task: str,
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
    groups: list[SubQueryGroup],
) -> list[str]:
    """Build a final answer organized by sub-query groups with citation labels."""
    lines: list[str] = []
    records = _evidence_records(observations, traces)
    successful = [r for r in records if r["success"]]

    for gi, group in enumerate(groups, 1):
        sq_label = group.sub_query or f"主题 {gi}"
        lines.extend([f"### 子问题 {gi}: {sq_label}", ""])

        # Collect findings from this group's claims
        for claim in group.claims:
            claim_text = str(claim.get("claim_text") or "")
            # Find citations for this claim
            claim_citations = [
                c for c in group.citations
                if str(c.get("report_claim_id")) == str(claim.get("report_claim_id"))
            ]
            citation_labels = [
                str(c.get("citation_label") or "")
                for c in claim_citations
                if c.get("citation_label")
            ]
            cit_str = "[" + "][".join(citation_labels) + "]" if citation_labels else ""

            # Find content_basis for the passages backing this claim
            cb_labels: set[str] = set()
            for cit in claim_citations:
                for p in group.passages:
                    if str(p.get("passage_id")) == str(cit.get("passage_id")):
                        cb_labels.add(_content_basis_label(p))

            cb_suffix = ""
            if cb_labels:
                cb_suffix = f" （{' / '.join(sorted(cb_labels))}）"

            lines.append(f"**发现：** {claim_text}{cit_str}{cb_suffix}")
            lines.append("")

        # Show supporting evidence snippets
        if group.passages:
            lines.append("**支撑证据：**")
            lines.append("")
            for p in group.passages[:5]:
                text = str(p.get("text") or "")[:300]
                cb = _content_basis_label(p)
                trace_id = str(p.get("trace_id") or "")[:12]
                lines.append(f"* {cb} `{trace_id}…` — {text}")
            lines.append("")

    # If no groups have claims, fall back to legacy answer
    if not any(g.claims for g in groups):
        return _render_final_answer(task, observations, traces)

    return lines


def generate_markdown_report(
    run: AgentRun,
    plan: dict[str, Any],
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
    llm_client: "LLMClient | None" = None,
    provenance_bundle: dict[str, Any] | None = None,
    report_type: str = "summary",
    usage_callback: Callable[[Any], None] | None = None,
    citation_validation_callback: Callable[[Any], None] | None = None,
    reference_verification_callback: Callable[[Any], None] | None = None,
) -> str:
    """Build a Markdown report from persisted run evidence.

    Phase A: if llm_client is provided and available, the 「3. 最终回答」section
    is generated by LLM synthesis of tool evidence instead of template rules.
    Falls back to template automatically if LLM is unavailable or call fails.

    Phase 3: when sub-query groups exist, the answer section is organized by
    sub-query with citation labels and content_basis annotations.

    Phase 6: report_type controls output detail:
      - "summary" (default): existing full report
      - "detailed_report": full report + auto-generated TOC after §2
      - "outline_report": header + TOC + section headings only (fast preview)
    """

    degradation_label, degradation_note = _degradation_state(plan, traces)
    execution_mode = plan.get("execution_mode") or "planned"
    requested_execution_mode = plan.get("requested_execution_mode") or execution_mode

    # ── Phase 3: Build sub-query groups ─────────────────────────────────
    sub_query_groups = _build_sub_query_groups(plan, traces, provenance_bundle)
    content_basis_map = _build_content_basis_map(provenance_bundle)

    lines: list[str] = [
        "# Traceable Research Agent 调研报告",
        "",
        "## 1. 任务说明",
        "",
        run.task,
        "",
        "## 2. 运行摘要",
        "",
        f"* 执行模式 (`execution_mode`): `{execution_mode}`",
        f"* 请求执行模式 (`requested_execution_mode`): `{requested_execution_mode}`",
        f"* 降级状态: `{degradation_label}`",
        f"* 降级说明: {degradation_note}",
        "",
    ]
    outcome = plan.get("research_outcome") or {}
    if outcome.get("warnings"):
        lines.extend(["### 本次研究限制", "", *[f"* {warning}" for warning in outcome["warnings"]], ""])
    lines.extend(["* 生成方式：" + ("LLM 综合" if llm_client else "本地规则报告，非 LLM 综合"), ""])

    # ── Phase A: LLM synthesis if available, else template ──────────────────
    _llm_answer: str | None = None
    if llm_client is not None:
        _llm_answer = _llm_synthesize_answer(
            run.task,
            observations,
            llm_client,
            provenance_bundle,
            usage_callback,
        )
        if not _llm_answer and plan.get("research_outcome"):
            raise ValueError("report_synthesis_failed: LLM returned no usable, correctly cited report; retry after checking provider and evidence.")
        if _llm_answer:
            _llm_answer = _repair_tool_only_sources(
                _llm_answer,
                _evidence_records(observations, traces),
            )

    # ── Phase 3: Grouped answer when sub-query groups exist ─────────────
    if sub_query_groups and len(sub_query_groups) > 1 and not _llm_answer:
        _final_answer_lines = _render_grouped_final_answer(
            run.task, observations, traces, sub_query_groups,
        )
    elif _llm_answer:
        _final_answer_lines = [
            _llm_answer, "",
            *_source_reference_lines(_evidence_records(observations, traces)),
            "> **生成方式：** 本回答由 LLM 综合工具证据生成，各来源已标注。", "",
        ]
    else:
        _final_answer_lines = _render_final_answer(run.task, observations, traces) or []

    _final_answer_lines.extend(_conflict_alert_lines(provenance_bundle))

    lines += [
        "## 3. 最终回答",
        "",
        *_final_answer_lines,
        "## 4. 执行计划",
        "",
    ]

    for step in plan.get("steps", []):
        lines.extend(
            [
                f"### 步骤 {step.get('step_no')}: {step.get('tool_name')}",
                "",
                f"* 目标 (`goal`): {step.get('goal')}",
                f"* 参数 (`arguments`): `{json.dumps(step.get('arguments', {}), ensure_ascii=False)}`",
                f"* 完成标准 (`completion_criteria`): {step.get('completion_criteria')}",
                "",
            ]
        )

    notes = plan.get("notes") or []
    if notes:
        lines.extend(["### 规划备注", ""])
        lines.extend([f"* {note}" for note in notes])
        lines.append("")
    if not plan.get("steps"):
        lines.extend(["未生成可执行的计划步骤。", ""])

    confirmation = plan.get("confirmation")
    if isinstance(confirmation, dict) and confirmation:
        lines.extend(
            [
                "### 人工确认",
                "",
                f"* 需确认步骤 (`required_step_no`): {confirmation.get('required_step_no')}",
                f"* 需确认工具 (`required_tool_name`): {confirmation.get('required_tool_name')}",
                f"* 是否批准 (`approved`): `{confirmation.get('approved')}`",
                f"* 确认意见 (`comment`): {confirmation.get('comment') or '<none>'}",
                f"* 批准时间 (`approved_at`): {confirmation.get('approved_at') or '<none>'}",
                "",
            ]
        )

    react_state = plan.get("react_state")
    react_observations = (
        react_state.get("observation_history")
        if isinstance(react_state, dict)
        else None
    )
    if react_observations:
        lines.extend(["## 5. ReAct 决策过程", ""])
        for observation in react_observations:
            lines.extend(
                [
                    f"### ReAct 步骤 {observation.get('step_no')}",
                    "",
                    f"* Thought（简短决策理由）: {str(observation.get('thought') or '<none>')[:500]}",
                    f"* Action（选择工具）: `{observation.get('action') or '<none>'}`",
                    f"* Observation（工具观察）: {observation.get('observation_summary') or '<none>'}",
                    f"* 是否成功: `{observation.get('success')}`",
                    f"* 错误信息: {observation.get('error_message') or '<none>'}",
                    "",
                ]
            )

    lines.extend(["## 6. 证据与工具观察结果", ""])
    if observations:
        for observation in observations:
            tool_name = str(
                observation.get("tool_name") or observation.get("action") or "unknown"
            )
            output_summary = (
                observation.get("output_summary")
                or observation.get("observation_summary")
            )
            # ── Phase 3: content_basis annotation ──────────────────────
            trace_id = str(observation.get("trace_id") or "")
            cb_label = content_basis_map.get(trace_id, "")
            cb_line = [f"* 证据质量 (`content_basis`): {cb_label}", ""] if cb_label else []

            lines.extend(
                [
                    f"### 步骤 {observation.get('step_no')}: {tool_name}",
                    "",
                    f"* 是否成功 (`success`): `{observation.get('success')}`",
                    f"* 输出摘要 (`output_summary`): {output_summary or '<none>'}",
                    f"* 错误信息 (`error_message`): {observation.get('error_message') or '<none>'}",
                    *cb_line,
                    "关键证据片段：",
                    "",
                    "```text",
                    _selected_evidence(
                        tool_name,
                        observation.get("output"),
                    ),
                    "```",
                    "",
                ]
            )
            metadata = _observation_metadata(observation)
            data_source = metadata.get("data_source")
            if tool_name == "mcp_github_search":
                source_notes = {
                    "public_api": "> **数据来源：** 当前结果来自真实 GitHub Public API。",
                    "cache": "> **数据来源：** 当前结果来自此前 GitHub Public API 请求的本地缓存。",
                    "mock": (
                        "> **离线演示说明：** 当前 GitHub 结果来自 mock 离线数据，"
                        "仅用于离线演示，不代表真实 GitHub star 排名。"
                    ),
                    "fallback": (
                        "> **降级说明：** 真实 GitHub API 请求失败后已降级为 mock 数据，"
                        "不能作为真实 GitHub star 排名依据。"
                    ),
                }
                if data_source in source_notes:
                    lines.extend([source_notes[data_source], ""])
            elif tool_name == "tavily_search":
                if data_source == "tavily_api":
                    lines.extend(["> **数据来源：** 当前结果来自真实 Tavily Search API。", ""])
                elif data_source in {"mock", "fallback"}:
                    lines.extend(
                        [
                            "> **离线/降级说明：** 当前 Tavily 结果不是实时 API 数据，"
                            "仅可作为离线演示证据。",
                            "",
                        ]
                    )
            if metadata:
                parallel_lines = _parallel_metadata_lines(metadata)
                if parallel_lines:
                    lines.extend(parallel_lines)
                lines.extend(
                    [
                        "原始 Metadata（JSON key 保持不变）：",
                        "",
                        "```json",
                        _json_preview(metadata, max_chars=1600),
                        "```",
                        "",
                    ]
                )
    else:
        lines.extend(["未记录可执行工具的观察结果。", ""])

    lines.extend(_render_citation_index(provenance_bundle))
    lines.extend(_render_provenance_markdown(provenance_bundle))
    lines.extend(_render_reasoning_markdown(provenance_bundle))

    problem_traces = [trace for trace in traces if trace.status in {"failed", "rejected"}]
    if problem_traces:
        lines.extend(["", "## 9. 失败与拒绝详情", ""])
        for trace in problem_traces:
            trace_metadata = _trace_metadata(trace)
            lines.extend(
                [
                    f"### 步骤 {trace.step_no}: {trace.tool_name}",
                    "",
                    f"* 状态 (`status`): `{trace.status}`",
                    f"* 类型: {_failure_category(trace.error_message, trace_metadata)}",
                    f"* 错误说明: {_friendly_report_error(trace.error_message)}",
                    f"* 原始错误 (`error_message`): {trace.error_message or '<none>'}",
                    f"* 输出摘要 (`output_summary`): {trace.output_summary or '<none>'}",
                    "",
                ]
            )

    # ── Phase 6: Limitations section (claim verification pass) ────────────
    if provenance_bundle:
        limitations_lines = _render_limitations_section(provenance_bundle)
        if limitations_lines:
            lines.extend(limitations_lines)

    # Outline reports intentionally contain headings only and therefore have
    # no inline citations to validate.
    if report_type == "outline_report":
        return _to_outline(lines)

    # ── Phase 7.5: Citation validation ────────────────────────────────────
    if provenance_bundle:
        try:
            from app.config import settings as _reporter_settings
            if _reporter_settings.citation_validation_enabled:
                from app.evidence.citation_validator import (
                    render_citation_validation_section,
                    validate_citations,
                )
                validation_llm_client = llm_client
                if _reporter_settings.citation_validation_llm_enabled:
                    if validation_llm_client is None or not validation_llm_client.is_available():
                        from app.llm.providers import create_llm_client

                        validation_llm_client = create_llm_client(_reporter_settings)
                # Validate actual answer claims, not the citation index quoting sources.
                report_text = "\n".join(_final_answer_lines)
                citation_validation_report = validate_citations(
                    report_text,
                    provenance_bundle,
                    min_supported_overlap=0.15,
                    min_weak_overlap=0.05,
                    llm_client=validation_llm_client,
                    use_llm=_reporter_settings.citation_validation_llm_enabled,
                )
                if citation_validation_callback is not None:
                    citation_validation_callback(citation_validation_report)
                validation_lines = render_citation_validation_section(
                    citation_validation_report,
                )
                if validation_lines:
                    lines.extend(validation_lines)
        except Exception:
            pass  # Citation validation failure must not block report generation

    # ── Phase 8.4: Reference existence gate ──────────────────────────────
    if provenance_bundle:
        try:
            from app.config import settings as _ref_settings
            if _ref_settings.reference_verification_enabled:
                from app.evidence.reference_verifier import (
                    ReferenceVerifier,
                    extract_academic_references,
                    render_reference_verification_section,
                )
                academic_refs = extract_academic_references(provenance_bundle)
                if academic_refs:
                    verifier = ReferenceVerifier(
                        allowed_indexes=[
                            i.strip()
                            for i in _ref_settings.reference_verifier_allowed_indexes.split(",")
                            if i.strip()
                        ],
                        timeout=_ref_settings.reference_verifier_timeout_seconds,
                        cache_dir=_ref_settings.reference_verifier_cache_dir,
                        cache_ttl=_ref_settings.reference_verifier_cache_ttl_seconds,
                    )
                    ref_report = verifier.verify(academic_refs)
                    if reference_verification_callback is not None:
                        reference_verification_callback(ref_report)
                    ref_lines = render_reference_verification_section(ref_report)
                    if ref_lines:
                        lines.extend(ref_lines)
        except Exception:
            pass  # Reference verification failure must not block report generation

    # ── Phase 8.1: Source tier distribution ──────────────────────────
    if provenance_bundle:
        tier_lines = _render_tier_distribution(provenance_bundle, plan)
        if tier_lines:
            lines.extend(tier_lines)

    # ── Phase 7: Skill version footer ──────────────────────────────────────
    skill_name = plan.get("skill_name")
    skill_version = plan.get("skill_version")
    if skill_name:
        footer = f"\n---\n\n*本报告由 Skill `{skill_name}` v{skill_version} 生成。*\n"
        lines.append(footer)

    if report_type == "detailed_report":
        toc = _build_toc(lines)
        toc_insert_pos = _find_section_start(lines, "## 3.")
        if toc_insert_pos >= 0:
            lines[toc_insert_pos:toc_insert_pos] = toc
        else:
            lines = _insert_after_section(lines, "## 2.", toc)

    return "\n".join(lines)


def _build_toc(lines: list[str]) -> list[str]:
    """Build a table of contents from ## and ### headings."""
    toc: list[str] = ["## 目录", ""]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            title = stripped[4:]
            toc.append(f"    * {title}")
        elif stripped.startswith("## "):
            title = stripped[3:]
            toc.append(f"* {title}")
    toc.append("")
    return toc


def _find_section_start(lines: list[str], marker: str) -> int:
    """Find the index of the first line starting with marker."""
    for i, line in enumerate(lines):
        if line.strip().startswith(marker):
            return i
    return -1


def _insert_after_section(lines: list[str], marker: str, insert_lines: list[str]) -> list[str]:
    """Insert lines after the last line of a section starting with marker."""
    pos = _find_section_start(lines, marker)
    if pos < 0:
        return lines
    # Find the end of that section (next ## heading or end of list)
    end = pos + 1
    while end < len(lines) and not lines[end].strip().startswith("## "):
        end += 1
    result = lines[:end] + [""] + insert_lines + lines[end:]
    return result


def _to_outline(lines: list[str]) -> str:
    """Reduce full report lines to outline only: header + TOC + section headings."""
    outline: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            outline.append(line)
            outline.append("")
    # Build TOC and insert after header
    toc = _build_toc(lines)
    toc_insert_pos = _find_section_start(outline, "## 2.")
    if toc_insert_pos >= 0:
        outline[toc_insert_pos:toc_insert_pos] = toc
    else:
        outline = outline[:2] + [""] + toc + [""] + outline[2:]
    if outline and outline[-1] != "":
        outline.append("")
    outline.append("> **大纲模式：** 本报告仅包含章节标题，如需完整内容请使用 `detailed_report` 或 `summary` 模式。")
    outline.append("")
    return "\n".join(outline)


def _render_limitations_section(bundle: dict[str, Any]) -> list[str]:
    """Generate a limitations section for unresolved/disputed claims.

    Phase 6: claims with status unresolved/requires_human are downgraded
    and listed here with supporting/refuting evidence counts.
    """
    claims = {item.get("claim_id"): item for item in bundle.get("claims") or []}
    resolutions = [
        item for item in bundle.get("resolutions") or []
        if item.get("status") in {"unresolved", "requires_human"}
    ]
    if not resolutions:
        return []

    lines = [
        "## 10. 限制与待核实结论",
        "",
        "> ⚠️ 以下结论存在未解决的证据冲突或需要人工判断，"
        "已从确定性结论中降级，不得作为事实使用。",
        "",
    ]

    for i, resolution in enumerate(resolutions, 1):
        claim_id = str(resolution.get("claim_id"))
        claim = claims.get(claim_id) or {}
        claim_text = claim.get("claim_text") or claim_id
        status = str(resolution.get("status"))
        confidence = resolution.get("confidence", 0)
        support_count = resolution.get("independent_support_count", 0)
        refute_count = resolution.get("independent_refute_count", 0)

        status_label = "未解决" if status == "unresolved" else "需人工判断"
        lines.extend([
            f"### {i}. {claim_text}",
            "",
            f"* 冲突状态: `{status}` ({status_label})",
            f"* 置信度: `{confidence}`",
            f"* 独立支持来源: `{support_count}`",
            f"* 独立反驳来源: `{refute_count}`",
        ])

        rationale = resolution.get("rationale") or {}
        if isinstance(rationale, dict):
            quality_gate = rationale.get("quality_gate") or {}
            if isinstance(quality_gate, dict) and not quality_gate.get("passed"):
                lines.append(
                    f"* 质量门禁: 未通过 "
                    f"(独立来源={quality_gate.get('independent_source_count')}, "
                    f"最低要求={quality_gate.get('minimum_independent_sources')})"
                )
            summary = rationale.get("summary")
            if summary and isinstance(summary, str):
                lines.append(f"* 分析摘要: {summary[:300]}")
        lines.append("")

    return lines


def save_report(run_id: str, markdown: str) -> str:
    """Save Markdown report and return a repository-relative path."""

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORTS_ROOT / f"{run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path.relative_to(ROOT))
