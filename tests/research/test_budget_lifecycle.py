from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from app.agent.budget import (
    BudgetExceeded,
    BudgetRuntime,
    FinalizationRequired,
    _active,
    current_budget,
    ensure_budget,
    limits,
    budgeted_execution,
    pause_budget_deadline,
    planning_budget,
    report_budget,
    resume_budget_deadline,
)
from app.trace import store
from app.trace.models import RunBudget

from .conftest import create_root


def test_report_recovery_fetch_cannot_spend_finalization_reserve(db, r12_settings):
    from app.agent.budget import acquisition_budget
    run = create_root(db)
    runtime = BudgetRuntime(db, run.run_id, r12_settings.model_copy(update={
        "research_max_estimated_cost": 10,
    }))
    runtime.reserve(cost=8)
    @report_budget
    def final_report(run, plan):
        with pytest.raises(FinalizationRequired):
            with acquisition_budget():
                runtime.reserve(cost=1)
        runtime.reserve(cost=1)
    token = _active.set(runtime)
    try:
        final_report(run, {})
    finally:
        _active.reset(token)
    assert runtime.snapshot()["estimated_cost"] == 9
    assert runtime.snapshot()["stop_reason"] is None


def test_report_time_and_cost_reserves_stay_inside_configured_caps(db, r12_settings):
    settings = r12_settings.model_copy(update={
        "research_max_seconds": 100,
        "research_max_estimated_cost": 10,
        "research_tool_cost_estimate": 1,
    })
    configured = limits(settings)
    assert configured["final_report_seconds"] == 20
    assert configured["final_report_cost"] == 2
    assert configured["final_report_seconds"] <= configured["max_seconds"]
    assert configured["final_report_cost"] <= configured["max_estimated_cost"]

    run = create_root(db)
    runtime = BudgetRuntime(db, run.run_id, settings)
    runtime.reserve(cost=8)
    with pytest.raises(FinalizationRequired) as boundary:
        runtime.reserve(cost=0.1)
    assert boundary.value.reason == "estimated_cost"
    assert runtime.snapshot()["estimated_cost"] == 8

    @report_budget
    def final_report(run_row, plan):
        assert run_row.run_id == run.run_id
        assert plan == {}
        runtime.reserve(cost=2)

    token = _active.set(runtime)
    try:
        final_report(run, {})
    finally:
        _active.reset(token)
    assert runtime.snapshot()["estimated_cost"] == 10

    with pytest.raises(BudgetExceeded) as hard_cap:
        runtime.reserve(cost=0.1)
    assert hard_cap.value.reason == "estimated_cost"


def test_report_time_reserve_hands_off_before_deadline_but_deadline_remains_hard(
    db, r12_settings, monkeypatch,
):
    run = create_root(db)
    settings = r12_settings.model_copy(update={"research_max_seconds": 100})
    runtime = BudgetRuntime(db, run.run_id, settings)
    deadline = runtime.snapshot()["deadline"]
    monkeypatch.setattr("app.agent.budget.time.time", lambda: deadline - 1)
    with pytest.raises(FinalizationRequired) as boundary:
        runtime.reserve()
    assert boundary.value.reason == "deadline"
    assert runtime.snapshot()["stop_reason"] is None

    @report_budget
    def final_report(run_row, plan):
        runtime.reserve()

    token = _active.set(runtime)
    try:
        final_report(run, {})
    finally:
        _active.reset(token)

    monkeypatch.setattr("app.agent.budget.time.time", lambda: deadline + 1)
    with pytest.raises(BudgetExceeded) as expired:
        final_report(run, {})
    assert expired.value.reason == "deadline"


def test_planning_budget_installs_and_restores_the_shared_root_runtime(db, r12_settings):
    run = create_root(db)
    assert current_budget() is None
    with planning_budget(db, run.run_id, r12_settings) as runtime:
        assert current_budget() is runtime
        assert runtime.run_id == run.run_id
        runtime.reserve(llm=1, tokens=20)
    assert current_budget() is None
    assert runtime.snapshot()["llm_calls"] == 1
    assert runtime.snapshot()["accounted_tokens"] == 20


def test_wait_pause_and_resume_extend_once_without_resetting_usage(db, r12_settings):
    run = create_root(db)
    store.update_agent_run_status(db, run.run_id, "waiting_human_plan", None)
    runtime = BudgetRuntime(db, run.run_id, r12_settings)
    runtime.reserve(tool=1, tokens=25)
    original_deadline = runtime.snapshot()["deadline"]
    pause_started = original_deadline - 10

    assert pause_budget_deadline(db, run.run_id, now=pause_started)
    assert not pause_budget_deadline(db, run.run_id, now=pause_started + 1)
    marker = json.loads(store.get_fresh_agent_run(db, run.run_id).plan_json)["execution_budget_pause"]
    assert marker["root_run_id"] == run.run_id
    assert marker["waiting_run_ids"] == [run.run_id]

    store.update_agent_run_status(db, run.run_id, "pending", None)
    assert resume_budget_deadline(db, run.run_id, now=original_deadline + 100)
    assert not resume_budget_deadline(db, run.run_id, now=original_deadline + 101)
    assert runtime.snapshot()["deadline"] == pytest.approx(original_deadline + 110)
    assert runtime.snapshot()["tool_calls"] == 1
    assert runtime.snapshot()["accounted_tokens"] == 25
    assert "execution_budget_pause" not in json.loads(
        store.get_fresh_agent_run(db, run.run_id).plan_json
    )


def test_budgeted_execution_pauses_deadline_when_run_enters_human_wait(db, r12_settings):
    run = create_root(db)

    @budgeted_execution
    def request_confirmation(db, run_id, settings_obj):
        del settings_obj
        store.update_agent_run_status(db, run_id, "waiting_human", "confirm this step")
        return {"run_id": run_id, "status": "waiting_human"}

    result = request_confirmation(db, run.run_id, r12_settings)
    assert result["status"] == "waiting_human"
    refreshed = store.get_fresh_agent_run(db, run.run_id)
    plan = json.loads(refreshed.plan_json)
    assert plan["execution_budget_pause"]["root_run_id"] == run.run_id
    assert plan["execution_budget_pause"]["waiting_run_ids"] == [run.run_id]


def test_overlapping_root_and_child_waiters_pause_shared_deadline_once(db, r12_settings):
    root = create_root(db)
    store.update_agent_run_status(db, root.run_id, "waiting_human_plan", None)
    runtime = BudgetRuntime(db, root.run_id, r12_settings)
    child = store.create_agent_run(
        db, "child confirmation", "summary", "real", parent_run_id=root.run_id,
        root_run_id=root.run_id, research_scope_id=None, run_role="research_branch",
        engine_version="v2",
    )
    ensure_budget(db, child.run_id, r12_settings, parent_run_id=root.run_id)
    store.update_agent_run_status(db, child.run_id, "waiting_human", None)
    deadline = runtime.snapshot()["deadline"]
    started = deadline - 30
    assert pause_budget_deadline(db, root.run_id, now=started)
    assert pause_budget_deadline(db, child.run_id, now=started + 2)

    store.update_agent_run_status(db, child.run_id, "pending", None)
    assert resume_budget_deadline(db, child.run_id, now=deadline + 50)
    assert runtime.snapshot()["deadline"] == deadline
    waiters = json.loads(store.get_fresh_agent_run(db, root.run_id).plan_json)[
        "execution_budget_pause"]["waiting_run_ids"]
    assert waiters == [root.run_id]

    store.update_agent_run_status(db, root.run_id, "pending", None)
    assert resume_budget_deadline(db, root.run_id, now=deadline + 100)
    assert runtime.snapshot()["deadline"] == pytest.approx(deadline + 130)


def test_stopped_or_expired_budget_is_not_resumed_or_extended(db, r12_settings):
    run = create_root(db)
    store.update_agent_run_status(db, run.run_id, "waiting_human", None)
    runtime = BudgetRuntime(db, run.run_id, r12_settings)
    deadline = runtime.snapshot()["deadline"]
    assert not pause_budget_deadline(db, run.run_id, now=deadline)
    assert pause_budget_deadline(db, run.run_id, now=deadline - 1)
    db.execute(update(RunBudget).where(RunBudget.run_id == run.run_id).values(stop_reason="tokens"))
    db.commit()
    store.update_agent_run_status(db, run.run_id, "pending", None)

    assert not resume_budget_deadline(db, run.run_id, now=deadline + 20)
    assert runtime.snapshot()["deadline"] == deadline
    assert runtime.snapshot()["stop_reason"] == "tokens"
    assert "execution_budget_pause" in json.loads(
        store.get_fresh_agent_run(db, run.run_id).plan_json
    )


def test_wait_helpers_do_not_create_ledgers_for_historical_runs(db):
    run = store.create_agent_run(db, "historical fixture", "summary", "real")
    store.update_agent_run_status(db, run.run_id, "waiting_human_plan", None)
    assert not pause_budget_deadline(db, run.run_id)
    assert not resume_budget_deadline(db, run.run_id)
    assert db.get(RunBudget, run.run_id) is None


def test_scope_projection_registers_child_not_root_and_excludes_only_scope_wait(db, r12_settings):
    from app.research.scope import create_research_node, create_research_scope
    root = create_root(db)
    store.update_agent_run_status(db, root.run_id, "running", None)
    runtime = BudgetRuntime(db, root.run_id, r12_settings)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(db, "approval", "summary", "real", parent_run_id=root.run_id)
    ensure_budget(db, child.run_id, r12_settings, parent_run_id=root.run_id)
    create_research_node(db, scope.scope_id, parent_node_id=None, run_id=child.run_id,
                        node_type="verification", topic="approval", query="approval",
                        research_goal="approval", depth=1, priority=1, status="waiting_human")
    store.update_agent_run_status(db, child.run_id, "waiting_human", None)
    deadline = runtime.snapshot()["deadline"]
    assert not pause_budget_deadline(db, child.run_id, now=deadline - 40)
    store.update_agent_run_status(db, root.run_id, "waiting_human", None)
    assert pause_budget_deadline(db, root.run_id, now=deadline - 30)
    marker = json.loads(store.get_fresh_agent_run(db, root.run_id).plan_json)["execution_budget_pause"]
    assert marker["waiting_run_ids"] == [child.run_id]
    store.update_agent_run_status(db, child.run_id, "pending", None)
    assert resume_budget_deadline(db, child.run_id, now=deadline + 10)
    assert runtime.snapshot()["deadline"] == pytest.approx(deadline + 40)
    assert "execution_budget_pause" not in json.loads(store.get_fresh_agent_run(db, root.run_id).plan_json)
