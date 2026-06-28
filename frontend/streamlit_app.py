"""Traceable Research Agent"""

from __future__ import annotations

import json
import os
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
ALL_TOOLS = ["file_reader", "sql_query", "rag_search", "mcp_github_search", "report_writer"]

TOOL_ICON = {
    "file_reader":      "📄",
    "sql_query":        "🗄️",
    "rag_search":       "🔍",
    "mcp_github_search":"🐙",
    "report_writer":    "📝",
}
TOOL_CN = {
    "file_reader":      "本地文件读取",
    "sql_query":        "数据库查询",
    "rag_search":       "RAG 向量检索",
    "mcp_github_search":"GitHub 只读调研",
    "report_writer":    "Markdown 报告生成",
}
RISK_COLOR = {"low": "#15803D", "medium": "#B45309", "high": "#B91C1C"}

DEMO_TEMPLATES: dict[str, dict[str, Any]] = {
    "📄 标准调研（文件+SQL+RAG+报告）": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report",
        "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
    },
    "🐙 GitHub 只读调研报告": {
        "task": "Search GitHub repository issues about traceable research agent and generate a markdown report",
        "allowed_tools": ["mcp_github_search", "report_writer"],
    },
    "✋ HITL 人工确认流程": {
        "task": "Read local docs, retrieve trace evidence, and generate a markdown report with human approval",
        "allowed_tools": ["file_reader", "rag_search", "report_writer"],
    },
    "⚡ LLM 规划器（全工具）": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, search GitHub repository issues, and generate a markdown report",
        "allowed_tools": ALL_TOOLS,
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

RAG_METADATA_FIELDS = [
    "retrieval_mode", "embedding_backend", "vector_backend",
    "fallback_used", "dense_hit_count", "bm25_hit_count", "rrf_k",
    "dimension", "collection_name",
]

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
        "last_report": None,
        "selected_template": list(DEMO_TEMPLATES.keys())[0],
        "task_text": list(DEMO_TEMPLATES.values())[0]["task"],
        "allowed_tools": list(DEMO_TEMPLATES.values())[0]["allowed_tools"],
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


def _sync_allowed_tools() -> None:
    """on_change callback for the template selectbox — syncs allowed_tools only.
    Called automatically by Streamlit when the selectbox value changes.
    Must NOT touch st.session_state.selected_template (widget owns it via key=).
    """
    name = st.session_state.get("selected_template", list(DEMO_TEMPLATES.keys())[0])
    t = DEMO_TEMPLATES.get(name) or list(DEMO_TEMPLATES.values())[0]
    st.session_state.allowed_tools = list(t["allowed_tools"])


def apply_template(name: str, fill_task: bool = False) -> None:
    """Apply a template. Only overwrites task_text when fill_task=True (user explicitly clicked).
    NOTE: Never sets st.session_state.selected_template — Streamlit owns it via key=.
    """
    t = DEMO_TEMPLATES.get(name) or list(DEMO_TEMPLATES.values())[0]
    if fill_task:
        st.session_state.task_text = t["task"]
    st.session_state.allowed_tools = list(t["allowed_tools"])


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
    with st.expander("Research Evidence Aggregation", expanded=False):
        cols = st.columns(4)
        cols[0].metric("evidence", evidence.get("total_evidence_items", 0))
        cols[1].metric("source groups", len(groups))
        cols[2].metric("claims", len(claims))
        cols[3].metric("unsupported", len(unsupported))
        if warnings:
            for warning in warnings:
                st.warning(warning)
        if groups:
            st.dataframe(groups, use_container_width=True, hide_index=True)
        if claims:
            preview = [
                {
                    "claim_id": item.get("claim_id"),
                    "support": item.get("support_level"),
                    "evidence": ", ".join(item.get("evidence_ids") or []),
                    "claim": item.get("claim"),
                }
                for item in claims[:8]
            ]
            st.dataframe(preview, use_container_width=True, hide_index=True)


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
                st.caption("当前任务实际执行模式以左侧“执行模式”选择为准。")
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
        if st.button("📋 填充示例任务文本", use_container_width=True,
                     help="将下方任务框替换为当前场景的示例任务（不影响已输入的自定义内容）"):
            apply_template(st.session_state.selected_template, fill_task=True)
            st.rerun()

        st.divider()
        st.markdown("**⚙️ 执行模式**")
        st.selectbox(
            "执行模式",
            ["planned", "react"],
            format_func=lambda x: "📋 Planned（固定计划）" if x == "planned" else "🤖 ReAct（动态决策）",
            key="execution_mode_display",
            label_visibility="collapsed",
        )
        if st.session_state.execution_mode_display == "react":
            st.caption("🤖 创建任务时将以 ReAct 模式运行，每步由 LLM 动态决策。需后端已配置 QWEN_API_KEY 或 DEEPSEEK_API_KEY。")
        else:
            st.caption("📋 创建任务时将以 Planned 模式运行，Planner 一次性生成固定执行计划。")

        st.divider()
        st.markdown("**🌐 数据来源**")
        st.selectbox(
            "数据来源",
            ["real", "mock"],
            format_func=lambda x: "🌐 real（真实 GitHub / Tavily API）" if x == "real" else "🧪 mock（离线演示数据）",
            key="source_mode_ui",
            label_visibility="collapsed",
        )
        if st.session_state.get("source_mode_ui") == "real":
            st.caption("⚠️ real 模式调用真实 API，受 rate limit 限制，需配置相应 Key。")
        else:
            st.caption("🧪 mock 模式使用本地离线数据，无需 API Key，适合演示。")

        st.divider()
        with st.expander("🔧 高级配置（API 连接）"):
            st.session_state.api_base_url = st.text_input(
                "API Base URL", value=st.session_state.api_base_url
            )
            st.text_input("API Key", key="api_key", type="password")
            st.text_input("Tenant ID", key="tenant_id")
            st.text_input("User ID",   key="user_id")
            st.checkbox("异步执行（推荐开启，避免超时）", key="use_async_run")
            st.checkbox("Realtime auto refresh", key="realtime_auto_refresh")
            st.slider("Realtime poll seconds", 1, 10, key="realtime_poll_seconds")

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
            }
            try:
                with st.spinner("正在创建任务并调用 Planner 生成执行计划，标准调研可能需要 30-90 秒..."):
                    resp = api_post("/api/tasks", payload, timeout=CREATE_TASK_TIMEOUT_SECONDS)
                st.session_state.last_task_response = resp
                st.session_state.run_id = resp.get("run_id", "")
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


def _render_hitl() -> None:
    st.warning("✋ 任务正在等待人工确认，请确认后继续执行。")
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
        "Realtime trace stream",
        expanded=bool(status_obj.get("status") in ("pending", "running", "waiting_human")),
    ):
        st.caption(
            "Backend SSE endpoint is available for external clients; "
            "this Streamlit panel uses lightweight auto-refresh."
        )
        if st.session_state.get("run_id"):
            st.code(realtime_events_url(), language=None)
        cols = st.columns(4)
        cols[0].metric("status", status_obj.get("status", "-"))
        cols[1].metric("current step", status_obj.get("current_step", 0))
        cols[2].metric("trace events", len(traces))
        cols[3].metric(
            "auto refresh",
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


# ── Tab 3：研究报告 ───────────────────────────────────────────────
def tab_report() -> None:
    st.markdown("#### 📝 Markdown 研究报告")
    st.markdown('<p class="section-tip">Reporter 根据 Trace 记录和工具返回结果组织报告，包含任务、执行计划、证据来源、Trace 汇总和运行时限制说明。</p>', unsafe_allow_html=True)

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
    # total_tool_calls excludes ReAct "finish" steps; use trace count as fallback
    steps_done = (status_obj.get("total_tool_calls") or
                  len(st.session_state.get("last_trace") or []) or
                  status_obj.get("total_steps", 0))

    # Show trace count (actual steps recorded) rather than tool_calls (excludes finish steps)
    trace_count = len(st.session_state.get("last_trace") or [])
    plan_steps  = len((st.session_state.get("last_plan") or {}).get("steps") or [])
    display_steps = trace_count or steps_done or plan_steps
    exec_mode = status_obj.get("execution_mode", status_obj.get("requested_execution_mode", "planned"))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("计划步骤数", plan_steps)
    col2.metric("实际执行步骤", display_steps)
    col3.metric("任务状态", status_obj.get("status", "completed"))
    col4.metric("执行模式", exec_mode)

    render_evidence_summary()

    st.divider()
    st.markdown(md)
    st.download_button(
        "⬇️ 下载 Markdown 报告",
        data=md,
        file_name=f"research_report_{st.session_state.get('run_id','')[:8]}.md",
        mime="text/markdown",
    )


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
    st.markdown(
        "可追踪调研智能体后端 &nbsp;·&nbsp; "
        "任务创建 → 规划 → 工具执行 → Trace 记录 → 研究报告"
    )
    st.divider()

    tab1, tab2, tab3 = st.tabs(["⚡ 任务与规划", "🔍 执行追踪", "📝 研究报告"])
    with tab1:  tab_task()
    with tab2:  tab_trace()
    with tab3:  tab_report()
    maybe_auto_refresh()


if __name__ == "__main__":
    main()
