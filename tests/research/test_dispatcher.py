from unittest.mock import patch
import json
import pytest

from app.agent.dispatcher import run_task_by_mode
from app.trace import store
from .conftest import create_root


@pytest.mark.parametrize("mode,execution,engine", [
    ("deep", "planned", "deep"),
    ("auto", "react", "deep"),
    ("auto", "planned", "quick"),
    ("quick", "react", "quick"),
])
def test_single_controller_selection(db, r12_settings, mode, execution, engine):
    root = create_root(db)
    plan = json.loads(root.plan_json)
    plan.update(research_mode=mode, execution_mode=execution)
    plan.pop("research_controller", None)
    store.replace_agent_run_plan(db, root.run_id, plan)
    result = {"run_id": root.run_id, "status": "running"}
    with (
        patch("app.agent.dispatcher.enforce_execution_readiness", return_value=True),
        patch("app.agent.dispatcher.run_plan", return_value=result) as quick,
        patch("app.research.orchestrator.run_deep_research_v2", return_value=result) as deep,
        patch("app.agent.react_executor.run_react_task", side_effect=AssertionError("legacy entry")),
        patch("app.agent.dispatcher._finalize_result", side_effect=lambda db, run_id, result: result),
    ):
        run_task_by_mode(db, root.run_id, r12_settings)
    assert quick.call_count == (engine == "quick")
    assert deep.call_count == (engine == "deep")


def test_quick_failure_never_triggers_another_engine(db, r12_settings):
    root = create_root(db)
    plan = json.loads(root.plan_json)
    plan.update(research_mode="quick")
    store.replace_agent_run_plan(db, root.run_id, plan)
    with (
        patch("app.agent.dispatcher.enforce_execution_readiness", return_value=True),
        patch("app.agent.dispatcher.run_plan", side_effect=ValueError("writer failed")) as quick,
        patch("app.research.orchestrator.run_deep_research_v2", side_effect=AssertionError("unexpected upgrade")) as deep,
        patch("app.improvement.lifecycle.finalize_improvement_cycle"),
    ):
        result = run_task_by_mode(db, root.run_id, r12_settings)
    quick.assert_called_once()
    deep.assert_not_called()
    assert result["status"] == "failed"


def test_incomplete_is_terminal_and_is_not_reexecuted(db, r12_settings):
    root = create_root(db)
    store.update_agent_run_status(db, root.run_id, "incomplete", "evidence gap")
    before = store.get_fresh_agent_run(db, root.run_id).plan_json
    with patch("app.agent.dispatcher.run_plan") as quick:
        run_task_by_mode(db, root.run_id, r12_settings)
    quick.assert_not_called()
    assert store.get_fresh_agent_run(db, root.run_id).plan_json == before


def test_disabled_deep_never_downgrades_to_planned(db, r12_settings):
    root = create_root(db)
    plan = json.loads(root.plan_json)
    plan.update(research_mode="deep")
    store.replace_agent_run_plan(db, root.run_id, plan)
    with patch("app.agent.dispatcher.run_plan") as quick:
        result = run_task_by_mode(db, root.run_id, r12_settings.model_copy(update={"react_enabled": False}))
    quick.assert_not_called()
    assert result["status"] == "failed"
