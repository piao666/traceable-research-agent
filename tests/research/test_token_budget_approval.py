from __future__ import annotations
import json
import pytest
from fastapi import HTTPException
from app.agent.budget import (BudgetRuntime, BudgetExceeded, TokenBudgetApprovalRequired,
    budgeted_execution, current_budget, pause_for_token_approval, approve_token_budget)
from app.trace import store
from app.trace.models import RunBudget
from app.api.tasks import confirm_task
from app.schemas import TaskConfirmRequest
from .conftest import create_root, db, r12_settings


def _new_run(db):
    run = create_root(db)
    plan = json.loads(run.plan_json)
    plan["task_contract"]["obligation_version"] = "research-obligations-v1"
    store.replace_agent_run_plan(db, run.run_id, plan)
    return run


def test_token_limit_pauses_new_run_without_terminal_failure(db, r12_settings):
    run = _new_run(db)
    settings = r12_settings.model_copy(update={"research_max_tokens": 1000})
    @budgeted_execution
    def execute(db, run_id, settings_obj):
        store.update_agent_run_status(db, run_id, "running", None)
        current_budget().reserve(tokens=1100)
        raise AssertionError("provider must not run before approval")
    result = execute(db, run.run_id, settings)
    assert result["status"] == "waiting_human"
    fresh = store.get_fresh_agent_run(db, run.run_id)
    plan = json.loads(fresh.plan_json)
    assert plan["token_budget_approval"]["status"] == "pending"
    assert "terminal_decision" not in plan
    assert db.get(RunBudget, run.run_id).stop_reason is None
    assert [t.tool_name for t in store.list_tool_traces(db, run.run_id)] == ["token_budget_approval"]


def test_finite_approval_preserves_counters_and_does_not_grant_tool_permission(db, r12_settings):
    run = _new_run(db)
    runtime = BudgetRuntime(db, run.run_id, r12_settings.model_copy(update={"research_max_tokens": 1000}))
    runtime.reserve(tokens=100)
    pause_for_token_approval(db, run.run_id)
    result = confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_tokens=3000), db=db, start_async=False)
    assert result.status == "pending"
    row = db.get(RunBudget, run.run_id, populate_existing=True)
    assert row.reserved_tokens == 100
    assert json.loads(row.limits_json)["max_tokens"] == 3000
    plan = json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)
    assert "confirmation" not in plan
    assert plan["allowed_tools"] == ["web_fetcher"]
    assert len(plan["token_budget_approval_history"]) == 1
    with pytest.raises(HTTPException):
        confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_tokens=4000), db=db, start_async=False)


def test_unlimited_tokens_require_explicit_pending_approval_and_keep_other_caps(db, r12_settings):
    run = _new_run(db)
    settings = r12_settings.model_copy(update={"research_max_tokens": 1000, "research_max_tool_calls": 1})
    BudgetRuntime(db, run.run_id, settings)
    pause_for_token_approval(db, run.run_id)
    confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, unlimited_tokens=True), db=db, start_async=False)
    runtime = BudgetRuntime(db, run.run_id, settings)
    runtime.reserve(tokens=10_000_000, tool=1)
    assert runtime.snapshot()["accounted_tokens"] == 10_000_000
    with pytest.raises(BudgetExceeded) as exc:
        runtime.reserve(tool=1)
    assert exc.value.reason == "tool_calls"


def test_invalid_extension_does_not_consume_approval(db, r12_settings):
    run = _new_run(db)
    BudgetRuntime(db, run.run_id, r12_settings)
    pause_for_token_approval(db, run.run_id)
    with pytest.raises(HTTPException) as exc:
        confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_tokens=1), db=db, start_async=False)
    assert exc.value.status_code == 400
    assert store.get_fresh_agent_run(db, run.run_id).status == "waiting_human"


def test_report_audit_appends_candidates_after_resume(db):
    from app.agent.report_generation import ReportGenerationAudit
    run = _new_run(db)
    first = ReportGenerationAudit(db, run.run_id, [])
    candidate = first.persist_attempt(0, "First retained draft.", {})
    resumed = ReportGenerationAudit(db, run.run_id, store.list_tool_traces(db, run.run_id))
    new = resumed.persist_attempt(0, "Second retained draft.", {})
    resumed.persist_validation(0, new, {"code": "accepted"})
    assert candidate != new
    assert [a["attempt"] for a in resumed.manifest()["attempts"]] == [0, 1]
    assert resumed.manifest()["attempts"][1]["outcome"] == "accepted"


def test_budget_audit_survives_redaction_and_nested_secrets_do_not():
    from app.security.redaction import redact_sensitive_data
    approval = {"status": "pending", "spent_tokens": 17000, "suggested_limit": 400000,
                "api_token": "private-value"}
    clean = redact_sensitive_data({"token_budget_approval": approval,
                                  "token_budget_approval_history": [approval], "access_token": "private-value"})
    assert clean["token_budget_approval"]["suggested_limit"] == 400000
    assert clean["token_budget_approval_history"][0]["spent_tokens"] == 17000
    assert clean["token_budget_approval"]["api_token"] == "[REDACTED]"
    assert clean["access_token"] == "[REDACTED]"


def test_model_call_limit_pauses_and_scoped_approval_keeps_other_limits(db, r12_settings):
    from app.agent.budget import finalization_budget
    run = _new_run(db)
    settings = r12_settings.model_copy(update={"research_max_llm_calls": 2})
    @budgeted_execution
    def execute(db, run_id, settings_obj):
        store.update_agent_run_status(db, run_id, "running", None)
        with finalization_budget():
            current_budget().reserve(llm=2, tokens=100)
            current_budget().reserve(llm=1)
        raise AssertionError("provider must not run before approval")
    assert execute(db, run.run_id, settings)["status"] == "waiting_human"
    before = BudgetRuntime(db, run.run_id, settings).snapshot()
    assert before["llm_calls"] == 2 and before["stop_reason"] is None
    plan = json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)
    assert plan["llm_call_budget_approval"]["spent_llm_calls"] == 2
    with pytest.raises(HTTPException):
        confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, unlimited_tokens=True), db=db)
    with pytest.raises(HTTPException):
        confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_llm_calls=2), db=db)
    assert store.get_fresh_agent_run(db, run.run_id).status == "waiting_human"
    confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_llm_calls=5), db=db)
    after = BudgetRuntime(db, run.run_id, settings).snapshot()
    assert after["llm_calls"] == 2 and after["accounted_tokens"] == 100
    assert after["limits"]["max_llm_calls"] == 5
    for key in ("max_tokens", "max_tool_calls", "max_seconds", "tokens_unlimited"):
        assert after["limits"].get(key) == before["limits"].get(key)
    assert "confirmation" not in json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)


def test_approved_manual_start_excludes_deployment_wait_and_preserves_counters(db, r12_settings, monkeypatch):
    from app.agent.budget import finalization_budget
    clock = [1000.0]
    monkeypatch.setattr("app.agent.budget.time.time", lambda: clock[0])
    run = _new_run(db)
    settings = r12_settings.model_copy(update={"research_max_seconds": 1000})
    runtime = BudgetRuntime(db, run.run_id, settings)
    runtime.reserve(tokens=100)
    pause_for_token_approval(db, run.run_id)
    clock[0] = 1200.0
    confirm_task(run.run_id, TaskConfirmRequest(approved=True, resume=False, max_tokens=400000), db=db)
    assert db.get(RunBudget, run.run_id, populate_existing=True).deadline == 2000.0
    assert "execution_budget_pause" in json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)
    clock[0] = 4000.0  # Deployment wait has exceeded the original deadline.
    @budgeted_execution
    def execute(db, run_id, settings_obj):
        store.update_agent_run_status(db, run_id, "running", None)
        with finalization_budget(): current_budget().reserve(llm=1, tokens=10)
        return {"status": "running"}
    execute(db, run.run_id, settings)
    row = db.get(RunBudget, run.run_id, populate_existing=True)
    assert row.deadline == 5000.0
    assert row.llm_calls == 1 and row.reserved_tokens == 110 and row.stop_reason is None
    execute(db, run.run_id, settings)
    assert db.get(RunBudget, run.run_id, populate_existing=True).deadline == 5000.0
