"""Traceable Research Agent"""

from __future__ import annotations

import json
import html
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import streamlit as st

# 自动加载项目根目录的 .env（若存在），优先级低于已有环境变量
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        pass  # python-dotenv 未安装时静默跳过，不影响运行

# ── 常量 ──────────────────────────────────────────────────────────
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_API_TIMEOUT_SECONDS = int(os.environ.get("STREAMLIT_API_TIMEOUT_SECONDS", "30"))
CREATE_TASK_TIMEOUT_SECONDS = int(os.environ.get("STREAMLIT_CREATE_TASK_TIMEOUT_SECONDS", "120"))
ALL_TOOLS = ["file_reader", "sql_query", "rag_search", "mcp_github_search", "tavily_search", "report_writer"]

TOOL_ICON = {
    "file_reader":      "📄",
    "sql_query":        "🗄️",
    "rag_search":       "🔍",
    "mcp_github_search":"🐙",
    "tavily_search":    "🌐",
    "report_writer":    "📝",
}
TOOL_CN = {
    "file_reader":      "本地文件读取",
    "sql_query":        "数据库查询",
    "rag_search":       "RAG 向量检索",
    "mcp_github_search":"GitHub 只读调研",
    "tavily_search":    "Tavily 外部搜索",
    "report_writer":    "Markdown 报告生成",
}
RISK_COLOR = {"low": "#15803D", "medium": "#B45309", "high": "#B91C1C"}

DEMO_TEMPLATES: dict[str, dict[str, Any]] = {
    "本地读取（文件 + RAG + SQL）": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report",
        "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
        "scenario_template_key": "standard",
    },
    "外部调研（GitHub + Tavily）": {
        "task": "Search GitHub repository issues and current web sources about traceable research agent, then generate a markdown report",
        "allowed_tools": ["mcp_github_search", "tavily_search", "report_writer"],
        "scenario_template_key": "standard",
    },
    "深度网页调研（Tavily + Firecrawl/Exa MCP）": {
        "task": "Deeply research current web sources about traceable research agent, discover sources, read page content, extract verifiable evidence, and generate a markdown report",
        "allowed_tools": None,
        "scenario_template_key": "deep_web_research",
    },
    "技术文档调研（GitHub + Context7/Exa MCP）": {
        "task": "Research current technical documentation for FastAPI, Streamlit, MCP SDK, and RAG patterns; use GitHub and documentation sources, then generate a markdown report",
        "allowed_tools": None,
        "scenario_template_key": "technical_docs_research",
    },
    "全规划器（本地读取 + 外部调研）": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, search GitHub repository issues and current web sources, then generate a markdown report",
        "allowed_tools": ALL_TOOLS,
        "scenario_template_key": "full_planner",
    },
}

STATUS_CN = {
    "pending":       ("⏳", "待执行", "#6B7280"),
    "running":       ("🔄", "执行中", "#2563EB"),
    "waiting_human": ("✋", "等待确认", "#B45309"),
    "completed":     ("✅", "已完成", "#15803D"),
    "failed":        ("❌", "执行失败", "#B91C1C"),
    "success":       ("✅", "成功", "#15803D"),
    "rejected":      ("🚫", "已拒绝", "#B91C1C"),
}

STREAM_EVENT_CN = {
    "create_task_started": "创建任务",
    "plan_ready": "规划完成",
    "run_requested": "请求执行",
    "run_status": "任务状态",
    "trace_created": "步骤开始",
    "trace_finished": "步骤完成",
    "waiting_human": "等待人工确认",
    "report_ready": "报告已生成",
    "done": "执行结束",
    "heartbeat": "心跳",
    "stream_error": "实时事件流异常",
    "message": "事件",
}

STREAM_STATUS_CN = {
    "pending": "待执行",
    "running": "运行中",
    "success": "成功",
    "completed": "已完成",
    "failed": "失败",
    "waiting_human": "等待确认",
    "rejected": "已拒绝",
    "fallback_polling": "切换轮询",
    "stream_timeout": "事件流超时",
    "not_found": "任务不存在",
}

HIDDEN_REPORT_SECTION_PREFIXES = ("## 6. 证据与工具观察结果",)

RAG_METADATA_FIELDS = [
    "retrieval_mode", "embedding_backend", "vector_backend",
    "fallback_used", "dense_hit_count", "bm25_hit_count", "rrf_k",
    "dimension", "collection_name",
]


def _template_allowed_tools(template: dict[str, Any]) -> list[str] | None:
    tools = template.get("allowed_tools")
    return list(tools) if isinstance(tools, list) else None


def _current_template() -> dict[str, Any]:
    name = st.session_state.get("selected_template", list(DEMO_TEMPLATES.keys())[0])
    return DEMO_TEMPLATES.get(name) or list(DEMO_TEMPLATES.values())[0]


def _current_scenario_template_key() -> str:
    template = _current_template()
    explicit_key = str(template.get("scenario_template_key") or "").strip()
    if explicit_key:
        return explicit_key
    return "full_planner" if st.session_state.get("allowed_tools") == ALL_TOOLS else "standard"

# ── 全局 CSS（极简深色调） ────────────────────────────────────────
GLOBAL_CSS = """
<style>
.step-card {
    border-left: 3px solid #3B82F6;
    padding: 10px 14px;
    margin: 8px 0;
    border-radius: 0 8px 8px 0;
    background: rgba(59,130,246,0.07);
}
.step-card-success { border-left-color: #15803D; background: rgba(21,128,61,0.07); }
.step-card-failed  { border-left-color: #B91C1C; background: rgba(185,28,28,0.07); }
.step-card-rejected{ border-left-color: #B91C1C; background: rgba(185,28,28,0.07); }
.step-header { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.step-meta   { font-size: 12px; opacity: 0.7; }
.risk-badge  { display:inline-block; padding:1px 8px; border-radius:10px;
               font-size:11px; color:white; margin-left:6px; }
.section-tip { font-size:12px; color:#888; margin:-8px 0 10px; padding-left:2px; }
.metric-row  { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.metric-box  { flex:1; min-width:90px; background:rgba(255,255,255,0.04);
               border:0.5px solid rgba(255,255,255,0.12); border-radius:8px;
               padding:10px 14px; }
.metric-label{ font-size:11px; opacity:0.6; margin-bottom:2px; }
.metric-value{ font-size:20px; font-weight:600; }
</style>
"""


# ── API helpers ───────────────────────────────────────────────────
class ApiError(Exception):
    pass


def init_state() -> None:
    default_template = list(DEMO_TEMPLATES.values())[0]
    defaults = {
        "api_base_url": os.environ.get("STREAMLIT_API_BASE_URL", DEFAULT_API_BASE_URL),
        # AUTH_ENABLED=false 时 API Key 为空即可；若后端开启鉴权，从 DEMO_API_KEY 自动填充
        "api_key":      os.environ.get("DEMO_API_KEY", ""),
        "tenant_id":    os.environ.get("DEFAULT_TENANT_ID", "demo"),
        "user_id":      os.environ.get("DEFAULT_USER_ID", "local-user"),
        "use_async_run": True,   # async by default to avoid 30s sync timeout
        "realtime_auto_refresh": False,
        "realtime_poll_seconds": 2,
        # EXECUTION_MODE 由后端 .env 控制，这里只做显示用
        "execution_mode_display": os.environ.get("EXECUTION_MODE", "planned"),
        "run_id": "",
        "last_task_response": None,
        "last_run_response": None,
        "last_status": None,
        "last_plan": None,
        "last_trace": [],
        "last_evidence": None,
        "last_evidence_export": None,
        "last_evidence_export_content": None,
        "last_report": None,
        "event_log": [],
        "selected_template": list(DEMO_TEMPLATES.keys())[0],
        "task_text": default_template["task"],
        "allowed_tools": _template_allowed_tools(default_template),
        "report_type": "summary",
        "source_mode_ui": "real",       # default: real API
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def api_url(path: str) -> str:
    return st.session_state.api_base_url.rstrip("/") + path


def request_headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if (k := st.session_state.get("api_key", "").strip()):
        h["X-API-Key"] = k
    if (t := st.session_state.get("tenant_id", "").strip()):
        h["X-Tenant-ID"] = t
    if (u := st.session_state.get("user_id", "").strip()):
        h["X-User-ID"] = u
    return h


def api_request(
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: int | float | None = None,
) -> Any:
    effective_timeout = timeout or DEFAULT_API_TIMEOUT_SECONDS
    try:
        r = requests.request(
            method,
            api_url(path),
            json=payload,
            headers=request_headers(),
            timeout=effective_timeout,
        )
    except requests.ConnectionError:
        raise ApiError("⚠️ 后端未启动，请先运行 FastAPI：python -m uvicorn app.main:app --port 8000")
    except requests.Timeout:
        if path == "/api/tasks":
            raise ApiError(
                "⚠️ 创建任务超时：后端可能仍在调用 LLM Planner 生成执行计划。"
                "标准调研模板包含 file/sql/rag/report 多工具规划，耗时会更长；"
                "请稍后查看后端日志，或重试创建任务。"
            )
        raise ApiError("⚠️ 请求超时，但后端可能仍在执行。请点击“刷新全部”查看最新状态。")
    except requests.RequestException as e:
        raise ApiError(f"⚠️ 请求失败：{e}")
    try:
        data = r.json()
    except ValueError:
        raise ApiError(f"⚠️ 后端返回非 JSON（HTTP {r.status_code}）")
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ApiError(f"HTTP {r.status_code}：{detail}")
    return data


def api_get(path: str, timeout: int | float | None = None) -> Any:
    return api_request("GET", path, timeout=timeout)


def api_post(path: str, payload: dict | None = None, timeout: int | float | None = None) -> Any:
    return api_request("POST", path, payload or {}, timeout=timeout)


def api_download(path: str, timeout: int | float | None = None) -> tuple[bytes, str]:
    effective_timeout = timeout or DEFAULT_API_TIMEOUT_SECONDS
    try:
        r = requests.get(api_url(path), headers=request_headers(), timeout=effective_timeout)
    except requests.ConnectionError:
        raise ApiError("⚠️ 后端未启动，请先运行 FastAPI：python -m uvicorn app.main:app --port 8000")
    except requests.Timeout:
        raise ApiError("⚠️ 下载请求超时，请稍后重试。")
    except requests.RequestException as e:
        raise ApiError(f"⚠️ 下载请求失败：{e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise ApiError(f"HTTP {r.status_code}：{detail}")
    return r.content, r.headers.get("content-type", "application/octet-stream")


def normalize_trace(data: Any) -> list[dict]:
    if not data:    return []
    if isinstance(data, list): return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict): return data.get("traces") or [data]
    return []


def refresh_all(show_errors: bool = True) -> None:
    run_id = st.session_state.get("run_id")
    if not run_id:  return
    try:
        st.session_state.last_status = api_get(f"/api/tasks/{run_id}")
        st.session_state.last_plan   = api_get(f"/api/tasks/{run_id}/plan")
        st.session_state.last_trace  = normalize_trace(api_get(f"/api/tasks/{run_id}/trace"))
        st.session_state.last_evidence = api_get(f"/api/tasks/{run_id}/evidence")
        st.session_state.last_report = api_get(f"/api/reports/{run_id}")
    except ApiError as exc:
        if show_errors: st.error(str(exc))


def realtime_events_url() -> str:
    run_id = st.session_state.get("run_id", "")
    return api_url(f"/api/tasks/{run_id}/events") if run_id else ""


def maybe_auto_refresh() -> None:
    if not st.session_state.get("realtime_auto_refresh"):
        return
    run_id = st.session_state.get("run_id")
    if not run_id:
        return
    status = (st.session_state.get("last_status") or {}).get("status")
    if status not in ("pending", "running"):
        return
    delay = int(st.session_state.get("realtime_poll_seconds") or 2)
    time.sleep(max(delay, 1))
    refresh_all(show_errors=False)
    st.rerun()


def _format_stream_event(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "message")
    status = str(event.get("status") or "")
    step_no = event.get("step_no")
    tool_name = event.get("tool_name")
    summary = event.get("output_summary") or event.get("error_message") or ""
    latency_ms = event.get("latency_ms")
    parts = [STREAM_EVENT_CN.get(event_type, event_type)]
    if status:
        parts.append(f"状态={STREAM_STATUS_CN.get(status, status)}")
    if step_no:
        parts.append(f"步骤={step_no}")
    if tool_name:
        parts.append(f"工具={tool_name}")
    if latency_ms is not None:
        parts.append(f"耗时={latency_ms}ms")
    if summary:
        parts.append(f"摘要={str(summary)[:180]}")
    return " | ".join(parts)


def _append_event_log(event: dict[str, Any]) -> None:
    logs = list(st.session_state.get("event_log") or [])
    logs.append(_format_stream_event(event))
    st.session_state.event_log = logs[-200:]


def render_event_console(target: Any | None = None) -> None:
    lines = st.session_state.get("event_log") or []
    text = "\n".join(lines[-80:]) if lines else "暂无实时事件。"
    writer = target if target is not None else st
    writer.code(text, language="text")


def stream_task_events(run_id: str, target: Any | None = None) -> None:
    if not run_id:
        return
    url = api_url(
        f"/api/tasks/{run_id}/events"
        "?poll_interval_seconds=0.5&heartbeat_seconds=5&max_duration_seconds=600"
    )
    data_lines: list[str] = []
    try:
        with requests.get(
            url,
            headers=request_headers(),
            stream=True,
            timeout=(5, 660),
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines(decode_unicode=True):
                line = raw_line or ""
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                    continue
                if line:
                    continue
                if not data_lines:
                    continue
                try:
                    event = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    data_lines = []
                    continue
                data_lines = []
                _append_event_log(event)
                if target is not None:
                    render_event_console(target)
                if event.get("event_type") == "done":
                    break
    except requests.RequestException as exc:
        _append_event_log(
            {
                "event_type": "stream_error",
                "status": "fallback_polling",
                "error_message": f"实时事件流失败，已切换轮询：{type(exc).__name__}",
            }
        )
        if target is not None:
            render_event_console(target)
        st.session_state.realtime_auto_refresh = True


def _sync_allowed_tools() -> None:
    """on_change callback for the template selectbox — syncs allowed_tools only.
    Called automatically by Streamlit when the selectbox value changes.
    Must NOT touch st.session_state.selected_template (widget owns it via key=).
    """
    st.session_state.allowed_tools = _template_allowed_tools(_current_template())


def apply_template(name: str, fill_task: bool = False) -> None:
    """Apply a template. Only overwrites task_text when fill_task=True (user explicitly clicked).
    NOTE: Never sets st.session_state.selected_template — Streamlit owns it via key=.
    """
    t = DEMO_TEMPLATES.get(name) or list(DEMO_TEMPLATES.values())[0]
    if fill_task:
        st.session_state.task_text = t["task"]
    st.session_state.allowed_tools = _template_allowed_tools(t)


# ── UI helpers ────────────────────────────────────────────────────
def status_chip(status: str | None) -> str:
    icon, label, color = STATUS_CN.get(status or "", ("❓", status or "未知", "#6B7280"))
    return (f"<span style='padding:2px 10px;border-radius:10px;background:{color};"
            f"color:white;font-size:12px'>{icon} {label}</span>")


def risk_badge(level: str) -> str:
    c = RISK_COLOR.get(level, "#6B7280")
    cn = {"low": "低", "medium": "中", "high": "高"}.get(level, level)
    return f"<span class='risk-badge' style='background:{c}'>风险 {cn}</span>"


def plan_step_card(step: dict) -> None:
    tool  = step.get("tool_name", "")
    icon  = TOOL_ICON.get(tool, "🔧")
    cn    = TOOL_CN.get(tool, tool)
    goal  = step.get("goal", "")
    risk  = step.get("risk_level", "low")
    st.markdown(
        f"<div class='step-card'>"
        f"<div class='step-header'>"
        f"步骤 {step.get('step_no')} &nbsp; {icon} {cn}"
        f"{risk_badge(risk)}"
        f"</div>"
        f"<div class='step-meta'>{goal}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if step.get("arguments"):
        with st.expander("📎 调用参数", expanded=False):
            st.json(step["arguments"])


def trace_step_card(trace: dict) -> None:
    tool   = trace.get("tool_name", "")
    status = trace.get("status", "unknown")
    icon   = TOOL_ICON.get(tool, "🔧")
    cn     = TOOL_CN.get(tool, tool)
    css    = f"step-card-{status}" if status in ("success", "completed", "failed", "rejected") else ""
    s_icon, _, _ = STATUS_CN.get(status, ("❓", status, "#6B7280"))

    latency = trace.get("latency_ms")
    lat_str = f"&nbsp;·&nbsp;{latency} ms" if latency is not None else ""

    st.markdown(
        f"<div class='step-card {css}'>"
        f"<div class='step-header'>"
        f"步骤 {trace.get('step_no')} &nbsp; {icon} {cn} &nbsp; {s_icon}"
        f"{lat_str}"
        f"</div>"
        f"<div class='step-meta'>"
        f"输入：{trace.get('input_summary','—')}<br>"
        f"输出：{trace.get('output_summary','—')}"
        + (f"<br>⚠️ {trace.get('error_message')}" if trace.get("error_message") else "")
        + f"</div></div>",
        unsafe_allow_html=True,
    )

    # ReAct 思考链
    trace_meta = trace.get("metadata") or {}
    if isinstance(trace_meta, str):
        try:
            trace_meta = json.loads(trace_meta)
        except Exception:
            trace_meta = {}
    if isinstance(trace_meta, dict) and trace_meta.get("parallel") is True:
        with st.expander(f"Parallel group {trace_meta.get('parallel_group_id')}", expanded=False):
            cols = st.columns(4)
            cols[0].metric("parallel", str(trace_meta.get("parallel")))
            cols[1].metric("worker", trace_meta.get("parallel_worker_id", "-"))
            cols[2].metric("group size", trace_meta.get("parallel_group_size", "-"))
            cols[3].metric("latency", f"{trace_meta.get('latency_ms', '-')} ms")
            st.caption(
                f"started_at={trace_meta.get('started_at')} | "
                f"finished_at={trace_meta.get('finished_at')}"
            )

    out = trace.get("output") or {}
    if isinstance(out, dict):
        thought = out.get("thought") or (out.get("metadata") or {}).get("thought")
        if thought:
            with st.expander(f"🧠 ReAct 思考链（步骤 {trace.get('step_no')}）"):
                st.write(f"**Thought：** {thought}")
                st.write(f"**Action：**  {out.get('action', tool)}")
                obs = out.get("observation_summary") or trace.get("output_summary", "")
                st.write(f"**Observation：** {obs}")

    # RAG 检索元数据
    meta_src = (out if isinstance(out, dict) else {}) or {}
    meta = {k: meta_src.get(k) for k in RAG_METADATA_FIELDS if meta_src.get(k) is not None}
    if not meta:
        raw_meta = trace.get("metadata") or {}
        if isinstance(raw_meta, str):
            try: raw_meta = json.loads(raw_meta)
            except Exception: raw_meta = {}
        meta = {k: raw_meta.get(k) for k in RAG_METADATA_FIELDS if raw_meta.get(k) is not None}

    if meta:
        with st.expander(f"🔍 RAG 检索详情（步骤 {trace.get('step_no')}）"):
            cols = st.columns(3)
            cols[0].metric("检索模式", meta.get("retrieval_mode", "—"))
            cols[1].metric("稠密命中", meta.get("dense_hit_count", "—"))
            cols[2].metric("BM25 命中", meta.get("bm25_hit_count", "—"))
            cols2 = st.columns(3)
            cols2[0].metric("Embedding", meta.get("embedding_backend", "—"))
            cols2[1].metric("是否 Fallback", "是" if meta.get("fallback_used") else "否")
            cols2[2].metric("RRF-k", meta.get("rrf_k", "—"))
            cols3 = st.columns(2)
            cols3[0].metric("向量维度", meta.get("dimension", "—"))
            cols3[1].metric("Collection", meta.get("collection_name", "—"))


def render_evidence_summary() -> None:
    evidence = st.session_state.get("last_evidence") or {}
    if not isinstance(evidence, dict) or not evidence:
        return
    groups = evidence.get("source_groups") or []
    warnings = evidence.get("warnings") or []
    claims = evidence.get("claims") or []
    unsupported = evidence.get("unsupported_claims") or []
    with st.expander("证据聚合", expanded=False):
        cols = st.columns(4)
        cols[0].metric("证据条目", evidence.get("total_evidence_items", 0))
        cols[1].metric("来源分组", len(groups))
        cols[2].metric("支持结论", len(claims))
        cols[3].metric("受限结论", len(unsupported))
        if warnings:
            st.markdown("**来源警告**")
            for warning in warnings:
                st.warning(warning)
        if groups:
            st.markdown("**来源分组**")
            st.dataframe(groups, use_container_width=True, hide_index=True)
        if claims:
            preview = [
                {
                    "结论 ID": item.get("claim_id"),
                    "支持程度": item.get("support_level"),
                    "证据": ", ".join(item.get("evidence_ids") or []),
                    "结论": item.get("claim"),
                }
                for item in claims[:8]
            ]
            st.markdown("**结论-证据映射**")
            st.dataframe(preview, use_container_width=True, hide_index=True)
        _render_evidence_export_controls()


def _render_evidence_export_controls() -> None:
    run_id = st.session_state.get("run_id")
    if not run_id:
        return
    st.markdown("**证据导出**")
    cols = st.columns([2, 1])
    with cols[0]:
        export_format = st.selectbox(
            "证据导出格式",
            ["json", "jsonl", "markdown"],
            key="evidence_export_format",
            label_visibility="collapsed",
        )
    with cols[1]:
        if st.button("导出", use_container_width=True):
            try:
                result = api_get(f"/api/tasks/{run_id}/evidence/export?format={export_format}")
                content = api_get(f"/api/tasks/{run_id}/evidence/export/content?format={export_format}")
                st.session_state.last_evidence_export = result
                st.session_state.last_evidence_export_content = content
                st.success(f"export_path={result.get('export_path')}")
            except ApiError as exc:
                st.error(str(exc))
    last_export = st.session_state.get("last_evidence_export")
    content_payload = st.session_state.get("last_evidence_export_content")
    if isinstance(last_export, dict) and last_export.get("run_id") == run_id:
        st.caption(
            f"{last_export.get('format')} | {last_export.get('item_count')} items | "
            f"{last_export.get('export_path')}"
        )
    if (
        isinstance(content_payload, dict)
        and content_payload.get("run_id") == run_id
        and content_payload.get("format") == (last_export or {}).get("format")
    ):
        content = str(content_payload.get("content") or "")
        fmt = str(content_payload.get("format") or "json")
        preview = content[:8000]
        if fmt == "markdown":
            with st.expander("Markdown 预览", expanded=True):
                st.markdown(preview or "_暂无证据内容。_")
        else:
            with st.expander(f"{fmt.upper()} 预览", expanded=True):
                st.code(preview or "", language="json" if fmt == "json" else None)
        st.download_button(
            "下载证据导出",
            data=content,
            file_name=_evidence_export_filename(run_id, fmt),
            mime=content_payload.get("content_type") or "text/plain",
            use_container_width=True,
        )


def _evidence_export_filename(run_id: str, export_format: str) -> str:
    extension = {"json": "json", "jsonl": "jsonl", "markdown": "md"}.get(export_format, "txt")
    safe_run_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)[:96] or "unknown"
    return f"evidence_{safe_run_id}.{extension}"


# ── 侧边栏 ────────────────────────────────────────────────────────
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## 🕹️ 演示控制台")

        # 健康检查
        if st.button("🩺 检查后端连接", use_container_width=True):
            try:
                h = api_get("/health")
                st.session_state.health = h
                st.success(f"✅ 连接正常  ·  后端默认模式：{h.get('execution_mode','planned')}")
            except ApiError as e:
                st.error(str(e))

        st.divider()
        st.markdown("**📋 场景模板**")
        st.selectbox(
            "选择演示场景",
            list(DEMO_TEMPLATES.keys()),
            key="selected_template",       # Streamlit 独占管理此 key，禁止在回调外赋值
            on_change=_sync_allowed_tools, # 切换时只同步 allowed_tools，不碰 task_text
            label_visibility="collapsed",
        )

        st.divider()
        st.markdown("**⚙️ 执行模式**")
        st.selectbox(
            "执行模式",
            ["planned", "react"],
            format_func=lambda x: "📋 Planned（固定计划）" if x == "planned" else "🤖 ReAct（动态决策）",
            key="execution_mode_display",
            label_visibility="collapsed",
        )

        st.divider()
        if st.session_state.get("run_id"):
            st.code(st.session_state.run_id[:16] + "…", language=None)
            if st.button("🔄 刷新全部", use_container_width=True):
                refresh_all()
                # 不显式 st.rerun()：按钮点击本身会触发 Streamlit 的一次 rerun
                # 避免双重 rerun 导致 selectbox index 重计算、task_text 被覆盖
        if st.button("🗑️ 清空会话", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            init_state()
            st.rerun()


# ── Tab 1：任务与规划 ─────────────────────────────────────────────
def tab_task() -> None:
    st.markdown("#### 📝 调研任务描述")
    st.markdown('<p class="section-tip">系统会将自然语言任务转化为结构化执行计划，每一步工具调用都有明确的目标和风险等级。</p>', unsafe_allow_html=True)

    st.text_area(
        "任务内容",
        height=90,
        key="task_text",
        label_visibility="collapsed",
    )
    task_text = st.session_state.task_text  # 从 session state 读取，避免 value= 与 key= 冲突

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("① 创建任务", type="primary", use_container_width=True):
            payload = {
                "task": task_text,
                "allowed_tools": st.session_state.allowed_tools,
                "report_type": "summary",
                "source_mode": st.session_state.get("source_mode_ui", "real"),
                "execution_mode_override": st.session_state.execution_mode_display,
                "scenario_template": st.session_state.get("selected_template", ""),
                "scenario_template_key": _current_scenario_template_key(),
            }
            try:
                st.session_state.event_log = []
                _append_event_log(
                    {
                        "event_type": "create_task_started",
                        "status": "pending",
                        "output_summary": "开始创建任务并生成执行计划",
                    }
                )
                terminal = st.empty()
                render_event_console(terminal)
                with st.spinner("正在创建任务并调用 Planner 生成执行计划，标准调研可能需要 30-90 秒..."):
                    resp = api_post("/api/tasks", payload, timeout=CREATE_TASK_TIMEOUT_SECONDS)
                st.session_state.last_task_response = resp
                st.session_state.run_id = resp.get("run_id", "")
                _append_event_log(
                    {
                        "event_type": "plan_ready",
                        "status": resp.get("status"),
                        "output_summary": f"run_id={st.session_state.run_id}",
                    }
                )
                render_event_console(terminal)
                refresh_all(show_errors=False)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))

    with col2:
        run_id = st.session_state.get("run_id", "")
        if run_id and st.button("② 执行任务 ▶", type="primary", use_container_width=True):
            run_path = (
                f"/api/tasks/{run_id}/run_async"
                if st.session_state.use_async_run
                else f"/api/tasks/{run_id}/run"
            )
            try:
                resp = api_post(run_path, {})
                st.session_state.last_run_response = resp
                st.session_state.realtime_auto_refresh = True
                st.session_state.event_log = []
                _append_event_log(
                    {
                        "event_type": "run_requested",
                        "status": resp.get("status"),
                        "output_summary": f"run_id={run_id}",
                    }
                )
                terminal = st.empty()
                render_event_console(terminal)
                if run_path.endswith("/run_async"):
                    stream_task_events(run_id, terminal)
                refresh_all(show_errors=False)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))

    # 当前状态摘要
    status_obj = st.session_state.get("last_status")
    if status_obj:
        cur = status_obj.get("status", "unknown")
        st.markdown(status_chip(cur), unsafe_allow_html=True)
        if cur == "waiting_human":
            _render_hitl()

    st.divider()

    # 计划可视化
    plan = st.session_state.get("last_plan")
    if plan:
        ps = plan.get("planner_source", "deterministic")
        if ps == "llm":
            st.success("🤖 LLM 规划器已激活")
        elif ps == "deterministic_fallback":
            st.warning("⚠️ LLM 规划器降级为确定性模式")
        else:
            st.info("📋 当前使用确定性规划器（无 LLM 调用）")

        steps = plan.get("steps") or []
        st.markdown(f"#### 📋 执行计划  ·  共 {len(steps)} 步")
        st.markdown('<p class="section-tip">计划由 Planner 根据任务描述和可用工具生成，每步有固定的 goal、参数和风险等级。</p>', unsafe_allow_html=True)
        for step in steps:
            plan_step_card(step)
    elif st.session_state.get("run_id"):
        st.info("计划加载中…点击侧边栏「刷新全部」获取最新状态。")


def _pending_confirmation_details() -> dict[str, Any] | None:
    plan = st.session_state.get("last_plan") or {}
    react_state = plan.get("react_state") if isinstance(plan, dict) else None
    pending = react_state.get("pending_confirmation") if isinstance(react_state, dict) else None
    if isinstance(pending, dict) and isinstance(pending.get("confirmation_details"), dict):
        return pending["confirmation_details"]
    current_step = int((st.session_state.get("last_status") or {}).get("current_step") or 0)
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if int(step.get("step_no") or 0) > current_step and step.get("requires_confirmation"):
            details = step.get("confirmation_details")
            return details if isinstance(details, dict) else None
    return None


def _render_hitl() -> None:
    st.warning("✋ 任务正在等待人工确认，请确认后继续执行。")
    details = _pending_confirmation_details()
    if details:
        st.error("检测到 file_reader 请求读取非白名单路径。批准后仅允许读取本次展示的具体文件路径，不会放开目录或磁盘。")
        st.json(
            {
                "requested_path": details.get("requested_path"),
                "resolved_path": details.get("resolved_path"),
                "allowed_roots": details.get("allowed_roots"),
                "confirmation_scope": details.get("confirmation_scope"),
            }
        )
    approved = st.checkbox("批准执行", value=True)
    comment  = st.text_input("备注", value="Streamlit 界面已确认")
    if st.button("提交确认"):
        run_id = st.session_state.get("run_id")
        try:
            resp = api_post(f"/api/tasks/{run_id}/confirm",
                            {"approved": approved, "resume": True, "comment": comment})
            st.success("✅ 确认已提交")
            st.session_state.last_run_response = resp.get("run_result") or resp
            refresh_all(show_errors=False)
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))


# ── Tab 2：执行追踪 ───────────────────────────────────────────────
def tab_trace() -> None:
    st.markdown("#### 🔍 工具调用追踪")
    st.markdown('<p class="section-tip">每次工具调用都记录在 Trace Store（SQLite）中，包含输入摘要、输出摘要、延迟和结构化元数据。这是项目"可追踪"能力的核心体现。</p>', unsafe_allow_html=True)

    traces = st.session_state.get("last_trace") or []
    status_obj = st.session_state.get("last_status") or {}

    with st.expander(
        "实时执行事件流",
        expanded=bool(status_obj.get("status") in ("pending", "running", "waiting_human")),
    ):
        if st.session_state.get("run_id"):
            st.code(realtime_events_url(), language=None)
        render_event_console()
        cols = st.columns(4)
        cols[0].metric("status", status_obj.get("status", "-"))
        cols[1].metric("current step", status_obj.get("current_step", 0))
        cols[2].metric("trace events", len(traces))
        cols[3].metric(
            "auto polling",
            "on" if st.session_state.get("realtime_auto_refresh") else "off",
        )
        if traces:
            latest = traces[-1]
            st.caption(
                f"latest={latest.get('tool_name')} status={latest.get('status')} "
                f"finished_at={latest.get('finished_at')}"
            )

    if not traces:
        st.info("暂无 Trace，请先创建并执行任务。")
        return

    # 汇总指标
    total   = len(traces)
    success = sum(1 for t in traces if t.get("status") in ("success", "completed"))
    failed  = sum(1 for t in traces if t.get("status") in ("failed", "rejected"))
    avg_lat = (sum(t.get("latency_ms", 0) or 0 for t in traces) / total) if total else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总步骤数", total)
    c2.metric("成功", success, delta=None)
    c3.metric("失败", failed, delta=None)
    c4.metric("平均延迟", f"{avg_lat:.0f} ms")

    st.markdown("---")
    st.markdown("**工具调用时间线**")
    for trace in traces:
        trace_step_card(trace)

    st.divider()
    # 执行元数据摘要
    plan_source = status_obj.get("planner_source", "—")
    exec_mode   = status_obj.get("execution_mode", "planned")
    with st.expander("📊 执行元信息（点击展开）", expanded=False):
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("规划器来源", plan_source)
        mc2.metric("执行模式",   exec_mode)
        mc3.metric("总延迟",     f"{status_obj.get('total_latency_ms', '—')} ms")
        if status_obj.get("llm_provider"):
            mc1.metric("LLM 提供商", status_obj.get("llm_provider"))
            mc2.metric("LLM 模型",   status_obj.get("llm_model", "—"))


FOLDED_REPORT_SECTION_PREFIXES = ("## 4.", "## 5.", "## 6.", "## 7.")


def _split_report_markdown(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    in_code_block = False

    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            current_lines.append(line)
            continue
        if not in_code_block and line.startswith("## "):
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, "\n".join(current_lines).strip()))
    return sections


def _report_section_label(heading: str, body: str) -> str:
    source = heading or next((line for line in body.splitlines() if line.strip()), "报告正文")
    return re.sub(r"^#+\s*", "", source).strip() or "报告正文"


def _collapse_key_evidence_blocks(markdown: str) -> str:
    pattern = re.compile(r"关键证据片段：\s*\n\s*```(?:text)?\n(.*?)\n```", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        escaped = html.escape(match.group(1).strip())
        return (
            "<details><summary>关键证据片段</summary>"
            f"<pre><code>{escaped}</code></pre></details>"
        )

    return pattern.sub(replace, markdown)


def render_report_markdown(markdown: str) -> None:
    for heading, body in _split_report_markdown(markdown):
        if heading.startswith(HIDDEN_REPORT_SECTION_PREFIXES):
            continue
        should_fold = heading.startswith(FOLDED_REPORT_SECTION_PREFIXES)
        rendered = _collapse_key_evidence_blocks(body)
        if should_fold:
            with st.expander(_report_section_label(heading, body), expanded=False):
                st.markdown(rendered, unsafe_allow_html=True)
        else:
            st.markdown(rendered, unsafe_allow_html=True)


# ── Tab 3：研究报告 ───────────────────────────────────────────────
def tab_report() -> None:
    st.markdown("#### 📝 Markdown 研究报告")

    report = st.session_state.get("last_report")
    if not report:
        st.info("尚未生成报告，请先执行任务。")
        return

    if not report.get("exists"):
        st.warning(report.get("message") or "报告文件尚未生成。")
        return

    md = report.get("markdown") or ""

    # 报告存在：展示状态摘要
    status_obj = st.session_state.get("last_status") or {}
    plan = st.session_state.get("last_plan") or {}
    react_state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
    exec_mode = status_obj.get("execution_mode", plan.get("execution_mode", "planned"))
    requested_mode = status_obj.get(
        "requested_execution_mode",
        plan.get("requested_execution_mode", exec_mode),
    )
    fallback_used = bool(react_state.get("fallback_used"))

    col1, col2, col3 = st.columns(3)
    col1.metric("执行模式", exec_mode)
    col2.metric("请求执行模式", requested_mode)
    col3.metric("是否降级", "是" if fallback_used else "否")

    render_evidence_summary()

    st.divider()
    render_report_markdown(md)
    run_id = st.session_state.get("run_id", "")
    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button(
        "⬇️ Markdown",
        data=md,
        file_name=f"research_report_{run_id[:8]}.md",
        mime="text/markdown",
    )
    for column, fmt, label, filename, fallback_mime in [
        (
            dl2,
            "docx",
            "⬇️ Word",
            f"research_report_{run_id[:8]}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (dl3, "pdf", "⬇️ PDF", f"research_report_{run_id[:8]}.pdf", "application/pdf"),
    ]:
        try:
            content, content_type = api_download(
                f"/api/reports/{run_id}/download?format={fmt}",
                timeout=DEFAULT_API_TIMEOUT_SECONDS,
            )
            column.download_button(
                label,
                data=content,
                file_name=filename,
                mime=content_type or fallback_mime,
            )
        except ApiError as exc:
            column.caption(str(exc))


# ── 主入口 ────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="Traceable Research Agent",
        page_icon="🔬",
        layout="wide",
    )
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
    init_state()
    render_sidebar()

    st.markdown("# 🔬 Traceable Research Agent")
    st.divider()

    tab1, tab2, tab3 = st.tabs(["⚡ 任务与规划", "🔍 执行追踪", "📝 研究报告"])
    with tab1:  tab_task()
    with tab2:  tab_trace()
    with tab3:  tab_report()
    maybe_auto_refresh()


if __name__ == "__main__":
    main()
