import json
from unittest.mock import patch

import pytest

from app.agent.react_executor import _complete_report
from app.agent.budget import BudgetRuntime, FinalizationRequired, budget_snapshot, ensure_budget
from app.research.scope import create_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root


def test_react_node_finalization_materializes_without_intermediate_report(db, r12_settings):
    root = create_root(db)
    create_research_scope(db, root.run_id, {})
    plan = json.loads(root.plan_json)
    plan["defer_to_research_scope"] = True
    state = {"observation_history": [], "max_steps": 1, "step_limit": 1, "step_offset": 0}
    store.replace_agent_run_plan(db, root.run_id, plan)
    store.update_agent_run_status(db, root.run_id, "running", None)
    add_web_trace(db, root.run_id, "Deferred node evidence.", "deferred")
    with patch("app.agent.react_executor.generate_markdown_report") as report:
        result = _complete_report(
            db, root.run_id, plan, state, "node_complete", r12_settings
        )
    report.assert_not_called()
    assert result["status"] == "running"
    assert store.get_agent_run(db, root.run_id).report_path is None


def test_child_node_finalization_completes_child_run(db, r12_settings):
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(
        db,
        "child",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        research_scope_id=scope.scope_id,
        engine_version="v2",
    )
    plan = json.loads(child.plan_json or "{}")
    plan["defer_to_research_scope"] = True
    state = {"observation_history": [], "max_steps": 1, "step_limit": 1, "step_offset": 0}
    store.replace_agent_run_plan(db, child.run_id, plan)
    store.update_agent_run_status(db, child.run_id, "running", None)
    add_web_trace(db, child.run_id, "Deferred child evidence.", "child")

    result = _complete_report(
        db, child.run_id, plan, state, "node_complete", r12_settings
    )

    assert result["status"] == "completed"


def test_child_finalization_boundary_does_not_poison_root_budget(db, r12_settings):
    root = create_root(db)
    child = store.create_agent_run(
        db,
        "child",
        "summary",
        "real",
        parent_run_id=root.run_id,
        root_run_id=root.run_id,
        run_role="research_branch",
        engine_version="v2",
    )
    tight = r12_settings.model_copy(
        update={"research_max_tokens": 100, "research_max_llm_calls": 10}
    )
    ensure_budget(db, root.run_id, tight)
    ensure_budget(db, child.run_id, tight, parent_run_id=root.run_id)
    runtime = BudgetRuntime(db, child.run_id, tight)
    with pytest.raises(FinalizationRequired):
        runtime.reserve(llm=1, tokens=91)
    assert budget_snapshot(db, root.run_id)["stop_reason"] is None
