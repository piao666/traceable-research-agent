"""R8.6 read-only public API explanations and persisted recovery integration."""
import asyncio
import json
import time
import unittest

from sqlalchemy import select, func
from app.agent.budget import BudgetRuntime, ensure_budget
from app.agent.execution_view import execution_insights
from app.api.tasks import get_task_plan, get_plan_review
from app.schemas import TaskPlanResponse
from app.trace import store
from app.trace.logger import record_trace_event
from app.trace.models import RunBudget
from tests import test_r8_recovery as recovery


class ExecutionViewTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    skill_plan = recovery.RecoveryTests.skill_plan
    run_script = recovery.RecoveryTests.run_script

    def create(self, state=None, allowed=None):
        run = store.create_agent_run(self.db, "R8 view fixture", "summary", "real")
        plan = self.skill_plan(allowed)
        if state is not None:
            plan["react_state"] = state
        store.update_agent_run_plan(self.db, run.run_id, plan)
        return run, plan

    def test_read_only_plan_does_not_create_budget_or_rewrite_legacy(self):
        run, _ = self.create()
        before = run.plan_json
        response = asyncio.run(get_task_plan(run.run_id, self.db))
        self.assertIsNone(response.execution_budget)
        self.assertFalse(response.execution_insights.recovery_recorded)
        self.assertTrue(response.execution_insights.source_context.gaps.no_sources)
        self.assertEqual(run.plan_json, before)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RunBudget)), 0)

    def test_source_context_is_rebuilt_from_trace_not_fabricated_plan_context(self):
        run, plan = self.create({"source_context": {"sources": [{"url": "https://forged.invalid"}]}})
        trace = record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "sample",
            {"results": [{"url": recovery.URL, "title": "Official", "content": "sample"},
                         {"url": "https://example.net/?token=secret", "content": "hidden"}]})
        before = run.plan_json
        response = asyncio.run(get_task_plan(run.run_id, self.db))
        candidates = response.execution_insights.source_context.sources
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].trace_ids, [trace.trace_id])
        self.assertEqual(candidates[0].url, recovery.URL)
        self.assertEqual(run.plan_json, before)

    def test_disabled_github_does_not_mark_other_tools_disabled(self):
        run, plan = self.create({"same_tool_max_calls": 3, "tool_call_counts": {"mcp_github_search": 1},
            "tool_recovery": {"mcp_github_search": {"status": "disabled", "reason": "auth_error"}}})
        data = execution_insights(run, plan, [])
        tools = {t["name"]: t for t in data["tools"]}
        self.assertEqual(tools["mcp_github_search"]["status"], "disabled")
        self.assertEqual(tools["tavily_search"]["status"], "available")

    def test_expired_cooldown_shows_selection_eligible_without_mutation(self):
        run, plan = self.create({"same_tool_max_calls": 3, "tool_recovery": {
            "tavily_search": {"status": "cooldown", "reason": "rate_limited", "retry_at": time.time()-1}}})
        before = run.plan_json
        tool = next(t for t in execution_insights(run, plan, [])["tools"] if t["name"] == "tavily_search")
        self.assertEqual(tool["status"], "available")
        self.assertIsNone(tool["retry_at"])
        self.assertEqual(run.plan_json, before)

    def test_cooldown_and_input_restrictions_keep_distinct_scope(self):
        run, plan = self.create({"same_tool_max_calls": 3, "tool_call_counts": {"tavily_search": 1},
            "tool_recovery": {"tavily_search": {"status": "cooldown", "reason": "rate_limited", "retry_at": time.time()+60},
                              "web_fetcher": {"status": "available", "reason": "input_blocked", "blocked_inputs": {"private-hash": "not_found"}}}})
        data = execution_insights(run, plan, [])
        tools = {t["name"]: t for t in data["tools"]}
        self.assertEqual(tools["tavily_search"]["status"], "cooldown")
        self.assertEqual(tools["web_fetcher"]["status"], "available")
        self.assertEqual(tools["web_fetcher"]["blocked_input_count"], 1)
        self.assertNotIn("private-hash", json.dumps(data))

    def test_single_tool_exhaustion_is_not_shared_budget_exhaustion(self):
        run, plan = self.create({"same_tool_max_calls": 3, "tool_call_counts": {"tavily_search": 3}})
        BudgetRuntime(self.db, run.run_id, self.settings).tool("tavily_search")
        response = asyncio.run(get_task_plan(run.run_id, self.db))
        tools = {t.name: t for t in response.execution_insights.tools}
        self.assertEqual(tools["tavily_search"].status, "exhausted")
        self.assertIsNone(response.execution_budget.stop_reason)

    def test_run_permission_override_is_honored_in_plan_and_review(self):
        run, plan = self.create()
        run.allowed_tools_json = "[]"
        self.db.commit()
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan", None)
        response = asyncio.run(get_task_plan(run.run_id, self.db))
        self.assertEqual(response.allowed_tools, [])
        self.assertEqual(response.execution_insights.tools, [])
        review = asyncio.run(get_plan_review(run.run_id, self.db))
        self.assertEqual(review.allowed_tools, [])
        self.assertEqual(review.source_mode, "real")
        self.assertFalse(review.preflight.ready)

    def test_child_plan_budget_uses_live_parent_counter_not_saved_snapshot(self):
        root, _ = self.create()
        child, _ = self.create()
        ensure_budget(self.db, child.run_id, self.settings, parent_run_id=root.run_id)
        runtime = BudgetRuntime(self.db, root.run_id, self.settings)
        runtime.tool("web_fetcher")
        response = asyncio.run(get_task_plan(child.run_id, self.db))
        self.assertEqual(response.execution_budget.tool_calls, 1)
        self.assertEqual(response.execution_budget.root_run_id, root.run_id)
        runtime.tool("tavily_search")
        self.assertEqual(asyncio.run(get_task_plan(child.run_id, self.db)).execution_budget.tool_calls, 2)

    def test_401_recovery_pipeline_exposes_terminal_queue_and_typed_budget(self):
        run, result, _, _ = self.run_script([recovery.decision("tavily_search", query="docs"),
            recovery.decision("mcp_github_search", query="repos"),
            recovery.decision("web_fetcher", urls=[recovery.URL]), recovery.decision("finish")])
        self.assertEqual(result["status"], "completed")
        response = asyncio.run(get_task_plan(run.run_id, self.db))
        TaskPlanResponse.model_validate_json(response.model_dump_json())
        self.assertEqual(response.execution_insights.source_context.gaps.fetched, 1)
        self.assertEqual(response.execution_budget.tool_calls, 3)
        self.assertEqual(next(t for t in response.execution_insights.tools if t.name == "mcp_github_search").status, "disabled")
