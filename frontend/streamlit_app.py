"""Streamlit demo UI for the Traceable Research Agent FastAPI backend."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
import streamlit as st


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
ALL_TOOLS = [
    "file_reader",
    "sql_query",
    "rag_search",
    "mcp_github_search",
    "report_writer",
]
RAG_METADATA_FIELDS = [
    "embedding_backend",
    "vector_backend",
    "requested_embedding_backend",
    "requested_vector_backend",
    "fallback_used",
    "dimension",
    "model_path",
    "persist_dir",
    "collection_name",
]

DEMO_TEMPLATES: dict[str, dict[str, Any]] = {
    "Normal file/sql/rag/report": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report",
        "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
    },
    "GitHub mock report": {
        "task": "Search GitHub repository issues about traceable research agent and generate a markdown report",
        "allowed_tools": ["mcp_github_search", "report_writer"],
    },
    "HITL report": {
        "task": "Read local docs, retrieve trace evidence, and generate a markdown report with human approval",
        "allowed_tools": ["file_reader", "rag_search", "report_writer"],
    },
    "LLM planner full tools": {
        "task": "Read local docs, query database metrics, retrieve trace evidence, search GitHub repository issues, and generate a markdown report",
        "allowed_tools": ALL_TOOLS,
    },
}


class ApiError(Exception):
    """User-facing API error for Streamlit rendering."""


def init_state() -> None:
    defaults = {
        "api_base_url": os.environ.get("STREAMLIT_API_BASE_URL", DEFAULT_API_BASE_URL),
        "api_key": "",
        "tenant_id": "demo",
        "user_id": "local-user",
        "use_async_run": False,
        "run_id": "",
        "last_task_response": None,
        "last_run_response": None,
        "last_status": None,
        "last_plan": None,
        "last_trace": [],
        "last_report": None,
        "selected_template": "Normal file/sql/rag/report",
        "task_text": DEMO_TEMPLATES["Normal file/sql/rag/report"]["task"],
        "allowed_tools": DEMO_TEMPLATES["Normal file/sql/rag/report"]["allowed_tools"],
        "report_type": "summary",
        "source_mode": "mock",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def api_url(path: str) -> str:
    base = st.session_state.api_base_url.rstrip("/")
    return f"{base}{path}"


def api_get(path: str) -> Any:
    return api_request("GET", path)


def api_post(path: str, payload: dict[str, Any] | None = None) -> Any:
    return api_request("POST", path, payload or {})


def request_headers() -> dict[str, str]:
    """Build request-scoped headers without rendering or persisting credentials."""

    headers: dict[str, str] = {}
    api_key = st.session_state.get("api_key", "").strip()
    tenant_id = st.session_state.get("tenant_id", "").strip()
    user_id = st.session_state.get("user_id", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    if user_id:
        headers["X-User-ID"] = user_id
    return headers


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    try:
        response = requests.request(
            method,
            api_url(path),
            json=payload,
            headers=request_headers(),
            timeout=30,
        )
    except requests.ConnectionError as exc:
        raise ApiError(
            "Backend is not reachable. Please start FastAPI first: "
            "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
        ) from exc
    except requests.Timeout as exc:
        raise ApiError("Backend request timed out.") from exc
    except requests.RequestException as exc:
        raise ApiError(f"Backend request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise ApiError(f"Backend returned non-JSON response with status {response.status_code}.") from exc

    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ApiError(f"HTTP {response.status_code}: {detail}")
    return data


def normalize_trace_response(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def extract_trace_metadata(trace: dict[str, Any]) -> dict[str, Any]:
    """Extract backend metadata from current and backward-compatible trace shapes."""

    candidates = [trace.get("metadata"), trace.get("output")]
    output = trace.get("output")
    if isinstance(output, dict):
        candidates.insert(1, output.get("metadata"))
    selected: dict[str, Any] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for field in RAG_METADATA_FIELDS:
            if field in candidate and candidate[field] is not None:
                selected[field] = candidate[field]
    return selected


def render_json(data: Any) -> None:
    st.json(data, expanded=False)


def status_badge(status: str | None) -> str:
    status = status or "unknown"
    colors = {
        "pending": "#6b7280",
        "running": "#2563eb",
        "waiting_human": "#b45309",
        "completed": "#15803d",
        "failed": "#b91c1c",
        "success": "#15803d",
        "rejected": "#b91c1c",
    }
    color = colors.get(status, "#374151")
    return (
        f"<span style='display:inline-block;padding:0.18rem 0.45rem;"
        f"border-radius:0.35rem;background:{color};color:white;"
        f"font-size:0.82rem'>{status}</span>"
    )


def show_api_error(error: Exception) -> None:
    st.error(str(error))


def refresh_current_run(show_errors: bool = True) -> None:
    run_id = st.session_state.get("run_id")
    if not run_id:
        return
    try:
        st.session_state.last_status = api_get(f"/api/tasks/{run_id}")
        st.session_state.last_plan = api_get(f"/api/tasks/{run_id}/plan")
        st.session_state.last_trace = normalize_trace_response(api_get(f"/api/tasks/{run_id}/trace"))
        st.session_state.last_report = api_get(f"/api/reports/{run_id}")
    except ApiError as exc:
        if show_errors:
            show_api_error(exc)


def apply_template(template_name: str) -> None:
    template = DEMO_TEMPLATES[template_name]
    st.session_state.task_text = template["task"]
    st.session_state.allowed_tools = list(template["allowed_tools"])
    st.session_state.selected_template = template_name


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Controls")
        st.session_state.api_base_url = st.text_input(
            "API Base URL",
            value=st.session_state.api_base_url,
            help="FastAPI backend URL. This UI only calls HTTP APIs.",
        )
        st.text_input(
            "API Key",
            key="api_key",
            type="password",
            help="Optional. Kept only in this Streamlit session and sent as X-API-Key.",
        )
        st.text_input("Tenant ID", key="tenant_id")
        st.text_input("User ID", key="user_id")
        st.checkbox("Use async run", key="use_async_run")
        if st.button("Health Check", use_container_width=True):
            try:
                st.session_state.health = api_get("/health")
            except ApiError as exc:
                show_api_error(exc)

        selected = st.selectbox(
            "Demo Template",
            list(DEMO_TEMPLATES),
            index=list(DEMO_TEMPLATES).index(st.session_state.selected_template),
        )
        if selected != st.session_state.selected_template:
            apply_template(selected)
            st.rerun()

        st.text_input("Current run_id", value=st.session_state.get("run_id", ""), disabled=True)
        if st.button("Refresh", use_container_width=True):
            refresh_current_run()
        if st.button("Clear Session", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_state()
            st.rerun()


def render_health_panel() -> None:
    st.subheader("Backend Health")
    health = st.session_state.get("health")
    if health is None:
        try:
            health = api_get("/health")
            st.session_state.health = health
        except ApiError as exc:
            show_api_error(exc)
            return

    cols = st.columns(3)
    cols[0].metric("status", health.get("status", "unknown"))
    cols[1].metric("service", health.get("service", "unknown"))
    cols[2].metric("phase", health.get("phase", "unknown"))


def render_task_creation() -> None:
    st.subheader("Create Task")
    st.text_area("Task", key="task_text", height=110)
    col1, col2 = st.columns(2)
    with col1:
        st.text_input("report_type", key="report_type")
    with col2:
        st.text_input("source_mode", key="source_mode")

    st.multiselect(
        "allowed_tools",
        ALL_TOOLS,
        key="allowed_tools",
    )

    if st.button("Create Task", type="primary"):
        payload = {
            "task": st.session_state.task_text,
            "report_type": st.session_state.report_type,
            "source_mode": st.session_state.source_mode,
            "allowed_tools": st.session_state.allowed_tools,
        }
        try:
            response = api_post("/api/tasks", payload)
            st.session_state.last_task_response = response
            st.session_state.run_id = response["run_id"]
            st.success(f"Created task: {response['run_id']}")
            render_json(response)
            refresh_current_run(show_errors=False)
            st.rerun()
        except ApiError as exc:
            show_api_error(exc)

    if st.session_state.get("last_task_response"):
        st.caption("Latest create response")
        render_json(st.session_state.last_task_response)


def render_plan_viewer() -> None:
    st.subheader("Plan")
    run_id = st.session_state.get("run_id")
    if not run_id:
        st.info("Create or paste a run_id first.")
        return

    if st.button("Refresh Plan"):
        try:
            st.session_state.last_plan = api_get(f"/api/tasks/{run_id}/plan")
        except ApiError as exc:
            show_api_error(exc)
            return

    plan = st.session_state.get("last_plan")
    if not plan:
        st.info("No plan loaded yet.")
        return

    planner_source = plan.get("planner_source") or "deterministic"
    if planner_source == "llm":
        st.success("LLM Planner active")
    elif planner_source == "deterministic_fallback":
        st.warning("LLM Planner fallback to deterministic")
    else:
        st.info("Deterministic planner mode")

    cols = st.columns(4)
    cols[0].metric("planner_source", planner_source)
    cols[1].metric("llm_provider", plan.get("llm_provider") or "-")
    cols[2].metric("llm_model", plan.get("llm_model") or "-")
    cols[3].metric("steps", len(plan.get("steps") or []))

    st.write(
        {
            "run_id": plan.get("run_id"),
            "version": plan.get("version"),
            "source_mode": plan.get("source_mode"),
            "allowed_tools": plan.get("allowed_tools"),
            "notes": plan.get("notes"),
            "confirmation": plan.get("confirmation"),
        }
    )

    steps = plan.get("steps") or []
    if steps:
        table_rows = [
            {
                "step_no": step.get("step_no"),
                "tool_name": step.get("tool_name"),
                "goal": step.get("goal"),
                "risk_level": step.get("risk_level"),
                "requires_confirmation": step.get("requires_confirmation"),
                "completion_criteria": step.get("completion_criteria"),
            }
            for step in steps
        ]
        st.dataframe(table_rows, use_container_width=True, hide_index=True)
        for step in steps:
            with st.expander(f"Step {step.get('step_no')}: {step.get('tool_name')} arguments"):
                render_json(step.get("arguments") or {})
    else:
        st.warning("Plan has no executable steps.")


def render_run_executor() -> None:
    st.subheader("Run")
    run_id = st.session_state.get("run_id")
    if not run_id:
        st.info("Create or paste a run_id first.")
        return

    if st.button("Run Task", type="primary"):
        try:
            run_path = (
                f"/api/tasks/{run_id}/run_async"
                if st.session_state.use_async_run
                else f"/api/tasks/{run_id}/run"
            )
            response = api_post(run_path, {})
            st.session_state.last_run_response = response
            st.markdown(status_badge(response.get("status")), unsafe_allow_html=True)
            render_json(response)
            if st.session_state.use_async_run and response.get("status") == "running":
                st.info("Async run started. Use Refresh to poll status, trace, and report.")
            refresh_current_run(show_errors=False)
            st.rerun()
        except ApiError as exc:
            show_api_error(exc)

    if st.session_state.get("last_run_response"):
        response = st.session_state.last_run_response
        status = response.get("status")
        if status == "completed":
            st.success("Run completed.")
        elif status == "waiting_human":
            st.warning("Run is waiting for human confirmation.")
        elif status == "failed":
            st.error("Run failed.")
        else:
            st.info(f"Run status: {status}")
        render_json(response)


def render_status_panel() -> None:
    st.subheader("Status")
    run_id = st.session_state.get("run_id")
    if not run_id:
        st.info("No active run.")
        return
    if st.button("Refresh Status"):
        try:
            st.session_state.last_status = api_get(f"/api/tasks/{run_id}")
        except ApiError as exc:
            show_api_error(exc)
            return
    status = st.session_state.get("last_status")
    if not status:
        st.info("No status loaded yet.")
        return
    st.markdown(status_badge(status.get("status")), unsafe_allow_html=True)
    fields = {
        key: status.get(key)
        for key in [
            "run_id",
            "status",
            "current_step",
            "total_steps",
            "total_tool_calls",
            "total_latency_ms",
            "report_path",
            "error_message",
        ]
    }
    render_json(fields)


def render_hitl_panel() -> None:
    status = st.session_state.get("last_status") or {}
    run_response = st.session_state.get("last_run_response") or {}
    current_status = status.get("status") or run_response.get("status")
    if current_status != "waiting_human":
        return

    st.subheader("Human Confirmation")
    approved = st.checkbox("approved", value=True)
    resume = st.checkbox("resume", value=True)
    comment = st.text_input("comment", value="Approved from Streamlit UI.")
    if st.button("Confirm"):
        run_id = st.session_state.get("run_id")
        payload = {"approved": approved, "resume": resume, "comment": comment}
        try:
            response = api_post(f"/api/tasks/{run_id}/confirm", payload)
            st.success("Confirmation submitted.")
            render_json(response)
            st.session_state.last_run_response = response.get("run_result") or response
            refresh_current_run(show_errors=False)
            st.rerun()
        except ApiError as exc:
            show_api_error(exc)


def render_trace_viewer() -> None:
    st.subheader("Trace")
    run_id = st.session_state.get("run_id")
    if not run_id:
        st.info("No active run.")
        return
    if st.button("Refresh Trace"):
        try:
            st.session_state.last_trace = normalize_trace_response(api_get(f"/api/tasks/{run_id}/trace"))
        except ApiError as exc:
            show_api_error(exc)
            return

    traces = st.session_state.get("last_trace") or []
    if not traces:
        st.info("No trace yet.")
        return

    counts: dict[str, int] = {}
    for trace in traces:
        status = trace.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    cols = st.columns(max(1, len(counts)))
    for col, (status, count) in zip(cols, counts.items()):
        col.markdown(status_badge(status), unsafe_allow_html=True)
        col.metric("count", count)

    rows = [
        {
            "step_no": trace.get("step_no"),
            "tool_name": trace.get("tool_name"),
            "status": trace.get("status"),
            "input_summary": trace.get("input_summary"),
            "output_summary": trace.get("output_summary"),
            "error_message": trace.get("error_message"),
            "latency_ms": trace.get("latency_ms"),
            "created_at": trace.get("created_at"),
            "finished_at": trace.get("finished_at"),
        }
        for trace in traces
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    for trace in traces:
        with st.expander(
            f"Trace details: {trace.get('step_no')} - {trace.get('tool_name')}"
        ):
            metadata = extract_trace_metadata(trace)
            if metadata:
                st.caption("RAG backend metadata")
                st.dataframe([metadata], use_container_width=True, hide_index=True)
            render_json(trace)


def render_report_viewer() -> None:
    st.subheader("Report")
    run_id = st.session_state.get("run_id")
    if not run_id:
        st.info("No active run.")
        return
    if st.button("Refresh Report"):
        try:
            st.session_state.last_report = api_get(f"/api/reports/{run_id}")
        except ApiError as exc:
            show_api_error(exc)
            return

    report = st.session_state.get("last_report")
    if not report:
        st.info("No report loaded yet.")
        return

    st.write(
        {
            "exists": report.get("exists"),
            "report_path": report.get("report_path"),
            "message": report.get("message"),
        }
    )
    markdown = report.get("markdown") or ""
    if report.get("exists"):
        st.markdown(markdown)
        st.download_button(
            "Download Markdown",
            data=markdown,
            file_name=f"traceable_report_{run_id}.md",
            mime="text/markdown",
        )
    else:
        st.info(report.get("message") or "Report has not been generated yet.")


def main() -> None:
    st.set_page_config(page_title="Traceable Research Agent", layout="wide")
    init_state()
    render_sidebar()

    st.title("Traceable Research Agent Demo")
    st.caption("Create task -> inspect plan -> run -> trace -> report")

    render_health_panel()
    left, right = st.columns([0.95, 1.05])
    with left:
        render_task_creation()
        render_plan_viewer()
    with right:
        render_status_panel()
        render_run_executor()
        render_hitl_panel()

    render_trace_viewer()
    render_report_viewer()


if __name__ == "__main__":
    main()
