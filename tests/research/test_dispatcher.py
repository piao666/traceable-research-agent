from unittest.mock import patch
import json

from app.agent.dispatcher import _pear_rollout_decision, run_task_by_mode
from app.eval.fake_react_llm import FakeReActLLMClient
from app.trace import store

from .conftest import create_root


def test_deep_dispatcher_calls_engine_v2_directly(db, r12_settings):
    root = create_root(db)
    expected = {
        "run_id": root.run_id,
        "status": "running",
        "current_step": 0,
        "total_steps": 0,
        "total_tool_calls": 0,
        "report_url": f"/api/reports/{root.run_id}",
        "trace_url": f"/api/tasks/{root.run_id}/trace",
        "error_message": None,
        "message": "fixture",
    }
    client = FakeReActLLMClient([])
    with patch(
        "app.research.orchestrator.run_deep_research_v2", return_value=expected
    ) as engine:
        run_task_by_mode(db, root.run_id, r12_settings, client)
    engine.assert_called_once()


def test_quick_dispatcher_never_enters_adaptive_react_gate(db, r12_settings):
    root = create_root(db)
    plan = json.loads(root.plan_json)
    plan.update({"research_mode": "quick", "execution_mode": "planned"})
    store.replace_agent_run_plan(db, root.run_id, plan)

    def fake_plan(session, run_id, **kwargs):
        current = store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": current.status}

    with (
        patch("app.agent.dispatcher.run_plan", side_effect=fake_plan) as planned,
        patch("app.agent.dispatcher._adaptive_upgrade_reason", side_effect=AssertionError("quick adaptive gate")),
        patch("app.improvement.lifecycle.finalize_improvement_cycle"),
    ):
        result = run_task_by_mode(
            db,
            root.run_id,
            r12_settings.model_copy(update={"react_enabled": True, "deep_research_enabled": True}),
        )

    planned.assert_called_once()
    assert result["status"] == "completed"


def test_pear_rollout_is_deterministic_and_only_targets_auto_mode(r12_settings):
    auto = {"research_mode": "auto"}
    first = _pear_rollout_decision("run-42", auto, r12_settings.model_copy(update={"pear_rollout_percent": 37}))
    second = _pear_rollout_decision("run-42", auto, r12_settings.model_copy(update={"pear_rollout_percent": 37}))
    assert first == second
    assert first["bucket"] is not None
    assert _pear_rollout_decision("run-42", auto, r12_settings.model_copy(update={"pear_rollout_percent": 0}))["selected"] is False
    assert _pear_rollout_decision("run-42", auto, r12_settings.model_copy(update={"pear_rollout_percent": 100}))["selected"] is True
    assert _pear_rollout_decision("run-42", {"research_mode": "quick"}, r12_settings.model_copy(update={"pear_rollout_percent": 100}))["selected"] is False


def test_auto_rollout_switches_only_new_run_controller(db, r12_settings):
    def prepare(percent):
        root = create_root(db)
        plan = json.loads(root.plan_json)
        plan.update({"research_mode": "auto", "execution_mode": "react"})
        store.replace_agent_run_plan(db, root.run_id, plan)
        return root, r12_settings.model_copy(update={"pear_rollout_percent": percent})

    legacy_root, legacy_settings = prepare(0)
    with (
        patch("app.agent.dispatcher.enforce_execution_readiness", return_value=True),
        patch("app.agent.react_executor.run_react_task", return_value={"run_id": legacy_root.run_id, "status": "completed"}) as legacy,
        patch("app.research.orchestrator.run_deep_research_v2", side_effect=AssertionError("unexpected PEAR")),
        patch("app.agent.dispatcher._finalize_result", side_effect=lambda _db, _id, result: result),
    ):
        run_task_by_mode(db, legacy_root.run_id, legacy_settings)
    legacy.assert_called_once()
    observed_legacy_plan = json.loads(store.get_agent_run(db, legacy_root.run_id).plan_json)
    assert observed_legacy_plan["pear_rollout"]["selected"] is False

    pear_root, pear_settings = prepare(100)
    with (
        patch("app.agent.dispatcher.enforce_execution_readiness", return_value=True),
        patch("app.research.orchestrator.run_deep_research_v2", return_value={"run_id": pear_root.run_id, "status": "completed"}) as pear,
        patch("app.agent.react_executor.run_react_task", side_effect=AssertionError("unexpected legacy ReAct")),
        patch("app.agent.dispatcher._finalize_result", side_effect=lambda _db, _id, result: result),
    ):
        run_task_by_mode(db, pear_root.run_id, pear_settings)
    pear.assert_called_once()
    observed_pear_plan = json.loads(store.get_agent_run(db, pear_root.run_id).plan_json)
    assert observed_pear_plan["pear_rollout"]["selected"] is True


def test_auto_rollout_can_promote_planned_cohort_to_pear(db, r12_settings):
    root = create_root(db)
    plan = json.loads(root.plan_json)
    plan.update({"research_mode": "auto", "execution_mode": "planned"})
    store.replace_agent_run_plan(db, root.run_id, plan)
    settings = r12_settings.model_copy(update={"pear_rollout_percent": 100, "deep_research_enabled": True})
    with (
        patch("app.agent.dispatcher.enforce_execution_readiness", return_value=True),
        patch("app.research.orchestrator.run_deep_research_v2", return_value={"run_id": root.run_id, "status": "completed"}) as pear,
        patch("app.agent.react_executor.run_react_task", side_effect=AssertionError("unexpected legacy ReAct")),
        patch("app.agent.dispatcher._finalize_result", side_effect=lambda _db, _id, result: result),
    ):
        run_task_by_mode(db, root.run_id, settings)
    pear.assert_called_once()
    observed_plan = json.loads(store.get_agent_run(db, root.run_id).plan_json)
    assert observed_plan["execution_mode"] == "react"
