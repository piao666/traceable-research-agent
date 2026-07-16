"""Static smoke checks for the Streamlit frontend demo layer."""

from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_APP = ROOT / "frontend" / "streamlit_app.py"
FRONTEND_README = ROOT / "frontend" / "README.md"
REQUIREMENTS = ROOT / "requirements.txt"
START_SCRIPT = ROOT / "start_traceable_demo.bat"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    assert_true(FRONTEND_APP.exists(), "frontend/streamlit_app.py missing")
    assert_true(FRONTEND_README.exists(), "frontend/README.md missing")
    assert_true(REQUIREMENTS.exists(), "requirements.txt missing")
    assert_true(START_SCRIPT.exists(), "start_traceable_demo.bat missing")

    requirements = REQUIREMENTS.read_text(encoding="utf-8").lower()
    assert_true("streamlit" in requirements, "streamlit missing from requirements.txt")
    assert_true("requests" in requirements, "requests missing from requirements.txt")

    py_compile.compile(str(FRONTEND_APP), doraise=True)
    source = FRONTEND_APP.read_text(encoding="utf-8")
    start_script = START_SCRIPT.read_text(encoding="utf-8")

    required_paths = [
        "/health",
        "/api/tasks",
        "/plan",
        "/run",
        "/run_async",
        "/trace",
        "/api/reports",
        "/download?format=",
        "/confirm",
        "/mcp/refresh",
    ]
    missing_paths = [path for path in required_paths if path not in source]
    assert_true(not missing_paths, f"missing API paths: {missing_paths}")

    metadata_fields = [
        "embedding_backend",
        "vector_backend",
        "fallback_used",
        "retrieval_mode",
        "dense_hit_count",
        "bm25_hit_count",
        "rrf_k",
        "dimension",
        "collection_name",
    ]
    missing_metadata = [field for field in metadata_fields if field not in source]
    assert_true(not missing_metadata, f"missing RAG metadata display fields: {missing_metadata}")
    assert_true(
        "执行元信息" in source or "Trace details:" in source,
        "trace details display missing",
    )
    assert_true("执行模式" in source or "Execution Mode" in source, "execution mode control missing")
    removed_sidebar_text = [
        "填充示例任务文本",
        "**🌐 数据来源**",
        "数据来源\"",
        "🔧 高级配置（API 连接）",
        "异步执行（推荐开启，避免超时）",
        "Realtime auto refresh",
        "可追踪调研智能体后端",
    ]
    leaked_sidebar_text = [text for text in removed_sidebar_text if text in source]
    assert_true(not leaked_sidebar_text, f"removed sidebar/title text still present: {leaked_sidebar_text}")
    assert_true("api_base_url" in source and "tenant_id" in source and "user_id" in source, "default API context state missing")
    for react_field in ["思考摘要", "选择动作", "观察结果"]:
        assert_true(react_field in source, f"missing ReAct trace display: {react_field}")
    assert_true(
        "ReAct 思考链" in source or "ReAct Trace" in source,
        "missing ReAct trace section",
    )
    assert_true('"source_mode_ui": "real"' in source or '"source_mode": "real"' in source, "real source mode is not the default")
    assert_true(
        '"source_mode": st.session_state.source_mode' in source
        or '"source_mode": st.session_state.get("source_mode_ui", "real")' in source,
        "task payload does not use selected source mode",
    )
    assert_true('"scenario_template_key"' in source, "task payload does not include scenario template key")
    assert_true("full_planner" not in source or "ALL_TOOLS" in source, "full planner should not be a default visible template")
    assert_true("stream_task_events" in source, "Streamlit does not consume SSE task events")
    assert_true("render_event_console" in source, "Streamlit realtime event console missing")
    assert_true("STREAM_EVENT_CN" in source and "STREAM_STATUS_CN" in source, "realtime event Chinese mapping missing")
    assert_true("run_requested | status=" not in source, "raw English run_requested log should not be shown")
    assert_true("create_task_started | planner=requested" not in source, "raw English create-task log should not be shown")
    assert_true("CREATE_TASK_TIMEOUT_SECONDS" in source, "create-task timeout constant missing")
    assert_true(
        'api_post("/api/tasks", payload, timeout=CREATE_TASK_TIMEOUT_SECONDS)' in source,
        "create-task request does not use the longer planner timeout",
    )
    assert_true('"tavily_search"' in source, "tavily_search missing from frontend tool list")
    assert_true('"docx"' in source and '"pdf"' in source, "report docx/pdf download formats missing")
    assert_true("本地资料分析" in source, "local analysis template missing")
    assert_true("联网深度调研" in source, "deep web research template missing")
    assert_true("技术文档调研" in source, "technical docs template missing")
    assert_true("内部资料复盘" in source or "复盘一个 AI 调研功能" in source, "local template should use business review task")
    assert_true("OpenAI-compatible API 网关" in source, "deep research template should use competitive intelligence task")
    assert_true("企业 Agent 工具链落地" in source, "technical docs template should use vendor evaluation task")
    assert_true("_sync_template_state" in source and "task_text" in source, "template switch should sync task text")
    assert_true("外部调研（GitHub + Tavily）" not in source, "external research template should be merged into deep research")
    assert_true("全规划器（本地读取 + 外部调研）" not in source, "full planner template should be hidden from default UI")
    assert_true("deep_web_research" in source, "deep web research scenario key missing")
    assert_true("technical_docs_research" in source, "technical docs scenario key missing")
    assert_true('"allowed_tools": None' in source, "dynamic remote MCP templates should allow backend defaults")
    assert_true("_current_scenario_template_key()" in source, "scenario key helper not used in payload")
    assert_true("HITL 人工确认流程" not in source, "standalone HITL template should be removed")
    assert_true("MCP 已注册" in source and "远端 MCP 已配置但尚未注册" in source, "MCP registration status should be visible")
    assert_true("重新注册远端 MCP" in source, "MCP refresh action should be visible")
    assert_true("MCP_CHANNEL_READONLY_SERVERS" in start_script, "demo starter should configure source-pack readonly MCP")
    assert_true("source_pack=http://127.0.0.1:9001/mcp" in start_script, "demo starter should use parseable source-pack shorthand")
    assert_true("Waiting for MCP Source Pack Bridge readiness" in start_script, "demo starter should wait for bridge readiness")
    assert_true(start_script.find("Waiting for MCP Source Pack Bridge readiness") < start_script.find("[1/3] FastAPI backend"), "FastAPI should start after bridge readiness wait")
    assert_true("-Providers 'firecrawl,exa'" in start_script, "demo starter should quote comma-separated providers")
    assert_true("Exa 负责发现候选来源" in source, "MCP role explanation for Exa/Firecrawl missing")
    assert_true("Context7 adapter 已预留" in source, "Context7 reserved-state explanation missing")
    assert_true("降级状态" in source and "部分降级" in source, "degradation summary should be user-facing")
    assert_true("任务状态" in source and "当前步骤" in source and "Trace 条数" in source, "trace metrics should be localized")
    assert_true("证据聚合" in source, "evidence aggregation title should be localized")
    assert_true("证据导出" in source, "evidence export title should be localized")
    assert_true("FOLDED_REPORT_SECTION_PREFIXES" in source, "report folded section helper missing")
    assert_true("HIDDEN_REPORT_SECTION_PREFIXES" in source, "hidden report section helper missing")
    assert_true("in_code_block" in source, "report section splitter must ignore headings inside code blocks")
    assert_true(
        "heading.startswith(HIDDEN_REPORT_SECTION_PREFIXES)" in source,
        "Streamlit report preview should hide the tool observation section",
    )
    assert_true("render_report_markdown(md)" in source, "report markdown should use folded renderer")
    assert_true("<details><summary>关键证据片段</summary>" in source, "key evidence fragment folding missing")
    assert_true("计划步骤数" not in source and "实际执行步骤" not in source, "report summary still shows removed metrics")
    assert_true(
        source.count('"allowed_tools":') >= 3,
        "expected at least three scenario template allowed_tools entries",
    )

    forbidden_patterns = [
        r"QWEN_API_KEY\s*=",
        r"DEEPSEEK_API_KEY\s*=",
        r"sk-[A-Za-z0-9_\-]{16,}",
        r"Bearer\s+[A-Za-z0-9_\-]{20,}",
    ]
    hits = []
    for pattern in forbidden_patterns:
        if re.search(pattern, source):
            hits.append(pattern)
    assert_true(not hits, f"potential hardcoded secret patterns found: {hits}")

    print(
        json.dumps(
            {
                "streamlit_frontend": "ok",
                "files": "ok",
                "requirements": "ok",
                "api_paths": "ok",
                "rag_metadata_display": "ok",
                "secret_scan": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
