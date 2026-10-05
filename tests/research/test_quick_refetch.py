"""Quick recovery only advances to deferred candidates from this run."""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.executor import _run_quick_refetches
from app.agent.quick_refetch import select_pending_candidates
from app.tools.base import ToolResult
from app.trace import store
from app.trace.logger import record_tool_result

from .conftest import create_root


def _trace(db, run_id, tool, inputs, output, *, success=True):
    return record_tool_result(
        db,
        run_id,
        1,
        tool,
        inputs,
        ToolResult(success=success, output=output, error_message=None if success else "failed"),
        1,
    )


def test_candidate_selection_skips_attempted_urls_and_failed_discovery(db):
    root = create_root(db)
    traces = [
        _trace(db, root.run_id, "tavily_search", {}, {
            "discovery_candidates": [
                {"url": "https://example.test/a"},
                {"url": "https://example.test/b"},
                {"url": "https://example.test/c"},
                {"url": "https://example.test/d"},
            ],
            "fetch_candidates": ["https://example.test/a"],
        }),
        _trace(db, root.run_id, "tavily_search", {}, {
            "discovery_candidates": [{"url": "https://untrusted.test/only-failed-search"}],
        }, success=False),
        _trace(db, root.run_id, "web_fetcher", {"urls": ["https://example.test/a"]}, {
            "pages": [{"url": "https://example.test/a", "final_url": "https://redirect.test/final"}],
        }),
    ]

    first = select_pending_candidates(
        traces, max_total_candidates=4, remaining_rounds=2
    )
    assert first == ["https://example.test/b", "https://example.test/c"]

    _trace(db, root.run_id, "web_fetcher", {"urls": first}, {"pages": []})
    traces = store.list_tool_traces(db, root.run_id)
    second = select_pending_candidates(
        traces, max_total_candidates=4, remaining_rounds=1
    )
    assert second == ["https://example.test/d"]


def test_candidate_selection_counts_requested_urls_not_redirect_targets(db):
    root = create_root(db)
    traces = [
        _trace(db, root.run_id, "tavily_search", {}, {
            "discovery_candidates": [
                {"url": "https://example.test/a"},
                {"url": "https://example.test/b"},
            ],
        }),
        _trace(db, root.run_id, "web_fetcher", {"urls": ["https://example.test/a"]}, {
            "pages": [{"url": "https://example.test/a", "final_url": "https://redirect.test/final"}],
        }),
    ]

    assert select_pending_candidates(traces, max_total_candidates=2) == [
        "https://example.test/b"
    ]


@pytest.mark.parametrize("report_feedback", [False, True])
def test_quick_refetch_uses_governed_executor_and_persists_trace(db, r12_settings, report_feedback):
    root = create_root(db, "Explain a documented behavior")
    root = store.mark_agent_run_running_unless_cancelled(db, root.run_id)
    search = _trace(db, root.run_id, "tavily_search", {}, {
        "discovery_candidates": [
            {"url": "https://allowed.test/first"},
            {"url": "https://allowed.test/second"},
            {"url": "https://outside.test/third"},
        ],
        "fetch_candidates": ["https://allowed.test/first"],
    })
    first_fetch = _trace(db, root.run_id, "web_fetcher", {
        "urls": ["https://allowed.test/first"],
    }, {"pages": [{"url": "https://allowed.test/first"}]})
    settings = r12_settings.model_copy(update={"max_fetch_candidates": 3, "max_refetch_rounds": 2})
    plan = {
        "research_mode": "quick",
        "quick_output_mode": "limited_research",
        "source_constraints": {"mode": "restrict", "domains": ["allowed.test"]},
        "task_contract": {"evidence_requirement": "substantive"},
        "steps": [{"step_no": 1}, {"step_no": 2}],
    }
    failed_assessment = SimpleNamespace(passed=False, as_dict=lambda: {"passed": False})
    passed_assessment = SimpleNamespace(passed=True, as_dict=lambda: {"passed": True})
    returned = ToolResult(
        success=True,
        output={"pages": [{"url": "https://allowed.test/second", "content": "body"}]},
        output_summary="fetched alternate candidate",
        metadata={"executed": True},
    )

    with (
        patch("app.agent.executor.execute_governed_operation", return_value=returned) as execute,
        patch("app.agent.executor.materialize_execution_provenance", return_value={"passages": ["new"]}) as materialize,
        patch("app.agent.executor.assess_required_evidence", return_value=passed_assessment),
    ):
        traces, bundle, assessment = _run_quick_refetches(
            db,
            root,
            plan,
            settings,
            [],
            [search, first_fetch],
            {"passages": []},
            passed_assessment if report_feedback else failed_assessment,
            report_feedback=report_feedback,
        )

    execute.assert_called_once()
    args, kwargs = execute.call_args
    assert args[0] == "web_fetcher"
    assert args[1]["urls"] == ["https://allowed.test/second"]
    assert kwargs["arguments_prepared"] is True
    materialize.assert_called_once()
    assert assessment.passed
    assert bundle == {"passages": ["new"]}
    assert len(traces) == 3
    refreshed = store.list_tool_traces(db, root.run_id)
    assert refreshed[-1].tool_name == "web_fetcher"
    assert json.loads(refreshed[-1].input_json)["urls"] == ["https://allowed.test/second"]
    assert plan["quick_refetch"]["attempts"][0]["urls"] == ["https://allowed.test/second"]
    assert plan["quick_refetch"]["attempts"][0]["trigger"] == (
        "report_evidence_gap" if report_feedback else "body_evidence_gap")


def test_refetch_rounds_are_not_replayed_on_resume(db, r12_settings):
    root = create_root(db)
    _trace(db, root.run_id, "tavily_search", {}, {
        "discovery_candidates": [
            {"url": f"https://example.test/{index}"} for index in range(6)
        ],
    })
    traces = store.list_tool_traces(db, root.run_id)
    selected = select_pending_candidates(
        traces, max_total_candidates=2, remaining_rounds=1
    )
    _trace(db, root.run_id, "web_fetcher", {"urls": selected}, {"pages": []})
    traces = store.list_tool_traces(db, root.run_id)

    assert select_pending_candidates(
        traces, max_total_candidates=2, remaining_rounds=1
    ) == []


def test_quick_refetch_does_not_call_tools_outside_source_scope(db, r12_settings):
    root = create_root(db)
    search = _trace(db, root.run_id, "tavily_search", {}, {
        "discovery_candidates": [{"url": "https://outside.test/source"}],
    })
    plan = {
        "research_mode": "quick",
        "quick_output_mode": "limited_research",
        "source_constraints": {"mode": "restrict", "domains": ["allowed.test"]},
        "task_contract": {"evidence_requirement": "substantive"},
        "steps": [],
    }
    failed = SimpleNamespace(passed=False, as_dict=lambda: {"passed": False})

    with patch("app.agent.executor.execute_governed_operation") as execute:
        traces, bundle, assessment = _run_quick_refetches(
            db, root, plan, r12_settings, [], [search], {}, failed
        )

    execute.assert_not_called()
    assert traces == [search]
    assert bundle == {}
    assert assessment is failed
    assert plan["quick_refetch"]["status"] == "no_eligible_candidates"


def test_quick_refetch_honors_rounds_already_persisted_on_resume(db, r12_settings):
    root = create_root(db)
    search = _trace(db, root.run_id, "tavily_search", {}, {
        "discovery_candidates": [{"url": "https://example.test/pending"}],
    })
    plan = {
        "research_mode": "quick",
        "quick_output_mode": "limited_research",
        "task_contract": {"evidence_requirement": "substantive"},
        "steps": [],
        "quick_refetch": {"version": "quick-refetch-v1", "attempts": [
            {"round": 1}, {"round": 2},
        ]},
    }
    failed = SimpleNamespace(passed=False, as_dict=lambda: {"passed": False})

    with (
        patch("app.agent.executor.execute_governed_operation") as execute,
        patch("app.agent.executor.materialize_execution_provenance") as materialize,
    ):
        traces, bundle, assessment = _run_quick_refetches(
            db, root, plan, r12_settings, [], [search], {}, failed
        )

    execute.assert_not_called()
    materialize.assert_not_called()
    assert traces == [search]
    assert bundle == {}
    assert assessment is failed
    assert plan["quick_refetch"]["status"] == "round_limit_reached"
