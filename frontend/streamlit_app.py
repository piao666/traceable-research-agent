"""Traceable Research Agent"""

from __future__ import annotations

import json
import html
import os
import re
import textwrap
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
    "本地资料分析": {
        "task": "结合本地产品资料、示例 SQL 指标和 RAG 证据，复盘一个 AI 调研功能的效果、风险和下一步优化建议，生成可审计中文报告。",
        "description": "适合演示内部资料复盘：本地文件、只读 SQL、RAG 检索和报告生成。",
        "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
        "scenario_template_key": "standard",
    },
    "联网深度调研": {
        "task": "调研 OpenAI-compatible API 网关产品的竞品格局：比较定价、模型支持、限流策略、文档成熟度和迁移风险，生成带来源链接的中文报告。",
        "description": "适合演示竞品/市场情报：Exa/Tavily 做来源发现，Firecrawl 读取网页正文。",
        "allowed_tools": None,
        "scenario_template_key": "deep_web_research",
    },
    "技术文档调研": {
        "task": "比较 FastAPI、LangGraph、MCP SDK 在企业 Agent 工具链落地中的适用边界、集成成本、风险点和推荐采用路径。",
        "description": "适合演示技术选型/供应商评估；Context7 adapter 已预留，当前以已注册的只读 MCP 工具为准。",
        "allowed_tools": None,
        "scenario_template_key": "technical_docs_research",
    },
}

EXECUTION_MODE_CN = {
    "planned": "固定计划",
    "react": "ReAct 动态决策",
}

PLANNER_SOURCE_CN = {
    "llm": "LLM 规划器",
    "deterministic": "规则规划器",
    "deterministic_fallback": "规则兜底",
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
:root {
    --ra-bg: #EDF1F5;
    --ra-panel: #F8FAFC;
    --ra-panel-soft: #F1F5F9;
    --ra-border: #CBD5E1;
    --ra-border-soft: #D9E2EC;
    --ra-text: #142033;
    --ra-muted: #526278;
    --ra-faint: #7A8798;
    --ra-accent: #078F80;
    --ra-accent-strong: #06756B;
    --ra-blue: #2563EB;
    --ra-green-soft: #DDF2EA;
    --ra-blue-soft: #E4EEF9;
    --ra-red: #C2413A;
    --ra-shadow: 0 8px 24px rgba(15, 23, 42, 0.07);
}

html, body, [data-testid="stAppViewContainer"] {
    background: var(--ra-bg);
    color: var(--ra-text);
}

[data-testid="stHeader"] {
    background: rgba(237, 241, 245, 0.88);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid rgba(203, 213, 225, 0.82);
}

[data-testid="stSidebar"] {
    background: #F3F6FA;
    border-right: 1px solid var(--ra-border);
}

[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span {
    color: var(--ra-text);
}

.block-container {
    max-width: 1260px;
    padding-top: 3.2rem;
    padding-bottom: 3rem;
}

h1, h2, h3, h4 {
    color: var(--ra-text);
    letter-spacing: 0;
}

div[data-testid="stButton"] > button,
div[data-testid="stDownloadButton"] > button {
    border-radius: 8px;
    min-height: 42px;
    border: 1px solid var(--ra-border);
    color: var(--ra-text);
    background: var(--ra-panel);
    box-shadow: none;
    font-weight: 600;
}

div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stButton"] > button[data-testid="baseButton-primary"] {
    background: var(--ra-accent);
    border-color: var(--ra-accent);
    color: #FFFFFF;
}

div[data-testid="stButton"] > button:hover,
div[data-testid="stDownloadButton"] > button:hover {
    border-color: var(--ra-accent);
    color: var(--ra-accent-strong);
}

div[data-testid="stButton"] > button[kind="primary"]:hover,
div[data-testid="stButton"] > button[data-testid="baseButton-primary"]:hover {
    background: var(--ra-accent-strong);
    border-color: var(--ra-accent-strong);
    color: #FFFFFF;
}

div[data-testid="stTextArea"] textarea,
div[data-baseweb="select"] > div,
div[data-testid="stTextInput"] input {
    border-radius: 8px;
    border-color: var(--ra-border);
    background: var(--ra-panel);
    color: var(--ra-text);
}

div[data-testid="stTextArea"] textarea:focus,
div[data-testid="stTextInput"] input:focus {
    border-color: var(--ra-accent);
    box-shadow: 0 0 0 2px rgba(8, 154, 134, 0.12);
}

div[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid var(--ra-border);
    gap: 18px;
}

div[data-testid="stTabs"] [role="tab"] {
    color: var(--ra-muted);
    font-weight: 700;
}

div[data-testid="stTabs"] [aria-selected="true"] {
    color: var(--ra-accent-strong);
}

.ra-shell-title {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 18px;
}

.ra-eyebrow {
    color: var(--ra-accent-strong);
    font-size: 13px;
    font-weight: 800;
    margin-bottom: 6px;
}

.ra-title {
    font-size: 34px;
    line-height: 1.12;
    font-weight: 800;
    color: var(--ra-text);
    margin: 0;
}

.ra-subtitle {
    color: var(--ra-muted);
    font-size: 15px;
    margin-top: 8px;
}

.ra-workflow {
    background: var(--ra-panel);
    border: 1px solid var(--ra-border);
    border-radius: 8px;
    padding: 16px 18px;
    margin: 8px 0 20px;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    box-shadow: var(--ra-shadow);
}

.ra-workflow-step {
    display: flex;
    gap: 12px;
    align-items: center;
    min-width: 0;
}

.ra-step-index {
    width: 34px;
    height: 34px;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #EFF3F8;
    color: var(--ra-muted);
    font-weight: 800;
}

.ra-workflow-step.active .ra-step-index,
.ra-workflow-step.done .ra-step-index {
    background: var(--ra-accent);
    color: #FFFFFF;
}

.ra-step-title {
    font-weight: 800;
    font-size: 14px;
    color: var(--ra-text);
}

.ra-step-caption {
    color: var(--ra-muted);
    font-size: 12px;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.ra-status-strip {
    background: var(--ra-panel);
    border: 1px solid var(--ra-border);
    border-radius: 8px;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    margin: 18px 0;
    box-shadow: var(--ra-shadow);
}

.ra-status-item {
    padding: 16px 18px;
    border-right: 1px solid var(--ra-border-soft);
}

.ra-status-item:last-child {
    border-right: none;
}

.ra-status-label {
    color: var(--ra-muted);
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 5px;
}

.ra-status-value {
    color: var(--ra-text);
    font-size: 17px;
    font-weight: 800;
}

.ra-status-note {
    color: var(--ra-faint);
    font-size: 12px;
    margin-top: 3px;
}

.ra-section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 0 0 12px;
}

.ra-section-title {
    font-size: 18px;
    font-weight: 800;
    color: var(--ra-text);
}

.ra-chip {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    background: var(--ra-green-soft);
    color: #067A63;
    font-size: 12px;
    font-weight: 800;
    padding: 5px 10px;
}

.ra-panel {
    background: var(--ra-panel);
    border: 1px solid var(--ra-border);
    border-radius: 8px;
    padding: 18px;
    min-height: 232px;
    box-shadow: var(--ra-shadow);
}

.ra-row {
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 11px 0;
    border-bottom: 1px solid var(--ra-border-soft);
}

.ra-row:last-child {
    border-bottom: none;
}

.ra-row-index {
    width: 26px;
    height: 26px;
    border-radius: 999px;
    background: #DEF7EC;
    color: #087F73;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
}

.ra-row-title {
    font-size: 14px;
    font-weight: 800;
    color: var(--ra-text);
}

.ra-row-caption {
    font-size: 12px;
    color: var(--ra-muted);
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.ra-tool-pill {
    border-radius: 999px;
    background: #F1F5F9;
    color: #475569;
    padding: 5px 9px;
    font-size: 11px;
    font-weight: 700;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.ra-empty-box {
    border: 1px dashed #CBD5E1;
    border-radius: 8px;
    min-height: 132px;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--ra-muted);
    background: var(--ra-panel-soft);
    padding: 18px;
}

.ra-empty-title {
    color: var(--ra-text);
    font-weight: 800;
    margin-bottom: 5px;
}

.ra-help-strip {
    background: var(--ra-blue-soft);
    color: #24548F;
    border: 1px solid #D7E8FF;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 13px;
    margin-top: 18px;
}

.ra-sidebar-card {
    border: 1px solid var(--ra-border);
    border-radius: 8px;
    padding: 14px;
    background: var(--ra-panel);
    margin: 12px 0;
}

.ra-sidebar-card strong {
    color: var(--ra-text);
}

.ra-sidebar-muted {
    color: var(--ra-muted);
    font-size: 12px;
    margin-top: 6px;
}

.ra-sidebar-ok {
    color: #087F73;
    font-size: 12px;
    font-weight: 800;
}

.step-card {
    border-left: 3px solid var(--ra-accent);
    padding: 10px 14px;
    margin: 8px 0;
    border-radius: 0 8px 8px 0;
    background: var(--ra-panel);
    border-top: 1px solid var(--ra-border-soft);
    border-right: 1px solid var(--ra-border-soft);
    border-bottom: 1px solid var(--ra-border-soft);
}
.step-card-success { border-left-color: #15803D; background: rgba(21,128,61,0.07); }
.step-card-failed  { border-left-color: #B91C1C; background: rgba(185,28,28,0.07); }
.step-card-rejected{ border-left-color: #B91C1C; background: rgba(185,28,28,0.07); }
.step-header { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.step-meta   { font-size: 12px; opacity: 0.7; }
.risk-badge  { display:inline-block; padding:1px 8px; border-radius:10px;
               font-size:11px; color:white; margin-left:6px; }
.section-tip { font-size:13px; color:var(--ra-muted); margin:-4px 0 12px; padding-left:2px; }
.metric-row  { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.metric-box  { flex:1; min-width:90px; background:var(--ra-panel);
               border:1px solid var(--ra-border); border-radius:8px;
               padding:10px 14px; }
.metric-label{ font-size:11px; color:var(--ra-muted); margin-bottom:2px; }
.metric-value{ font-size:20px; font-weight:600; }

@media (max-width: 920px) {
    .ra-workflow,
    .ra-status-strip {
        grid-template-columns: 1fr;
    }
    .ra-status-item {
        border-right: none;
        border-bottom: 1px solid var(--ra-border-soft);
    }
    .ra-status-item:last-child {
        border-bottom: none;
    }
    .ra-shell-title {
        display: block;
    }
    .ra-guide-pill {
        margin-top: 12px;
        display: inline-flex;
    }
}
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
        # ── Memory & Session state ──
        "active_session_id": "",
        "sessions": [],
        "sessions_loaded": False,
        "memory_list": None,
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
        parts.append(f"摘要={_friendly_summary(summary)[:180]}")
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


def render_event_summary() -> None:
    lines = st.session_state.get("event_log") or []
    if not lines:
        return
    latest = lines[-1]
    if len(lines) > 1:
        st.caption(f"已记录 {len(lines)} 条实时事件，最新：{latest}")
    else:
        st.caption(latest)


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


def _sync_template_state() -> None:
    """Switching a scenario starts a fresh demo context.

    The selectbox owns selected_template through its key, so this callback only
    synchronizes dependent state.
    """
    template = _current_template()
    st.session_state.allowed_tools = _template_allowed_tools(template)
    st.session_state.task_text = str(template.get("task") or "")
    for key, empty_value in {
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
    }.items():
        st.session_state[key] = empty_value


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


def _execution_mode_label(value: str | None) -> str:
    raw = str(value or "planned")
    return EXECUTION_MODE_CN.get(raw, raw)


def _planner_source_label(value: str | None) -> str:
    raw = str(value or "—")
    return PLANNER_SOURCE_CN.get(raw, raw)


def _remote_failures(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for trace in traces:
        tool_name = str(trace.get("tool_name") or "")
        if (
            trace.get("status") in {"failed", "rejected"}
            and ("source_pack." in tool_name or tool_name.startswith(("firecrawl.", "exa.", "context7.")))
        ):
            failures.append(trace)
    return failures


def _degradation_summary(plan: dict[str, Any], traces: list[dict[str, Any]]) -> tuple[str, str]:
    react_state = plan.get("react_state") if isinstance(plan.get("react_state"), dict) else {}
    if bool(react_state.get("fallback_used")):
        return "已降级", "ReAct 触发 fallback，任务由可追踪的兜底执行完成。"
    remote_failed = _remote_failures(traces)
    if remote_failed:
        return "部分降级", f"{len(remote_failed)} 个远端 MCP 调用失败，系统保留失败证据并继续使用可用来源。"
    return "未降级", "本次运行未记录远端工具失败或 fallback。"


def _html_text(value: Any) -> str:
    return html.escape(str(value or ""))


def _short_tool_name(tool_name: Any) -> str:
    value = str(tool_name or "—")
    for prefix in ("source_pack.",):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def _run_status_label(status_obj: dict[str, Any]) -> str:
    raw = str(status_obj.get("status") or "未创建")
    return STREAM_STATUS_CN.get(raw, raw)


def _mcp_status_snapshot() -> tuple[str, str, str]:
    try:
        mcp_health = api_get("/mcp/health", timeout=2)
        readonly = (
            mcp_health.get("channel_summary", {})
            .get("channels", {})
            .get("readonly", {})
        )
        registered = int(readonly.get("registered_tools") or 0)
        servers = int(readonly.get("configured_servers") or 0)
        if registered:
            return "MCP 工具", f"{registered} 个只读工具", f"{servers} 个服务已注册"
        if mcp_health.get("remote_registry_enabled"):
            return "MCP 工具", "待注册", "Bridge 已配置，等待发现"
        return "MCP 工具", "未配置", "使用内置搜索工具"
    except ApiError:
        return "MCP 工具", "状态未知", "后端连接后刷新"


def render_page_header() -> None:
    st.markdown(
        textwrap.dedent("""
        <div class="ra-shell-title">
            <div>
                <h1 class="ra-title">Traceable Research Agent</h1>
            </div>
        </div>
        """).strip(),
        unsafe_allow_html=True,
    )


def render_workflow_strip() -> None:
    plan = st.session_state.get("last_plan") or {}
    traces = st.session_state.get("last_trace") or []
    report = st.session_state.get("last_report") or {}
    steps = [
        ("1", "任务描述", "输入研究问题或资料来源", True),
        ("2", "执行计划", "生成可执行研究计划", bool(plan)),
        ("3", "执行追踪", "实时查看执行与证据链", bool(traces)),
        ("4", "研究报告", "生成结构化研究报告", bool(report.get("exists"))),
    ]
    html_steps = []
    for index, title, caption, done in steps:
        active = index == "1" and not bool(plan)
        class_name = "ra-workflow-step"
        if done:
            class_name += " done"
        elif active:
            class_name += " active"
        html_steps.append(
            textwrap.dedent(f"""
            <div class="{class_name}">
                <div class="ra-step-index">{index}</div>
                <div>
                    <div class="ra-step-title">{title}</div>
                    <div class="ra-step-caption">{caption}</div>
                </div>
            </div>
            """).strip()
        )
    st.markdown(f"<div class='ra-workflow'>{''.join(html_steps)}</div>", unsafe_allow_html=True)


def render_status_strip() -> None:
    status_obj = st.session_state.get("last_status") or {}
    plan = st.session_state.get("last_plan") or {}
    traces = st.session_state.get("last_trace") or []
    report = st.session_state.get("last_report") or {}
    mcp_label, mcp_value, mcp_note = _mcp_status_snapshot()
    planner_source = _planner_source_label(plan.get("planner_source") or status_obj.get("planner_source"))
    report_value = "已生成" if report.get("exists") else "待生成"
    items = [
        (mcp_label, mcp_value, mcp_note),
        ("Planner", planner_source if planner_source != "—" else "就绪", "可生成执行计划"),
        ("Trace", f"{len(traces)} 条", _run_status_label(status_obj)),
        ("研究报告", report_value, "Markdown / Word / PDF"),
    ]
    item_html = "".join(
        textwrap.dedent(f"""
        <div class="ra-status-item">
            <div class="ra-status-label">{_html_text(label)}</div>
            <div class="ra-status-value">{_html_text(value)}</div>
            <div class="ra-status-note">{_html_text(note)}</div>
        </div>
        """).strip()
        for label, value, note in items
    )
    st.markdown(f"<div class='ra-status-strip'>{item_html}</div>", unsafe_allow_html=True)


def _plan_rows_html(steps: list[dict[str, Any]]) -> str:
    rows = []
    preview_steps = steps[:4]
    if not preview_steps:
        preview_steps = [
            {
                "step_no": 1,
                "tool_name": "source_pack.firecrawl.search",
                "goal": "发现与主题相关的高质量网页与文献。",
            },
            {
                "step_no": 2,
                "tool_name": "source_pack.firecrawl.extract",
                "goal": "提取关键内容，去重与归纳要点。",
            },
            {
                "step_no": 3,
                "tool_name": "report_writer",
                "goal": "形成结构化报告并保留来源链接。",
            },
        ]
    for index, step in enumerate(preview_steps, start=1):
        step_no = step.get("step_no") or index
        tool_name = _short_tool_name(step.get("tool_name"))
        goal = _friendly_goal(step.get("goal") or step.get("completion_criteria") or "等待 Planner 生成计划。")
        rows.append(
            textwrap.dedent(f"""
            <div class="ra-row">
                <div class="ra-row-index">{_html_text(step_no)}</div>
                <div>
                    <div class="ra-row-title">{_html_text(tool_name)}</div>
                    <div class="ra-row-caption">{_html_text(goal)}</div>
                </div>
                <div class="ra-tool-pill">{_html_text(tool_name)}</div>
            </div>
            """).strip()
        )
    return "".join(rows)


def render_plan_preview_panel() -> None:
    plan = st.session_state.get("last_plan") or {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    badge = f"{len(steps)} 步" if steps else "预览"
    st.markdown(
        textwrap.dedent(f"""
        <div class="ra-panel">
            <div class="ra-section-head">
                <div class="ra-section-title">执行计划</div>
                <div class="ra-chip">{_html_text(badge)}</div>
            </div>
            {_plan_rows_html(steps)}
        </div>
        """).strip(),
        unsafe_allow_html=True,
    )


def render_trace_preview_panel() -> None:
    traces = st.session_state.get("last_trace") or []
    status_obj = st.session_state.get("last_status") or {}
    if traces:
        recent = traces[-4:]
        rows = []
        for index, trace in enumerate(recent, start=1):
            tool_name = _short_tool_name(trace.get("tool_name"))
            trace_status = STREAM_STATUS_CN.get(str(trace.get("status")), trace.get("status") or "未知")
            summary = _friendly_summary(trace.get("output_summary") or trace.get("error_message") or "已记录工具调用。")
            rows.append(
                textwrap.dedent(f"""
                <div class="ra-row">
                    <div class="ra-row-index">{index}</div>
                    <div>
                        <div class="ra-row-title">{_html_text(tool_name)} · {_html_text(trace_status)}</div>
                        <div class="ra-row-caption">{_html_text(summary)}</div>
                    </div>
                    <div class="ra-tool-pill">{_html_text(trace.get("latency_ms", "—"))} ms</div>
                </div>
                """).strip()
            )
        body = "".join(rows)
    else:
        body = textwrap.dedent("""
        <div class="ra-empty-box">
            <div>
                <div class="ra-empty-title">暂无执行记录</div>
                <div>创建并执行任务后，工具调用、状态和证据链会显示在这里。</div>
            </div>
        </div>
        """).strip()
    st.markdown(
        textwrap.dedent(f"""
        <div class="ra-panel">
            <div class="ra-section-head">
                <div class="ra-section-title">Trace 追踪</div>
                <div class="ra-chip">{_html_text(_run_status_label(status_obj))}</div>
            </div>
            {body}
        </div>
        """).strip(),
        unsafe_allow_html=True,
    )


def render_report_preview_panel() -> None:
    report = st.session_state.get("last_report") or {}
    evidence = st.session_state.get("last_evidence") or {}
    report_exists = bool(report.get("exists"))
    title = "报告已生成" if report_exists else "报告待生成"
    note = "可预览、下载 Markdown / Word / PDF。" if report_exists else "执行任务后，摘要、关键发现、证据链和来源链接会汇总在这里。"
    evidence_count = evidence.get("total_evidence_items", 0) if isinstance(evidence, dict) else 0
    body = textwrap.dedent(f"""
    <div class="ra-empty-box">
        <div>
            <div class="ra-empty-title">{_html_text(title)}</div>
            <div>{_html_text(note)}</div>
            <div style="margin-top:10px;color:#087F73;font-weight:800;">证据条目：{_html_text(evidence_count)}</div>
        </div>
    </div>
    """).strip()
    st.markdown(
        textwrap.dedent(f"""
        <div class="ra-panel">
            <div class="ra-section-head">
                <div class="ra-section-title">研究报告</div>
                <div class="ra-chip">{'已生成' if report_exists else '待生成'}</div>
            </div>
            {body}
        </div>
        """).strip(),
        unsafe_allow_html=True,
    )


def risk_badge(level: str) -> str:
    c = RISK_COLOR.get(level, "#6B7280")
    cn = {"low": "低", "medium": "中", "high": "高"}.get(level, level)
    return f"<span class='risk-badge' style='background:{c}'>风险 {cn}</span>"


def _friendly_summary(text: Any) -> str:
    value = str(text or "—")
    replacements = {
        "EXA_API_KEY is not configured.": "Exa 凭证未配置，本次记录为远端 MCP 失败。",
        "FIRECRAWL_API_KEY is not configured.": "Firecrawl 凭证未配置，本次记录为远端 MCP 失败。",
        "Report is ready.": "报告已生成。",
        "Returned": "返回",
        "row(s) with columns": "行，字段",
        "Read ": "已读取 ",
        " chars": " 个字符",
        "rag_search returned": "RAG 检索返回",
        "hits using": "条结果，模式",
        "tavily_search returned": "Tavily 搜索返回",
        "Tavily API results.": "条结果。",
    }
    for raw, localized in replacements.items():
        value = value.replace(raw, localized)
    return value


def _friendly_goal(goal: Any) -> str:
    value = str(goal or "")
    prefix = "Call remote MCP tool "
    suffix = " through the unified Tool Registry."
    if value.startswith(prefix) and value.endswith(suffix):
        tool_name = value[len(prefix):-len(suffix)]
        return f"通过统一工具注册表调用远端只读工具 {tool_name}。"
    if value == "Search current external web sources through the read-only Tavily API.":
        return "通过只读 Tavily 搜索当前外部来源。"
    return value


def _friendly_error(text: Any) -> str:
    value = str(text or "")
    if "EXA_API_KEY is not configured" in value:
        return "Exa 远端服务凭证未配置，已作为可追踪失败记录。"
    if "FIRECRAWL_API_KEY is not configured" in value:
        return "Firecrawl 远端服务凭证未配置，已作为可追踪失败记录。"
    return value


def plan_step_card(step: dict) -> None:
    tool  = step.get("tool_name", "")
    icon  = TOOL_ICON.get(tool, "🔧")
    cn    = TOOL_CN.get(tool, tool)
    goal  = _friendly_goal(step.get("goal", ""))
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
        f"输出：{_friendly_summary(trace.get('output_summary','—'))}"
        + (f"<br>⚠️ {_friendly_error(trace.get('error_message'))}" if trace.get("error_message") else "")
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
        with st.expander(f"并行执行组 {trace_meta.get('parallel_group_id')}", expanded=False):
            cols = st.columns(4)
            cols[0].metric("是否并行", "是" if trace_meta.get("parallel") else "否")
            cols[1].metric("工作线程", trace_meta.get("parallel_worker_id", "-"))
            cols[2].metric("组大小", trace_meta.get("parallel_group_size", "-"))
            cols[3].metric("耗时", f"{trace_meta.get('latency_ms', '-')} ms")
            st.caption(
                f"开始时间={trace_meta.get('started_at')} | "
                f"完成时间={trace_meta.get('finished_at')}"
            )

    out = trace.get("output") or {}
    if isinstance(out, dict):
        thought = out.get("thought") or (out.get("metadata") or {}).get("thought")
        if thought:
            with st.expander(f"🧠 ReAct 思考链（步骤 {trace.get('step_no')}）"):
                st.write(f"**思考摘要：** {thought}")
                st.write(f"**选择动作：**  {out.get('action', tool)}")
                obs = out.get("observation_summary") or trace.get("output_summary", "")
                st.write(f"**观察结果：** {_friendly_summary(obs)}")

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
            cols2[0].metric("Embedding 后端", meta.get("embedding_backend", "—"))
            cols2[1].metric("是否降级", "是" if meta.get("fallback_used") else "否")
            cols2[2].metric("RRF-k", meta.get("rrf_k", "—"))
            cols3 = st.columns(2)
            cols3[0].metric("向量维度", meta.get("dimension", "—"))
            cols3[1].metric("集合名", meta.get("collection_name", "—"))


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
                st.success("已生成证据包")
            except ApiError as exc:
                st.error(str(exc))
    last_export = st.session_state.get("last_evidence_export")
    content_payload = st.session_state.get("last_evidence_export_content")
    if isinstance(last_export, dict) and last_export.get("run_id") == run_id:
        st.caption(
            f"格式：{last_export.get('format')} | 条目：{last_export.get('item_count')} | "
            f"本地路径：{last_export.get('export_path')}"
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


# ── Session & Memory helpers ─────────────────────────────────────────

def _load_sessions() -> list[dict[str, Any]]:
    """Fetch session list from the backend."""
    try:
        return api_get("/api/sessions", timeout=5)
    except ApiError:
        return []


def _create_session(title: str | None = None) -> dict[str, Any] | None:
    """Create a new session via the backend."""
    try:
        return api_post("/api/sessions", {"title": title}, timeout=5)
    except ApiError:
        return None


def _load_memories(status: str | None = None) -> dict[str, Any] | None:
    """Fetch memory list from the backend."""
    try:
        params = f"?status={status}" if status else ""
        return api_get(f"/api/memory{params}", timeout=5)
    except ApiError:
        return None


def _confirm_memory(memory_id: str, approved: bool) -> bool:
    """Confirm or reject a pending memory."""
    try:
        api_post(f"/api/memory/{memory_id}/confirm", {"approved": approved}, timeout=5)
        return True
    except ApiError:
        return False


def _delete_memory(memory_id: str) -> bool:
    """Delete a single memory."""
    try:
        api_request("DELETE", f"/api/memory/{memory_id}", timeout=5)
        return True
    except ApiError:
        return False


# ── 侧边栏 ────────────────────────────────────────────────────────
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## Traceable Research Agent")

        # 健康检查
        if st.button("检查后端连接", use_container_width=True):
            try:
                h = api_get("/health")
                st.session_state.health = h
                st.success(f"连接正常 · 默认模式：{h.get('execution_mode','planned')}")
            except ApiError as e:
                st.error(str(e))

        st.divider()

        # ── 会话切换器 ──────────────────────────────────────────────
        st.markdown("**会话**")
        if not st.session_state.get("sessions_loaded"):
            st.session_state.sessions = _load_sessions()
            st.session_state.sessions_loaded = True

        sessions = st.session_state.get("sessions") or []
        session_options = ["（新建会话）"] + [
            f"{s.get('title') or '未命名'} ({s['session_id'][:8]}…)"
            for s in sessions
        ]
        session_labels = {opt: s for opt, s in zip(session_options, [None] + sessions)}

        selected_label = st.selectbox(
            "选择会话",
            session_options,
            key="session_selector",
            label_visibility="collapsed",
        )

        if selected_label == "（新建会话）":
            if st.button("新建会话", use_container_width=True):
                new_session = _create_session()
                if new_session:
                    st.session_state.active_session_id = new_session["session_id"]
                    st.session_state.sessions_loaded = False  # force reload
                    st.success("会话已创建")
                    st.rerun()
                else:
                    st.error("创建会话失败")
        else:
            selected = session_labels.get(selected_label)
            if selected:
                st.session_state.active_session_id = selected["session_id"]
                st.caption(f"当前会话：{selected.get('title') or '未命名'} ({selected['session_id'][:8]}…)")
            if st.button("刷新会话列表", use_container_width=True):
                st.session_state.sessions_loaded = False
                st.rerun()

        st.divider()

        # ── 记忆面板 ────────────────────────────────────────────────
        st.markdown("**用户记忆**")
        if st.button("加载记忆", use_container_width=True):
            st.session_state.memory_list = _load_memories()

        memory_list = st.session_state.get("memory_list")
        if memory_list:
            total = memory_list.get("total", 0)
            active = memory_list.get("active_count", 0)
            pending = memory_list.get("pending_count", 0)
            st.caption(f"共 {total} 条记忆 · {active} 条活跃 · {pending} 条待确认")

            if total == 0:
                st.info("完成 3 次调研后，系统将开始为您总结偏好。", icon="🧠")

            memories = memory_list.get("memories") or []
            for mem in memories:
                status_icon = {"active": "✅", "pending": "⏳", "superseded": "📦", "expired": "⏰"}.get(
                    mem.get("status"), "❓"
                )
                with st.expander(
                    f"{status_icon} [{mem.get('kind', '?')}] {mem.get('content', '')[:60]}…",
                    expanded=False,
                ):
                    st.caption(f"类型：{mem.get('kind')} | 方式：{mem.get('extraction_method')}")
                    st.caption(f"置信度：{mem.get('confidence', 0):.1f} | 状态：{mem.get('status')}")
                    st.text(mem.get("content", ""))
                    if mem.get("source_run_id"):
                        st.caption(f"来源 Run：{mem['source_run_id'][:16]}…")
                    if mem.get("status") == "pending":
                        c1, c2 = st.columns(2)
                        if c1.button("确认", key=f"confirm_{mem['memory_id']}"):
                            if _confirm_memory(mem["memory_id"], True):
                                st.session_state.memory_list = _load_memories()
                                st.rerun()
                        if c2.button("拒绝", key=f"reject_{mem['memory_id']}"):
                            if _confirm_memory(mem["memory_id"], False):
                                st.session_state.memory_list = _load_memories()
                                st.rerun()
        else:
            st.caption("点击「加载记忆」查看")

        st.divider()
        st.markdown("**场景模板**")
        st.selectbox(
            "选择演示场景",
            list(DEMO_TEMPLATES.keys()),
            key="selected_template",       # Streamlit 独占管理此 key，禁止在回调外赋值
            on_change=_sync_template_state,
            label_visibility="collapsed",
        )
        template_description = str(_current_template().get("description") or "")
        if template_description:
            st.caption(template_description)
        scenario_key = _current_scenario_template_key()
        if scenario_key in {"deep_web_research", "technical_docs_research"}:
            try:
                mcp_health = api_get("/mcp/health", timeout=3)
                readonly = (
                    mcp_health.get("channel_summary", {})
                    .get("channels", {})
                    .get("readonly", {})
                )
                registered = int(readonly.get("registered_tools") or 0)
                servers = int(readonly.get("configured_servers") or 0)
                if registered:
                    st.markdown(
                        textwrap.dedent(f"""
                        <div class="ra-sidebar-card">
                            <strong>MCP 已注册</strong>
                            <div class="ra-sidebar-ok">{registered} 个只读工具 · {servers} 个服务</div>
                        </div>
                        """).strip(),
                        unsafe_allow_html=True,
                    )
                    if scenario_key == "deep_web_research":
                        st.caption("Exa 负责发现候选来源；Firecrawl 负责搜索和有 URL 时的网页正文读取。")
                    else:
                        st.caption("技术文档场景会优先使用 GitHub/RAG/搜索；Context7 adapter 已预留，未注册时不会强行调用。")
                else:
                    enabled = bool(mcp_health.get("remote_registry_enabled"))
                    if enabled:
                        st.warning("远端 MCP 已配置但尚未注册，Bridge 可能刚启动完成。", icon="⚠️")
                        if st.button("重新注册远端 MCP", use_container_width=True):
                            try:
                                refreshed = api_post("/mcp/refresh", timeout=10)
                                summary = (
                                    refreshed.get("channel_summary", {})
                                    .get("channels", {})
                                    .get("readonly", {})
                                )
                                refreshed_count = int(summary.get("registered_tools") or 0)
                                if refreshed_count:
                                    st.success(f"远端 MCP 已注册：{refreshed_count} 个只读工具")
                                    st.rerun()
                                else:
                                    st.warning("仍未发现可注册的远端 MCP 工具，请确认 Bridge 窗口已启动。")
                            except ApiError as exc:
                                st.error(str(exc))
                    else:
                        st.warning("远端 MCP 未配置，本场景会降级到内置搜索工具。", icon="⚠️")
            except ApiError:
                st.caption("MCP 状态暂不可用")

        st.divider()
        st.markdown("**执行模式**")
        st.selectbox(
            "执行模式",
            ["planned", "react"],
            format_func=lambda x: "固定计划（推荐）" if x == "planned" else "ReAct 动态决策",
            key="execution_mode_display",
            label_visibility="collapsed",
        )
        st.caption("先生成计划，经确认后执行；过程可追踪、可干预。")

        st.divider()
        if st.session_state.get("run_id"):
            st.markdown(
                textwrap.dedent(f"""
                <div class="ra-sidebar-card">
                    <strong>会话信息</strong>
                    <div class="ra-sidebar-muted">Run ID：{_html_text(st.session_state.run_id[:16])}…</div>
                </div>
                """).strip(),
                unsafe_allow_html=True,
            )
            if st.button("刷新全部", use_container_width=True):
                refresh_all()
                # 不显式 st.rerun()：按钮点击本身会触发 Streamlit 的一次 rerun
                # 避免双重 rerun 导致 selectbox index 重计算、task_text 被覆盖
        if st.button("清空会话", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            init_state()
            st.rerun()


# ── Tab 1：任务与规划 ─────────────────────────────────────────────
def tab_task() -> None:
    st.markdown("### 输入任务")
    st.markdown('<p class="section-tip">描述研究目标或粘贴资料来源。URL 建议单独一行，Planner 会据此决定是否进入网页正文读取。</p>', unsafe_allow_html=True)

    st.text_area(
        "任务内容",
        height=132,
        key="task_text",
        label_visibility="collapsed",
    )
    task_text = st.session_state.task_text  # 从 session state 读取，避免 value= 与 key= 冲突

    col1, col2, col3 = st.columns([1.15, 1.15, 5])
    with col1:
        if st.button("创建任务", type="primary", use_container_width=True):
            payload = {
                "task": task_text,
                "allowed_tools": st.session_state.allowed_tools,
                "report_type": "summary",
                "source_mode": st.session_state.get("source_mode_ui", "real"),
                "execution_mode_override": st.session_state.execution_mode_display,
                "scenario_template": st.session_state.get("selected_template", ""),
                "scenario_template_key": _current_scenario_template_key(),
                "session_id": st.session_state.get("active_session_id") or None,
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
                refresh_all(show_errors=False)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))

    with col2:
        run_id = st.session_state.get("run_id", "")
        if st.button("执行任务", type="primary", use_container_width=True, disabled=not bool(run_id)):
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
                refresh_all(show_errors=False)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
    with col3:
        # 暂时隐藏实时事件摘要，避免在控制台首屏出现“请求执行/状态=运行”等内部状态文案。
        pass

    # 当前状态摘要
    status_obj = st.session_state.get("last_status")
    if status_obj:
        cur = status_obj.get("status", "unknown")
        # 暂时隐藏状态胶囊，减少控制台内部运行状态噪声。
        # st.markdown(status_chip(cur), unsafe_allow_html=True)
        if cur == "waiting_human":
            _render_hitl()

    render_status_strip()

    plan = st.session_state.get("last_plan")
    # 图 3 圈出的三列功能看板暂时移除；完整计划、Trace 和报告仍保留在各自 Tab 中查看。
    if not plan and st.session_state.get("run_id"):
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
        raw_status = status_obj.get("status", "-")
        cols[0].metric("任务状态", STREAM_STATUS_CN.get(str(raw_status), raw_status))
        cols[1].metric("当前步骤", status_obj.get("current_step", 0))
        cols[2].metric("Trace 条数", len(traces))
        cols[3].metric(
            "自动轮询",
            "开启" if st.session_state.get("realtime_auto_refresh") else "关闭",
        )
        if traces:
            latest = traces[-1]
            latest_status = STREAM_STATUS_CN.get(str(latest.get("status")), latest.get("status"))
            st.caption(
                f"最新工具={latest.get('tool_name')} | 状态={latest_status} | "
                f"完成时间={latest.get('finished_at')}"
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

    # Group traces by sub_query if present
    sub_queries: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []
    for trace in traces:
        sq = trace.get("sub_query")
        if sq:
            sub_queries.setdefault(str(sq), []).append(trace)
        else:
            ungrouped.append(trace)

    if sub_queries:
        for sq_label, sq_traces in sub_queries.items():
            with st.expander(f"📋 子查询: {sq_label[:120]} ({len(sq_traces)} 步)", expanded=False):
                for trace in sq_traces:
                    trace_step_card(trace)
        if ungrouped:
            st.markdown("*其他步骤*")
            for trace in ungrouped:
                trace_step_card(trace)
    else:
        for trace in traces:
            trace_step_card(trace)

    st.divider()
    # 执行元数据摘要
    plan_source = status_obj.get("planner_source", "—")
    exec_mode   = status_obj.get("execution_mode", "planned")
    with st.expander("📊 执行元信息（点击展开）", expanded=False):
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("规划器来源", _planner_source_label(plan_source))
        mc2.metric("执行模式",   _execution_mode_label(exec_mode))
        mc3.metric("总延迟",     f"{status_obj.get('total_latency_ms', '—')} ms")
        if status_obj.get("llm_provider"):
            mc1.metric("LLM 提供商", status_obj.get("llm_provider"))
            mc2.metric("LLM 模型",   status_obj.get("llm_model", "—"))


FOLDED_REPORT_SECTION_PREFIXES = ("## 4.", "## 5.", "## 6.", "## 7.", "## 8.")


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
    traces = st.session_state.get("last_trace") or []
    degradation_label, degradation_note = _degradation_summary(plan, traces)

    col1, col2, col3 = st.columns(3)
    col1.metric("执行模式", _execution_mode_label(exec_mode))
    col2.metric("请求执行模式", _execution_mode_label(requested_mode))
    col3.metric("降级状态", degradation_label)
    st.caption(degradation_note)

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

    render_page_header()
    render_workflow_strip()

    tab1, tab2, tab3 = st.tabs(["任务与规划", "执行追踪", "研究报告"])
    with tab1:  tab_task()
    with tab2:  tab_trace()
    with tab3:  tab_report()
    maybe_auto_refresh()


if __name__ == "__main__":
    main()
