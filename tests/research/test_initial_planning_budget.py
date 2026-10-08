"""API planning is part of the root ledger, not free pre-execution work."""
import json
from unittest.mock import patch

import pytest

from app.agent.budget import BudgetClient, budget_snapshot, current_budget
from app.api import tasks
from app.config import Settings
from app.llm.base import LLMMessage
from app.schemas import TaskCreateRequest, TaskRetryRequest
from app.trace import store
from tests.support.fake_react_llm import FakeReActLLMClient


@pytest.mark.parametrize("approval", [False, True])
def test_create_accounts_for_planning_before_execution(db, approval):
    config = Settings(research_max_llm_calls=12, research_max_tokens=200000)
    def planner(**kwargs):
        assert current_budget() is not None
        BudgetClient(FakeReActLLMClient(["ok"])).complete(
            [LLMMessage(role="user", content=kwargs["task"])], max_tokens=32)
        return {"steps": [], "task": kwargs["task"], "execution_mode": "planned", "notes": []}

    name = "plan_task_for_review" if approval else "plan_task"
    with patch.object(tasks, "settings", config), patch.object(tasks, name, side_effect=planner):
        response = tasks.create_task(TaskCreateRequest(task="Explain bounded research", require_plan_approval=approval), db)
    assert response.status == ("waiting_human_plan" if approval else "pending")
    snapshot = budget_snapshot(db, response.run_id)
    assert snapshot["llm_calls"] == 1
    assert snapshot["accounted_tokens"] > 0
    assert current_budget() is None


def test_planning_exhaustion_remains_failed_instead_of_waiting_for_approval(db):
    config = Settings(research_max_tool_calls=1)
    def planner(**kwargs):
        current_budget().stop("tokens")

    with patch.object(tasks, "settings", config), patch.object(tasks, "plan_task_for_review", side_effect=planner):
        response = tasks.create_task(TaskCreateRequest(task="Explain research", require_plan_approval=True), db)
    run = store.get_fresh_agent_run(db, response.run_id)
    assert response.status == run.status == "failed"
    assert budget_snapshot(db, run.run_id)["stop_reason"] == "tokens"
    assert "budget_exhausted" in str(json.loads(run.plan_json))
    assert current_budget() is None


@pytest.mark.parametrize("exhaust", [False, True])
def test_retry_replanning_uses_new_root_budget(db, exhaust):
    original = store.create_agent_run(db, "Explain research", "summary", "real")
    store.update_agent_run_status(db, original.run_id, "failed", "previous failure")
    def planner(**kwargs):
        assert current_budget().run_id != original.run_id
        if exhaust:
            current_budget().stop("tokens")
        BudgetClient(FakeReActLLMClient(["ok"])).complete(
            [LLMMessage(role="user", content=kwargs["task"])], max_tokens=32)
        return {"steps": [], "notes": [], "execution_mode": "planned"}
    config = Settings(offline_mode=False)
    understanding = BudgetClient(FakeReActLLMClient(["{}"] ))
    with patch.object(tasks, "settings", config), patch.object(tasks, "plan_task", side_effect=planner), \
            patch("app.agent.report_generation.resolve_report_llm_client", return_value=understanding):
        response = tasks.retry_task(original.run_id, TaskRetryRequest(reuse_plan=False), db)
    snapshot = budget_snapshot(db, response.run_id)
    assert snapshot["root_run_id"] == response.run_id
    assert response.status == ("failed" if exhaust else "pending")
    if exhaust:
        assert snapshot["stop_reason"] == "tokens"
        assert "budget_exhausted" in store.get_fresh_agent_run(db, response.run_id).plan_json
    else:
        assert snapshot["llm_calls"] == 2  # planning and mandatory task understanding
        assert json.loads(store.get_fresh_agent_run(db, response.run_id).plan_json)["task_contract"]["obligation_version"]
    assert store.get_fresh_agent_run(db, original.run_id).error_message == "previous failure"
