from app.agent.plan_guardrails import normalize_plan_arguments, validate_plan_for_execution
from app.agent.research_goal import build_task_contract
from app.llm.planner_client import build_planner_messages
from app.tools.base import RiskLevel, ToolSpec
from app.tools import registry


def test_real_planner_prompt_has_no_demo_defaults():
    messages = build_planner_messages("What is Jev?", ["file_reader", "sql_query", "tavily_search"], "real")
    prompt = messages[0].content + messages[1].content
    assert "demo_research_note.md" not in prompt
    assert "SELECT id, title, category FROM documents" not in prompt


def test_real_normalization_never_substitutes_demo_file_or_sql():
    plan = {"steps": [
        {"step_no": 1, "tool_name": "file_reader", "arguments": {}},
        {"step_no": 2, "tool_name": "sql_query", "arguments": {}},
    ]}
    normalized = normalize_plan_arguments(plan, "Explain Jev", "real")
    issues = validate_plan_for_execution(normalized, allowed_tools=["file_reader", "sql_query"])
    assert {issue.field for issue in issues} == {"path", "query"}
    assert "path" not in normalized["steps"][0]["arguments"]
    assert "query" not in normalized["steps"][1]["arguments"]


def test_academic_empty_queries_are_rejected_instead_of_using_task_as_a_default():
    plan = {"steps": [
        {"step_no": index, "tool_name": tool_name, "arguments": {}}
        for index, tool_name in enumerate(
            ("arxiv_search", "crossref_search", "openalex_search", "semantic_scholar_search"), 1
        )
    ]}
    normalized = normalize_plan_arguments(plan, "recent papers about async Python", "real")
    issues = validate_plan_for_execution(normalized, allowed_tools=[step["tool_name"] for step in plan["steps"]])
    assert {(issue.tool_name, issue.field) for issue in issues} == {
        ("arxiv_search", "query"),
        ("crossref_search", "query"),
        ("openalex_search", "query"),
        ("semantic_scholar_search", "query"),
    }


def test_contract_extracts_only_explicit_constraints_and_defaults_to_chinese():
    contract = build_task_contract("Search official Python docs and answer in exactly two English sentences.")
    assert contract["answer_mode"] == "answer"
    assert contract["evidence_requirement"] == "substantive"
    assert contract["source_constraints"]["official_only"] is True
    assert contract["output_constraints"] == {"language": "en", "sentence_count": 2}
    assert build_task_contract("解释 Jev 模型")["output_constraints"]["language"] == "zh"


def test_validator_does_not_replace_a_configured_disabled_builtin(monkeypatch):
    disabled_tavily = ToolSpec(
        name="tavily_search",
        description="disabled for this deployment",
        input_schema={"query": "string"},
        output_schema={},
        risk_level=RiskLevel.LOW,
        enabled=False,
    )
    monkeypatch.setitem(registry._tool_specs, "tavily_search", disabled_tavily)

    issues = validate_plan_for_execution(
        {"allowed_tools": ["tavily_search"], "steps": [
            {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "Jev"}},
        ]}
    )

    assert not issues
    assert registry.get_tool("tavily_search") is disabled_tavily


def test_validator_rejects_unknown_remote_and_explicit_empty_authorization():
    remote_issues = validate_plan_for_execution(
        {"allowed_tools": ["remote_unregistered_search"], "steps": [
            {"step_no": 1, "tool_name": "remote_unregistered_search", "arguments": {}},
        ]}
    )
    empty_scope_issues = validate_plan_for_execution(
        {"allowed_tools": [], "steps": [
            {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "Jev"}},
        ]}
    )

    assert {(issue.code, issue.tool_name) for issue in remote_issues} == {
        ("unknown_tool", "remote_unregistered_search"),
    }
    assert ("disallowed_tool", "tavily_search") in {
        (issue.code, issue.tool_name) for issue in empty_scope_issues
    }


def test_validator_rejects_invalid_enum_arguments():
    issues = validate_plan_for_execution(
        {"allowed_tools": ["tavily_search"], "steps": [
            {
                "step_no": 1,
                "tool_name": "tavily_search",
                "arguments": {"query": "Jev", "search_depth": "unbounded"},
            },
        ]}
    )

    assert {(issue.field, issue.code) for issue in issues} == {
        ("search_depth", "invalid_type"),
    }
