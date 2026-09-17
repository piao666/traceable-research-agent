from unittest.mock import patch
import json

from app.agent.dispatcher import run_task_by_mode
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
