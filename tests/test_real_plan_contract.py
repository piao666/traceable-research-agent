from __future__ import annotations

from app.agent.plan_guardrails import validate_plan_for_execution
from app.agent.planner import deterministic_plan_task
from app.agent.research_goal import build_task_contract
from app.agent.source_intake import execute_governed_operation
from app.config import settings
from app.llm.planner_client import build_planner_messages
from app.tools.base import ToolResult
from app.tools.defaults import register_default_tools


def test_real_planner_prompt_does_not_offer_demo_file_or_sql_defaults():
    messages = build_planner_messages(
        "Explain Python asyncio from official documentation.",
        ["file_reader", "sql_query", "tavily_search", "report_writer"],
        "real",
    )
    system = messages[0].content
    assert "demo_research_note.md" not in system
    assert "SELECT id, title, category FROM documents" not in system


def test_substantive_quick_plan_binds_fetch_to_search_within_permission_scope():
    register_default_tools()
    plan = deterministic_plan_task(
        "Search Python asyncio documentation and explain its event loop.",
        allowed_tools=["tavily_search", "web_fetcher", "report_writer"],
        source_mode="real",
    )
    # The deep template provides the same mandatory retrieval edge that Quick
    # substantive routing subsequently preserves.
    from app.agent.planner import _enforce_research_mode
    plan["research_mode"] = "quick"
    plan = _enforce_research_mode(plan, "quick", None)
    search = next(step for step in plan["steps"] if step["tool_name"] == "tavily_search")
    fetch = next(step for step in plan["steps"] if step["tool_name"] == "web_fetcher")
    assert fetch["arguments_from"] == {"step_no": search["step_no"], "field": "results"}
    assert fetch["arguments"]["urls"] == []
    assert not validate_plan_for_execution(plan, plan["task_contract"], plan["allowed_tools"])


def test_quick_source_request_with_body_requirement_is_not_misclassified_as_discovery():
    """The real Quick incident must retain the governed search-to-fetch edge."""
    register_default_tools()
    task = "Jev 模型是什么？请先搜索网页并抓取可核验的来源正文，再给出简明中文介绍和引用。"
    plan = deterministic_plan_task(
        task,
        allowed_tools=["tavily_search", "web_fetcher", "report_writer"],
        source_mode="real",
    )
    plan["task_contract"] = build_task_contract(task)
    from app.agent.planner import _enforce_research_mode

    plan = _enforce_research_mode(plan, "quick", None)

    assert plan["task_contract"]["evidence_requirement"] == "substantive"
    assert plan["quick_output_mode"] == "limited_research"
    search = next(step for step in plan["steps"] if step["tool_name"] == "tavily_search")
    fetch = next(step for step in plan["steps"] if step["tool_name"] == "web_fetcher")
    assert fetch["arguments_from"] == {"step_no": search["step_no"], "field": "results"}
    assert fetch["arguments"]["urls"] == []


def test_quick_pure_source_list_stays_discovery_only():
    register_default_tools()
    plan = deterministic_plan_task(
        "Find source links about Jev models.",
        allowed_tools=["tavily_search", "web_fetcher", "report_writer"],
        source_mode="real",
    )
    plan["task_contract"] = build_task_contract(plan["task"])
    from app.agent.planner import _enforce_research_mode

    plan = _enforce_research_mode(plan, "quick", None)

    assert plan["quick_output_mode"] == "discovery"
    assert {step["tool_name"] for step in plan["steps"]} == {"tavily_search", "report_writer"}


def test_contract_records_requested_output_and_source_constraints():
    contract = build_task_contract(
        "Search only official Python asyncio documentation and answer in exactly two English sentences."
    )
    assert contract["answer_mode"] == "answer"
    assert contract["evidence_requirement"] == "substantive"
    assert contract["source_constraints"]["official_only"] is True
    assert contract["output_constraints"] == {"language": "en", "sentence_count": 2}


def test_real_file_step_without_explicit_path_is_rejected_not_filled_from_demo():
    register_default_tools()
    plan = {
        "allowed_tools": ["file_reader"],
        "steps": [{"step_no": 1, "tool_name": "file_reader", "arguments": {}}],
    }
    issues = validate_plan_for_execution(plan, {}, plan["allowed_tools"])
    assert any(issue.code == "missing_required_argument" and issue.field == "path" for issue in issues)


def test_fetch_reordering_keeps_search_dependency_and_writer_last():
    from app.agent.planner import _ensure_search_fetch_dependency

    steps = [
        {"step_no": 9, "tool_name": "report_writer", "arguments": {}},
        {"step_no": 4, "tool_name": "web_fetcher", "arguments": {"urls": []}, "arguments_from": {"step_no": 7, "field": "results"}},
        {"step_no": 7, "tool_name": "tavily_search", "arguments": {"query": "Python asyncio"}},
    ]
    _ensure_search_fetch_dependency(steps, [], "Python asyncio", {"tavily_search", "web_fetcher", "report_writer"})
    assert [step["tool_name"] for step in steps] == ["tavily_search", "web_fetcher", "report_writer"]
    assert steps[1]["arguments_from"] == {"step_no": 1, "field": "results"}
    assert [step["step_no"] for step in steps] == [1, 2, 3]


def test_model_placeholder_fetch_url_is_replaced_by_search_dependency():
    """Only task-literal URLs may bypass the governed discovery edge."""
    from app.agent.planner import _ensure_search_fetch_dependency

    steps = [
        {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "JEV model"}},
        {"step_no": 2, "tool_name": "web_fetcher", "arguments": {
            "urls": ["https://example.com/placeholder"], "max_chars": 8000,
        }},
        {"step_no": 3, "tool_name": "report_writer", "arguments": {}},
    ]
    _ensure_search_fetch_dependency(steps, [], "JEV 模型是什么？", {"tavily_search", "web_fetcher", "report_writer"})
    fetch = steps[1]
    assert fetch["arguments"]["urls"] == []
    assert fetch["arguments_from"] == {"step_no": 1, "field": "results"}


def test_generic_deep_model_plan_rebinds_a_nonliteral_fetch_url_before_approval():
    from app.agent.planner import _ensure_substantive_search_fetch_steps

    task = "Explain Python asyncio event loops; search and fetch official documentation."
    plan = {
        "source_mode": "real", "task_contract": build_task_contract(task),
        "steps": [
            {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "site:docs.python.org asyncio event loop"}},
            {"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": ["https://docs.python.org/3/library/asyncio-eventloop.html"]}},
            {"step_no": 3, "tool_name": "report_writer", "arguments": {}},
        ],
    }
    allowed = ["tavily_search", "web_fetcher", "report_writer"]
    _ensure_substantive_search_fetch_steps(plan, task, allowed, "real")

    fetch = next(step for step in plan["steps"] if step["tool_name"] == "web_fetcher")
    assert fetch["arguments"]["urls"] == []
    assert fetch["arguments_from"] == {"step_no": 1, "field": "results"}
    assert not validate_plan_for_execution(plan, plan["task_contract"], allowed)


def test_every_model_fetch_step_is_bound_before_approval():
    from app.agent.planner import _ensure_substantive_search_fetch_steps

    task = "Explain asyncio using current official Python documentation; search and fetch sources."
    plan = {"source_mode": "real", "task_contract": build_task_contract(task), "steps": [
        {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "asyncio"}},
        {"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": []}},
        {"step_no": 3, "tool_name": "web_fetcher", "arguments": {"urls": ["https://docs.python.org/3/library/asyncio.html"]}},
        {"step_no": 4, "tool_name": "report_writer", "arguments": {}},
    ]}
    allowed = ["tavily_search", "web_fetcher", "report_writer"]
    _ensure_substantive_search_fetch_steps(plan, task, allowed, "real")
    fetches = [step for step in plan["steps"] if step["tool_name"] == "web_fetcher"]
    assert len(fetches) == 2
    assert all(step["arguments"]["urls"] == [] for step in fetches)
    assert all(step["arguments_from"] == {"step_no": 1, "field": "results"} for step in fetches)
    assert not validate_plan_for_execution(plan, plan["task_contract"], allowed)


def test_task_literal_fetch_url_remains_direct_and_has_no_model_dependency():
    from app.agent.planner import _ensure_search_fetch_dependency

    explicit_url = "https://docs.example.test/jev"
    steps = [
        {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "JEV model"}},
        {"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": [explicit_url]},
         "arguments_from": {"step_no": 1, "field": "results"}},
    ]
    _ensure_search_fetch_dependency(steps, [], f"Read {explicit_url} and explain JEV.", {"tavily_search", "web_fetcher"})
    fetch = steps[1]
    assert fetch["arguments"]["urls"] == [explicit_url]
    assert "arguments_from" not in fetch


def test_task_literal_url_uses_canonical_comparison_not_plan_provenance():
    from app.agent.planner import _ensure_search_fetch_dependency

    steps = [
        {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "JEV model"}},
        {"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": ["https://DOCS.example.test/jev#intro"]}},
    ]
    _ensure_search_fetch_dependency(
        steps, [], "Read https://docs.example.test/jev and explain it.",
        {"tavily_search", "web_fetcher"},
    )
    assert steps[1]["arguments"]["urls"] == ["https://DOCS.example.test/jev#intro"]
    assert "arguments_from" not in steps[1]


def test_source_id_read_is_valid_web_fetcher_argument():
    register_default_tools()
    issues = validate_plan_for_execution({"allowed_tools": ["web_fetcher"], "steps": [
        {"step_no": 1, "tool_name": "web_fetcher", "arguments": {"source_id": "source-1", "offset": 0, "max_chars": 200}},
    ]})
    assert not issues


def test_mentioning_official_does_not_turn_it_into_official_only_constraint():
    contract = build_task_contract("What is the official Python asyncio documentation?")
    assert contract["evidence_requirement"] == "substantive"
    assert contract["source_constraints"]["official_only"] is False


def test_imperative_current_official_documentation_is_an_official_only_constraint():
    contract = build_task_contract(
        "Explain asyncio using current official Python documentation."
    )

    assert contract["source_constraints"] == {
        "mode": "restrict", "domains": [], "official_only": True,
        "current_official_documentation": True,
    }


def test_current_official_docs_is_not_inferred_from_mention_or_version_history():
    mention = build_task_contract("What is the current official Python documentation?")
    assert mention["source_constraints"]["official_only"] is False
    assert "current_official_documentation" not in mention["source_constraints"]

    versioned = build_task_contract("Explain asyncio using current official Python 3.11 documentation.")
    assert versioned["source_constraints"]["official_only"] is True
    assert "current_official_documentation" not in versioned["source_constraints"]

    history = build_task_contract("Explain asyncio using current official Python documentation and historical versions.")
    assert history["source_constraints"]["official_only"] is True
    assert "current_official_documentation" not in history["source_constraints"]


def test_governed_operation_allows_valid_runtime_arguments_through_registry():
    """Runtime validation must not treat every action as an invalid plan step."""
    register_default_tools()
    invoked: list[dict] = []

    def execute(name: str, arguments: dict) -> ToolResult:
        invoked.append({"name": name, "arguments": arguments})
        return ToolResult(success=True, output={"results": []}, metadata={"data_source": "live"})

    result = execute_governed_operation(
        "tavily_search",
        {"query": "Python asyncio", "max_results": 1},
        {"source_mode": "real", "allowed_tools": ["tavily_search"]},
        settings.model_copy(update={"offline_mode": False}),
        execute,
    )
    assert result.success
    assert invoked and invoked[0]["name"] == "tavily_search"
