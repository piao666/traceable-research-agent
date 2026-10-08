"""Full retry preserves mandatory work without inheriting execution conclusions."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.agent.budget import BudgetClient, BudgetRuntime, budget_snapshot, current_budget
from app.agent.research_goal import build_task_contract
from app.api import tasks
from app.config import Settings
from app.research.scope import create_research_scope, list_scope_runs
from app.research.task_understanding import understand_new_task
from app.schemas import TaskRetryRequest
from app.trace import store
from tests.support.fake_react_llm import FakeReActLLMClient


TASK = "比较最近几款研究工具的原理和记忆"
PROPOSAL = {
    "questions": [{"question_id": "q-method", "text": TASK,
                   "requirement_ids": ["r-mechanism", "r-memory"]}],
    "requirements": [
        {"requirement_id": "r-mechanism", "question_id": "q-method", "kind": "comparison",
         "predicate": "原理", "entity": "research mechanism", "required": True},
        {"requirement_id": "r-memory", "question_id": "q-method", "kind": "comparison",
         "predicate": "记忆", "entity": "research memory", "required": True},
    ],
}


def _previous(db, contract):
    original = store.create_agent_run(db, TASK, "summary", "real", allowed_tools=["web_search"])
    plan = {"steps": [{"step_no": 1, "tool_name": "web_search", "arguments": {"query": TASK}}],
            "notes": [], "allowed_tools": ["web_search"], "execution_mode": "planned",
            "task_contract": deepcopy(contract), "requires_plan_approval": True,
            "answer_coverage": {"status": "complete"}, "branch_findings": {"status": "complete"},
            "answer_recovery": {"attempts": 8}, "research_scope_id": "old-scope",
            "token_budget_approval": {"status": "approved", "unlimited_tokens": True},
            "token_budget_approval_history": [{"status": "approved"}]}
    store.replace_agent_run_plan(db, original.run_id, plan)
    store.update_agent_run_status(db, original.run_id, "incomplete", "previous result")
    BudgetRuntime(db, original.run_id, Settings()).reserve(tokens=100)
    return original


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("retry_request", [None, TaskRetryRequest()])
def test_ui_full_retry_rebuilds_basic_and_lossy_legacy_contract(db, legacy, retry_request):
    contract = build_task_contract(TASK)
    contract["source_constraints"] = {"mode": "restrict", "domains": ["example.org"], "official_only": True}
    if legacy:
        # Historical one-to-many overwrite retained only the final requirement.
        contract.update(obligation_version="research-obligations-v1",
                        questions=PROPOSAL["questions"], requirements=[PROPOSAL["requirements"][-1]])
    old = _previous(db, contract)
    old_plan, old_budget = old.plan_json, budget_snapshot(db, old.run_id)
    client = BudgetClient(FakeReActLLMClient([PROPOSAL]))
    with patch.object(tasks, "settings", Settings(offline_mode=False)), \
         patch("app.agent.report_generation.resolve_report_llm_client", return_value=client):
        response = tasks.retry_task(old.run_id, retry_request, db)
    new = store.get_fresh_agent_run(db, response.run_id)
    result = json.loads(new.plan_json)
    repaired = result["task_contract"]
    assert response.status == "waiting_human_plan"
    assert repaired["obligation_version"] == "research-obligations-v2"
    assert [r["requirement_id"] for r in repaired["requirements"]] == ["r-mechanism", "r-memory", "req-original"]
    assert repaired["questions"][0]["requirement_ids"] == ["r-mechanism", "r-memory"]
    assert repaired["source_constraints"] == contract["source_constraints"]
    assert result["steps"][0]["arguments"]["query"] == TASK
    for field in ("answer_coverage", "branch_findings", "answer_recovery", "research_scope_id",
                  "token_budget_approval", "token_budget_approval_history"):
        assert field not in result
    assert new.parent_run_id == old.run_id
    snapshot = budget_snapshot(db, new.run_id)
    assert snapshot["root_run_id"] == new.run_id and snapshot["llm_calls"] == 1
    assert store.get_fresh_agent_run(db, old.run_id).plan_json == old_plan
    assert budget_snapshot(db, old.run_id) == old_budget
    # Subsequent planner/approval writes still cannot weaken the trusted contract.
    result["task_contract"] = {"requirements": []}
    store.update_agent_run_plan(db, new.run_id, result)
    assert json.loads(new.plan_json)["task_contract"] == repaired


def test_v2_retry_retains_obligations_but_reselects_dynamic_cohort(db):
    contract = understand_new_task(build_task_contract(TASK), FakeReActLLMClient([PROPOSAL]))
    contract["controller_findings"] = {"verified": True}
    contract["comparison_scope"].update(entities=["Old Tool"], selection_attempt={"status": "accepted"})
    old = _previous(db, contract)
    client = FakeReActLLMClient([])
    with patch.object(tasks, "settings", Settings(offline_mode=False)), \
         patch("app.agent.report_generation.resolve_report_llm_client", return_value=client), \
         patch.object(client, "complete", side_effect=AssertionError("valid obligations need no new provider call")):
        response = tasks.retry_task(old.run_id, TaskRetryRequest(), db)
    repaired = json.loads(store.get_fresh_agent_run(db, response.run_id).plan_json)["task_contract"]
    for field in ("questions", "requirements", "requirement_focus", "research_terms"):
        assert repaired[field] == contract[field]
    assert repaired["comparison_scope"]["entities"] == []
    assert "selection_attempt" not in repaired["comparison_scope"]
    assert "controller_findings" not in repaired
    assert budget_snapshot(db, response.run_id)["llm_calls"] == 0


def test_retry_preserves_new_token_pause_instead_of_old_approval_or_plan_review(db):
    contract = understand_new_task(build_task_contract(TASK), FakeReActLLMClient([PROPOSAL]))
    old = _previous(db, contract)
    def exhaust(*_args):
        current_budget().reserve(tokens=100000)
        raise AssertionError("must pause before provider spending")
    with patch.object(tasks, "settings", Settings(offline_mode=False, research_max_tokens=10000)), \
         patch("app.agent.report_generation.resolve_report_llm_client", return_value=None), \
         patch("app.research.task_understanding.understand_new_task", side_effect=exhaust):
        response = tasks.retry_task(old.run_id, TaskRetryRequest(), db)
    new = store.get_fresh_agent_run(db, response.run_id)
    plan = json.loads(new.plan_json)
    assert response.status == new.status == "waiting_human"
    assert plan["token_budget_approval"]["status"] == "pending"
    assert plan["token_budget_approval"]["current_limit"] == 10000
    assert "token_budget_approval_history" not in plan
    assert "terminal_decision" not in plan
    assert plan["task_contract"]["requirements"] == contract["requirements"]
    assert plan["steps"]
    assert budget_snapshot(db, new.run_id)["stop_reason"] is None


def test_retry_scope_retains_predecessor_without_sharing_scope_or_budget(db):
    old = _previous(db, build_task_contract(TASK))
    old_scope = create_research_scope(db, old.run_id, {})
    retry = store.create_agent_run(db, TASK, "summary", "real", parent_run_id=old.run_id, run_role="root")
    new_scope = create_research_scope(db, retry.run_id, {})
    assert retry.parent_run_id == old.run_id
    assert retry.root_run_id == retry.run_id
    assert new_scope.scope_id != old_scope.scope_id
    assert [r.run_id for r in list_scope_runs(db, new_scope.scope_id)] == [retry.run_id]
    BudgetRuntime(db, retry.run_id, Settings()).reserve(tokens=50)
    assert budget_snapshot(db, retry.run_id)["accounted_tokens"] == 50
    assert budget_snapshot(db, old.run_id)["accounted_tokens"] == 100
