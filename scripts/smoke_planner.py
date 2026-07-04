"""Smoke check for deterministic planner rules."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.planner import plan_task
from app.agent.plan_guardrails import normalize_plan_arguments
from app.tools.base import RiskLevel, ToolSpec
from app.tools.registry import register_tool


def register_fake_remote_tool(server: str, remote_name: str, input_schema: dict) -> None:
    register_tool(
        ToolSpec(
            name=f"{server}.{remote_name}",
            description=f"Fake readonly MCP tool {server}.{remote_name}",
            input_schema=input_schema,
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            enabled=True,
            tags=["mcp_remote", "mcp-channel-readonly", "read-only"],
            metadata={
                "tool_source": "mcp_remote",
                "mcp_channel": "readonly",
                "remote_server": server,
                "remote_tool_name": remote_name,
                "remote_registry_name": f"{server}.{remote_name}",
                "read_only": True,
                "side_effect_free": True,
                "headers_env": ["FAKE_HEADER_ENV"],
            },
        ),
        lambda args: None,
    )


def main() -> None:
    for server, remote_name, schema in [
        ("firecrawl", "search", {"properties": {"query": {"type": "string"}}}),
        ("firecrawl", "scrape", {"properties": {"url": {"type": "string"}, "query": {"type": "string"}}}),
        ("firecrawl", "extract", {"properties": {"query": {"type": "string"}}}),
        ("exa", "web_search_exa", {"properties": {"query": {"type": "string"}, "numResults": {"type": "integer"}}}),
        ("context7", "resolve-library-id", {"properties": {"libraryName": {"type": "string"}}}),
        ("context7", "query-docs", {"properties": {"query": {"type": "string"}, "libraryId": {"type": "string"}}}),
    ]:
        register_fake_remote_tool(server, remote_name, schema)

    task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
    plan = plan_task(
        task=task,
        allowed_tools=["file_reader", "sql_query", "rag_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    tool_names = [step["tool_name"] for step in plan["steps"]]
    expected = ["file_reader", "sql_query", "rag_search", "report_writer"]
    if tool_names != expected:
        raise SystemExit(f"Unexpected tool sequence: {tool_names}")

    step_numbers = [step["step_no"] for step in plan["steps"]]
    if step_numbers != list(range(1, len(step_numbers) + 1)):
        raise SystemExit(f"Step numbers are not consecutive: {step_numbers}")

    limited = plan_task(
        task="Read local docs and query database then generate report",
        allowed_tools=["file_reader"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    limited_tools = [step["tool_name"] for step in limited["steps"]]
    if limited_tools != ["file_reader"]:
        raise SystemExit(f"allowed_tools restriction failed: {limited_tools}")
    if not limited["notes"]:
        raise SystemExit("Expected allowed_tools restriction notes.")

    empty = plan_task(
        task="Read local docs and query database then generate report",
        allowed_tools=[],
        source_mode="mock",
        planner_mode="deterministic",
    )
    if empty["steps"] or "No executable planning step" not in " ".join(empty["notes"]):
        raise SystemExit(f"Expected empty plan for allowed_tools=[], got {empty}")

    outside_path = ROOT / "workspace" / "tmp" / "planner_outside_allowed_root.md"
    hitl = {
        "version": "planner-smoke",
        "task": "Read an explicit outside file",
        "source_mode": "mock",
        "allowed_tools": ["file_reader"],
        "steps": [
            {
                "step_no": 1,
                "tool_name": "file_reader",
                "arguments": {"path": str(outside_path), "max_chars": 100},
                "goal": "Read outside file.",
                "expected_output": "Content.",
                "completion_criteria": "Requires approval.",
                "risk_level": "low",
                "requires_confirmation": False,
            }
        ],
        "notes": [],
        "confirmation": None,
    }
    hitl = normalize_plan_arguments(hitl, hitl["task"], "mock")
    file_step = hitl["steps"][0]
    if (
        file_step["tool_name"] != "file_reader"
        or file_step["requires_confirmation"] is not True
        or file_step.get("confirmation_reason") != "file_reader_path_outside_allowed_roots"
    ):
        raise SystemExit(f"Expected file_reader path HITL confirmation, got {hitl}")

    external = plan_task(
        task="Search GitHub repository issues and current web sources about traceable research agent and generate a markdown report",
        allowed_tools=["mcp_github_search", "tavily_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    external_tools = [step["tool_name"] for step in external["steps"]]
    if external_tools != ["tavily_search", "mcp_github_search", "report_writer"]:
        raise SystemExit(f"Expected external research planning path, got {external_tools}")

    chinese_web = plan_task(
        task="帮我全网搜集关于 LLM 的学习资料、课程和教程，并生成报告",
        allowed_tools=["tavily_search", "report_writer"],
        source_mode="mock",
        planner_mode="deterministic",
    )
    chinese_web_tools = [step["tool_name"] for step in chinese_web["steps"]]
    if chinese_web_tools != ["tavily_search", "report_writer"]:
        raise SystemExit(f"Expected Chinese web keywords to trigger Tavily, got {chinese_web_tools}")

    full_planner = plan_task(
        task="Create an LLM learning roadmap with online course links.",
        allowed_tools=[
            "file_reader",
            "sql_query",
            "rag_search",
            "mcp_github_search",
            "tavily_search",
            "report_writer",
        ],
        source_mode="mock",
        planner_mode="deterministic",
        scenario_template="full_planner",
    )
    full_planner_tools = [step["tool_name"] for step in full_planner["steps"]]
    expected_full = [
        "file_reader",
        "sql_query",
        "rag_search",
        "mcp_github_search",
        "tavily_search",
        "report_writer",
    ]
    if full_planner_tools != expected_full:
        raise SystemExit(f"Expected full planner to backfill all tools, got {full_planner_tools}")

    deep_research = plan_task(
        task="深入调研 Stable Diffusion 和 ComfyUI 的学习资料，读取网页正文并生成可验证报告",
        allowed_tools=None,
        source_mode="mock",
        planner_mode="deterministic",
        scenario_template="deep_web_research",
    )
    deep_tools = [step["tool_name"] for step in deep_research["steps"]]
    for expected_tool in ["tavily_search", "exa.web_search_exa", "firecrawl.search", "report_writer"]:
        if expected_tool not in deep_tools:
            raise SystemExit(f"Expected deep research tool {expected_tool}, got {deep_tools}")
    if "firecrawl.scrape" in deep_tools:
        raise SystemExit(f"Firecrawl scrape should not be planned without a concrete URL, got {deep_tools}")

    deep_research_with_url = plan_task(
        task="深入调研 https://example.com/firecrawl/source-pack 的网页正文并生成可验证报告",
        allowed_tools=None,
        source_mode="mock",
        planner_mode="deterministic",
        scenario_template="deep_web_research",
    )
    deep_url_tools = [step["tool_name"] for step in deep_research_with_url["steps"]]
    if "firecrawl.scrape" not in deep_url_tools:
        raise SystemExit(f"Expected Firecrawl scrape when URL is present, got {deep_url_tools}")
    scrape_step = next(step for step in deep_research_with_url["steps"] if step["tool_name"] == "firecrawl.scrape")
    if not scrape_step["arguments"].get("url"):
        raise SystemExit(f"Expected Firecrawl scrape URL argument, got {scrape_step}")

    tech_docs = plan_task(
        task="学习 FastAPI 和 Streamlit 的最新技术文档，并生成报告",
        allowed_tools=None,
        source_mode="mock",
        planner_mode="deterministic",
        scenario_template="technical_docs_research",
    )
    tech_tools = [step["tool_name"] for step in tech_docs["steps"]]
    for expected_tool in ["mcp_github_search", "context7.resolve-library-id", "context7.query-docs", "report_writer"]:
        if expected_tool not in tech_tools:
            raise SystemExit(f"Expected technical docs tool {expected_tool}, got {tech_tools}")
    resolve_step = next(step for step in tech_docs["steps"] if step["tool_name"] == "context7.resolve-library-id")
    if not resolve_step["arguments"].get("libraryName"):
        raise SystemExit(f"Expected Context7 libraryName argument, got {resolve_step}")

    print(
        {
            "planner": "ok",
            "tools": tool_names,
            "limited_tools": limited_tools,
            "limited_notes": limited["notes"],
            "empty_steps": len(empty["steps"]),
            "hitl_file_requires_confirmation": file_step["requires_confirmation"],
            "external_tools": external_tools,
            "chinese_web_tools": chinese_web_tools,
            "full_planner_tools": full_planner_tools,
            "deep_research_tools": deep_tools,
            "deep_research_url_tools": deep_url_tools,
            "technical_docs_tools": tech_tools,
        }
    )


if __name__ == "__main__":
    main()
