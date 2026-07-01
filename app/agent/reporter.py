"""Markdown report generation from deterministic run observations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from app.llm.base import LLMClient
    from app.llm.base import LLMMessage
except ImportError:
    LLMClient = None   # type: ignore[assignment,misc]
    LLMMessage = None  # type: ignore[assignment,misc]

from app.trace.models import AgentRun, ToolTrace
from app.agent.context_compressor import compress_evidence, has_useful_evidence
from app.agent.evidence import build_evidence_bundle, render_evidence_markdown


ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = ROOT / "workspace" / "reports"


def _json_preview(data: Any, max_chars: int = 500) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


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
    if tool_name == "rag_search":
        hits = output.get("hits") or []
        rag_metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
        selected = [
            {
                "source": hit.get("source"),
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("score"),
                "text": str(hit.get("text") or "")[:240],
                "rrf_score": (hit.get("metadata") or {}).get("rrf_score"),
            }
            for hit in hits[:5]
        ]
        return _json_preview(
            {
                "retrieval": {
                    key: rag_metadata.get(key)
                    for key in (
                        "retrieval_mode",
                        "dense_hit_count",
                        "bm25_hit_count",
                        "rrf_k",
                        "fallback_used",
                    )
                    if key in rag_metadata
                },
                "hits": selected,
            },
            max_chars=1400,
        )
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


def _rag_metadata_lines(metadata: dict[str, Any]) -> list[str]:
    labels = {
        "retrieval_mode": "检索模式",
        "dense_hit_count": "稠密检索候选数",
        "bm25_hit_count": "BM25 候选数",
        "rrf_k": "RRF 融合参数",
        "fallback_used": "是否降级",
        "embedding_backend": "Embedding 后端",
        "vector_backend": "向量后端",
    }
    return [
        f"* {label} (`{key}`): `{metadata[key]}`"
        for key, label in labels.items()
        if key in metadata
    ]


def _failure_category(error_message: str | None, metadata: dict[str, Any]) -> str:
    error_type = str(metadata.get("error_type") or "").lower()
    text = f"{error_type} {error_message or ''}".lower()
    if "same_tool_max_calls" in text or error_type == "tool_call_limit":
        return "调用次数上限保护"
    if any(term in text for term in ("safety_rejected", "read-only", "readonly", "sql")):
        return "安全限制"
    if any(term in text for term in ("no_hit", "no hit", "empty")):
        return "检索为空"
    return "工具失败"


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
    requested_limit = 3 if any(term in task.lower() for term in ("top 3", "top3", "最高的 3", "最高3")) else 5
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


def _tavily_final_answer(record: dict[str, Any]) -> list[str] | None:
    output = record["output"]
    results = [item for item in (output.get("results") or []) if isinstance(item, dict)]
    answer = str(output.get("answer") or "").strip()
    if not answer and not results:
        return None
    source = str(record["metadata"].get("data_source") or "unknown")
    intro = {
        "tavily_api": "以下是根据真实 Tavily Search API 检索证据整理的资料：",
        "mock": "以下是根据 Tavily mock 离线数据整理的演示资料：",
        "fallback": "以下是 Tavily API 请求失败后使用降级数据整理的资料：",
    }.get(source, "以下是根据本次 Tavily 工具证据整理的资料：")
    lines = [intro, ""]
    if answer:
        lines.extend([f"**综合回答：** {answer}", ""])
    for index, item in enumerate(results[:5], 1):
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
        if not record["success"] or record["tool_name"] not in {"rag_search", "file_reader", "tavily_search"}:
            continue
        output = record["output"]
        if record["tool_name"] == "rag_search":
            snippets.extend(str(hit.get("text") or "").strip() for hit in output.get("hits") or [] if isinstance(hit, dict))
        elif record["tool_name"] == "file_reader":
            snippets.append(str(output.get("content") or "").strip())
        else:
            if output.get("answer"):
                snippets.append(str(output["answer"]).strip())
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
        ("大模型应用", ("prompt", "rag", "agent", "function calling", "工具调用")),
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
) -> str | None:
    """Call LLM to synthesize tool evidence into a coherent answer.
    Returns synthesized text, or None if LLM call fails / no useful evidence.
    """
    if LLMClient is None or not llm_client.is_available():
        return None
    if not has_useful_evidence(observations):
        return None
    evidence = compress_evidence(observations, max_total_chars=5000)
    if not evidence.strip():
        return None
    messages = [
        LLMMessage(role="system", content=_SYNTHESIS_SYSTEM),
        LLMMessage(
            role="user",
            content=_SYNTHESIS_USER_TMPL.format(task=task, evidence=evidence),
        ),
    ]
    try:
        response = llm_client.complete(messages)
        if response.success and response.content:
            return response.content.strip()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("LLM synthesis failed: %s", exc)
    return None


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
    repaired = re.sub(r"来源[:：]\s*\[`?(?:tavily_search|mcp_github_search)`?\]", replacement, answer)
    repaired = re.sub(r"来源[:：]\s*(?:tavily_search|mcp_github_search)\b", replacement, repaired)
    return repaired


def _render_final_answer(
    task: str, observations: list[dict[str, Any]], traces: list[ToolTrace]
) -> list[str]:
    """Build a user-facing answer strictly from successful tool evidence."""

    records = _evidence_records(observations, traces)
    successful = [record for record in records if record["success"]]

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
                "如需工具验证，请使用包含 rag_search 或 mcp_github_search 的场景模板重新提问。",
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

    if github_record:
        answer = _github_final_answer(github_record, task)
    elif learning_route:
        answer = _learning_route_final_answer(records)
    elif tavily_record:
        answer = _tavily_final_answer(tavily_record)
    else:
        answer = None

    if not answer:
        summaries = [
            str(record["summary"]).strip()
            for record in successful
            if record.get("summary")
        ]
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
                    "1. 切换到包含 `rag_search`（本地知识库检索）的场景模板；",
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


def generate_markdown_report(
    run: AgentRun,
    plan: dict[str, Any],
    observations: list[dict[str, Any]],
    traces: list[ToolTrace],
    llm_client: "LLMClient | None" = None,
) -> str:
    """Build a Markdown report from persisted run evidence.

    Phase A: if llm_client is provided and available, the 「3. 最终回答」section
    is generated by LLM synthesis of tool evidence instead of template rules.
    Falls back to template automatically if LLM is unavailable or call fails.
    """

    lines: list[str] = [
        "# Traceable Research Agent 调研报告",
        "",
        "## 1. 任务说明",
        "",
        run.task,
        "",
        "## 2. 运行摘要",
        "",
        f"* 执行模式 (`execution_mode`): `{plan.get('execution_mode') or 'planned'}`",
        f"* 请求执行模式 (`requested_execution_mode`): `{plan.get('requested_execution_mode') or plan.get('execution_mode') or 'planned'}`",
        f"* 是否降级 (`fallback_used`): `{bool((plan.get('react_state') or {}).get('fallback_used'))}`",
        "",
    ]

    # ── Phase A: LLM synthesis if available, else template ──────────────────
    _llm_answer: str | None = None
    if llm_client is not None:
        _llm_answer = _llm_synthesize_answer(run.task, observations, llm_client)
        if _llm_answer:
            _llm_answer = _repair_tool_only_sources(
                _llm_answer,
                _evidence_records(observations, traces),
            )

    _final_answer_lines: list[str] = (
        [_llm_answer, "",
         *_source_reference_lines(_evidence_records(observations, traces)),
         "> **生成方式：** 本回答由 LLM 综合工具证据生成，各来源已标注。", ""]
        if _llm_answer
        else (_render_final_answer(run.task, observations, traces) or [])
    )

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
            lines.extend(
                [
                    f"### 步骤 {observation.get('step_no')}: {tool_name}",
                    "",
                    f"* 是否成功 (`success`): `{observation.get('success')}`",
                    f"* 输出摘要 (`output_summary`): {output_summary or '<none>'}",
                    f"* 错误信息 (`error_message`): {observation.get('error_message') or '<none>'}",
                    "",
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
            if tool_name == "rag_search" and metadata:
                rag_lines = _rag_metadata_lines(metadata)
                if rag_lines:
                    lines.extend(["RAG metadata 中文说明：", "", *rag_lines, ""])
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

    problem_traces = [trace for trace in traces if trace.status in {"failed", "rejected"}]
    if problem_traces:
        lines.extend(["", "## 8. 失败与拒绝详情", ""])
        for trace in problem_traces:
            trace_metadata = _trace_metadata(trace)
            lines.extend(
                [
                    f"### 步骤 {trace.step_no}: {trace.tool_name}",
                    "",
                    f"* 状态 (`status`): `{trace.status}`",
                    f"* 类型: {_failure_category(trace.error_message, trace_metadata)}",
                    f"* 原始错误 (`error_message`): {trace.error_message or '<none>'}",
                    f"* 输出摘要 (`output_summary`): {trace.output_summary or '<none>'}",
                    "",
                ]
            )

    return "\n".join(lines)


def save_report(run_id: str, markdown: str) -> str:
    """Save Markdown report and return a repository-relative path."""

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORTS_ROOT / f"{run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path.relative_to(ROOT))
