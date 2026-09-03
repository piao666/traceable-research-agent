"""Synthetic, offline regressions for real-mode Skill/ReAct recovery."""
from __future__ import annotations

import json
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.improvement import models as improvement_models  # noqa: F401
from app.llm.base import LLMResponse
from app.skills.models import SkillDefinition
from app.tools import registry
from app.tools.base import ToolResult
from app.tools.defaults import register_default_tools
from app.trace import store


URL = "https://example.org/framework-docs"


def decision(tool, **args):
    return {"thought": "Fixture action", "action": tool, "args": args}


class ScriptedLLM:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.payloads = []

    def is_available(self):
        return True

    def describe(self):
        return {"provider": "fixture", "model": "offline"}

    def complete(self, messages, **kwargs):
        self.payloads.append(json.loads(messages[-1].content))
        action = next(self.actions, decision("finish"))
        return LLMResponse(success=True, content=json.dumps(action), provider="fixture", model="offline")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.specs = patch.dict(registry._tool_specs, clear=True)
        self.handlers = patch.dict(registry._tool_handlers, clear=True)
        self.specs.start()
        self.handlers.start()
        self.addCleanup(self.handlers.stop)
        self.addCleanup(self.specs.stop)
        register_default_tools()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.settings = Settings(offline_mode=False, tavily_api_key="fixture-only",
            qwen_api_key="fixture-only", react_enabled=True, react_max_steps=10,
            react_same_tool_max_calls=3, max_refetch_rounds=0,
            report_generation_mode="deterministic", evidence_pipeline_version="v1", evidence_reasoning_enabled=False)

    def skill_plan(self, allowed=None):
        from app.agent.planner import _skill_to_plan
        skill = SkillDefinition.model_validate_json(Path("workspace/skills/deep_web_research.json").read_text())
        return {**_skill_to_plan(skill, "Compare evaluation frameworks", allowed, "real"),
                "execution_mode": "react"}

    def run_script(self, actions, handler=None, plan=None, settings=None):
        from app.agent.react_executor import run_react_task
        plan = plan or self.skill_plan()
        run = store.create_agent_run(self.db, "Compare evaluation frameworks", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, plan)
        client = ScriptedLLM(actions)

        def fixture(tool, args):
            if tool == "tavily_search":
                return ToolResult(success=True, output={"results": [
                    {"url": URL, "title": "Official fixture", "content": "Source summary"}]},
                    output_summary="Found one URL", metadata={"data_source": "tavily_api"})
            if tool == "web_fetcher":
                return ToolResult(success=True, output={"pages": [
                    {"url": URL, "content": "Actual non-GitHub fixture source text.", "content_basis": "full_text"}]},
                    output_summary="Fetched fixture", metadata={"data_source": "web"})
            return ToolResult(success=False, error_message="GitHub public API request failed with HTTP 401.",
                              metadata={"error_type": "api_error", "http_status": 401, "retry_count": 0})

        with (patch("app.agent.react_executor.execute_tool", side_effect=handler or fixture) as execute,
              patch("app.agent.react_executor.generate_markdown_report", return_value="# Fixture report"),
              patch("app.agent.react_executor.save_report", return_value="fixture-not-written.md")):
            result = run_react_task(self.db, run.run_id, settings or self.settings, client)
        return run, result, client, execute

    def test_skill_default_permissions_include_all_required_steps(self):
        plan = self.skill_plan()
        self.assertTrue({s["tool_name"] for s in plan["steps"]} <= set(plan["allowed_tools"]))

    def test_explicit_permission_conflict_is_blocked_not_silently_dropped(self):
        from app.agent.preflight import check_plan_readiness
        plan = self.skill_plan(["tavily_search", "report_writer"])
        self.assertNotIn("web_fetcher", plan["allowed_tools"])
        result = check_plan_readiness(plan, self.settings)
        self.assertFalse(result["ready"])
        self.assertIn("web_fetcher", str(result["blockers"]))

    def test_explicit_deep_template_cannot_silently_omit_forbidden_fetch(self):
        from app.agent.planner import deterministic_plan_task
        from app.agent.preflight import check_plan_readiness
        plan = deterministic_plan_task("Compare frameworks", ["tavily_search", "report_writer"],
                                       "real", "deep_web_research")
        blockers = check_plan_readiness(plan, self.settings)["blockers"]
        self.assertTrue(any(b["code"] == "disallowed_tool" and b["capability"] == "web_fetcher" for b in blockers))
        self.assertNotIn("web_fetcher", plan["allowed_tools"])

    def test_disabled_required_tool_blocks_preflight(self):
        from app.agent.preflight import check_plan_readiness
        registry._tool_specs["web_fetcher"].enabled = False
        self.assertFalse(check_plan_readiness(self.skill_plan(), self.settings)["ready"])

    def test_real_offline_conflict_blocks_preflight(self):
        from app.agent.preflight import check_plan_readiness
        self.assertFalse(check_plan_readiness(self.skill_plan(), Settings(offline_mode=True), llm_available=True)["ready"])

    def test_github_401_is_auth_error(self):
        from app.tools.errors import classify_tool_error
        self.assertEqual(classify_tool_error("api_error", "GitHub public API request failed with HTTP 401.").value,
                         "auth_error")

    def test_401_then_other_sources_can_complete(self):
        # Full incident shape: repeated invalid GitHub selection must not kill the run.
        actions = [decision("tavily_search", query="frameworks"), decision("mcp_github_search", query="repos"),
                   decision("mcp_github_search", query="repos", mode="mock"),
                   decision("tavily_search", query="official docs"),
                   decision("web_fetcher", urls=[URL]), decision("finish")]
        run, result, client, execute = self.run_script(actions)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(sum(c.args[0] == "mcp_github_search" for c in execute.call_args_list), 1)
        self.assertNotIn("mcp_github_search", client.payloads[2]["allowed_tools"])
        self.assertEqual(json.loads(run.plan_json)["research_outcome"]["effective_evidence_count"], 3)

    def test_tool_limit_does_not_end_other_research(self):
        settings = self.settings.model_copy(update={"react_same_tool_max_calls": 1})
        _, result, client, execute = self.run_script([
            decision("tavily_search", query="one"), decision("tavily_search", query="two"),
            decision("web_fetcher", urls=[URL]), decision("finish")], settings=settings)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(sum(c.args[0] == "tavily_search" for c in execute.call_args_list), 1)
        self.assertNotIn("tavily_search", client.payloads[1]["allowed_tools"])

    def test_explicit_mock_argument_never_reaches_tool_in_real_run(self):
        plan = {**self.skill_plan(), "steps": []}
        _, _, _, execute = self.run_script([decision("mcp_github_search", query="x", mode="mock"), decision("finish")], plan=plan)
        execute.assert_not_called()

    def test_real_result_guard_removes_fallback_material(self):
        from app.agent.execution_policy import execute_with_policy
        execute = Mock(return_value=ToolResult(success=True, output={"results": [{"url": URL, "content": "Fake"}]},
            metadata={"data_source": "fallback", "fallback_used": True}))
        result = execute_with_policy("tavily_search", {"query": "x"}, self.skill_plan(), self.settings, execute)
        self.assertFalse(result.success)
        self.assertFalse(result.output)
        self.assertEqual(result.metadata["error_type"], "source_mode_violation")

    def test_prompt_specs_match_effective_permission_list(self):
        from app.agent.react_prompt import build_react_messages
        payload = json.loads(build_react_messages("x", "fixture", ["tavily_search"], registry.list_tools(), [])[-1].content)
        self.assertEqual([t["name"] for t in payload["available_tools"]], ["tavily_search"])

    def test_deep_prompt_does_not_force_github_or_remote_mcp(self):
        from app.agent.react_prompt import build_react_messages
        messages = build_react_messages("x", "fixture", ["tavily_search", "web_fetcher"],
                                       registry.list_tools(), [], "deep_web_research")
        self.assertIn("optional sources", messages[0].content)
        self.assertNotIn("call at least one", messages[0].content)
        self.assertIn("never switch real research to mock", messages[0].content)

    def test_explicit_empty_permission_list_is_persisted(self):
        run = store.create_agent_run(self.db, "No tools", "summary", "real", allowed_tools=[])
        self.assertEqual(json.loads(run.allowed_tools_json), [])

    def test_unknown_required_tool_blocks(self):
        from app.agent.preflight import check_plan_readiness
        plan = {"steps": [{"tool_name": "unregistered"}], "allowed_tools": ["unregistered"]}
        self.assertFalse(check_plan_readiness(plan, self.settings)["ready"])

    def test_mock_demonstration_remains_supported(self):
        from app.agent.execution_policy import execute_with_policy
        execute = Mock(return_value=ToolResult(success=True, output={"results": []}, metadata={"data_source": "mock"}))
        result = execute_with_policy("mcp_github_search", {"query": "fixture", "mode": "mock"},
            {**self.skill_plan(), "source_mode": "mock"}, Settings(offline_mode=True), execute)
        self.assertTrue(result.success)
        self.assertEqual(execute.call_args.args[1]["mode"], "mock")

    def test_mock_default_is_overridden_by_real_run(self):
        from app.agent.execution_policy import execute_with_policy
        execute = Mock(return_value=ToolResult(success=True, output={"results": []}))
        execute_with_policy("mcp_github_search", {"query": "fixture"}, self.skill_plan(),
                            self.settings.model_copy(update={"github_tool_default_mode": "mock"}), execute)
        self.assertEqual(execute.call_args.args[1]["mode"], "public_api")
        self.assertEqual(execute.call_args.args[1]["_max_transport_retries"], 0)

    def test_transport_classifies_401_403_and_429_without_real_network(self):
        from app.tools.mcp_github import github_search
        settings = self.settings.model_copy(update={"github_search_cache_enabled": False,
            "github_public_api_enabled": True, "github_public_api_max_retries": 3})
        for status, headers, expected in [(401, {}, "auth_error"), (403, {}, "forbidden"),
                (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"), (429, {"Retry-After": "30"}, "rate_limited")]:
            with self.subTest(status=status, headers=headers):
                opener = Mock(side_effect=HTTPError("https://example.org", status, "Fixture", headers, None))
                sleeper = Mock()
                result = github_search({"query": "fixture", "mode": "public_api", "_max_transport_retries": 0},
                    settings_obj=settings, opener=opener, sleeper=sleeper)
                self.assertFalse(result.success)
                self.assertEqual(result.metadata["error_type"], expected)
                self.assertEqual(result.metadata["http_status"], status)
                self.assertEqual(opener.call_count, 1)
                sleeper.assert_not_called()

    def test_tavily_preserves_retry_after_and_auth_classification(self):
        from app.tools.tavily_search import tavily_search
        for status, expected in [(401, "auth_error"), (403, "forbidden"), (429, "rate_limited")]:
            headers = {"Retry-After": "30"} if status == 429 else {}
            opener = Mock(side_effect=HTTPError("https://example.org", status, "Fixture", headers, None))
            result = tavily_search({"query": "fixture", "_max_transport_retries": 0},
                                  settings_obj=self.settings, opener=opener, sleeper=Mock())
            self.assertFalse(result.success)
            self.assertEqual(result.metadata["error_type"], expected)
            self.assertEqual(opener.call_count, 1)
            if status == 429:
                self.assertEqual(result.metadata["retry_after_seconds"], 30)

    def test_cooldown_and_nested_attempts_are_bounded(self):
        from app.agent.tool_recovery import observe_result, unavailable_reason
        state = {}
        with patch("app.agent.tool_recovery.time.time", return_value=100):
            observe_result(state, "tavily_search", {}, ToolResult(success=False,
                metadata={"error_type": "rate_limited", "retry_after_seconds": 30, "retry_count": 1}), 3)
            self.assertEqual(state["tool_call_counts"]["tavily_search"], 2)
            self.assertEqual(unavailable_reason(state, "tavily_search", 3), "cooldown")
        with patch("app.agent.tool_recovery.time.time", return_value=131):
            self.assertIsNone(unavailable_reason(state, "tavily_search", 3))
            observe_result(state, "tavily_search", {}, ToolResult(success=True), 3)
            self.assertEqual(unavailable_reason(state, "tavily_search", 3), "tool_call_limit")

    def test_page_failure_blocks_only_same_input(self):
        from app.agent.tool_recovery import observe_result, unavailable_reason
        state = {}
        args = {"urls": [URL]}
        observe_result(state, "web_fetcher", args, ToolResult(success=False, metadata={"error_type": "auth_error"}), 3)
        self.assertIsNotNone(unavailable_reason(state, "web_fetcher", 3, args))
        self.assertIsNone(unavailable_reason(state, "web_fetcher", 3, {"urls": ["https://example.net/other"]}))

    def test_empty_result_requires_a_different_query(self):
        from app.agent.tool_recovery import observe_result, unavailable_reason
        state = {}
        observe_result(state, "tavily_search", {"query": "one"}, ToolResult(success=True, output={"results": []}), 3)
        self.assertEqual(unavailable_reason(state, "tavily_search", 3, {"query": "one"}), "empty_result")
        self.assertIsNone(unavailable_reason(state, "tavily_search", 3, {"query": "two"}))

    def test_api_preflight_uses_request_permissions_and_source_mode(self):
        from app.api.tasks import get_task_preflight, approve_plan
        from app.schemas import PlanApproveRequest
        from fastapi import HTTPException, BackgroundTasks
        run = store.create_agent_run(self.db, "fixture", "summary", "real", allowed_tools=["tavily_search"])
        plan = self.skill_plan()
        plan["source_mode"] = "mock"  # A persisted/model plan cannot override the request.
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan", None)
        with patch("app.api.tasks.settings", self.settings):
            self.assertFalse(get_task_preflight(run.run_id, self.db).ready)
            with self.assertRaises(HTTPException) as error:
                approve_plan(run.run_id, PlanApproveRequest(approved=True), background_tasks=BackgroundTasks(),
                             start_async=False, db=self.db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(run.status, "waiting_human_plan")
        self.assertEqual(store.list_tool_traces(self.db, run.run_id), [])

    def test_repeated_disabled_selections_never_repeat_api_calls(self):
        _, result, _, execute = self.run_script([decision("mcp_github_search", query="x")] * 12)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["status"], "failed")

    def test_disabled_state_survives_resumed_execution(self):
        plan = self.skill_plan()
        plan["react_state"] = {"tool_recovery": {"mcp_github_search": {"status": "disabled", "reason": "auth_error"}},
                               "tool_call_counts": {"mcp_github_search": 1}, "observation_history": []}
        _, result, _, execute = self.run_script([decision("mcp_github_search", query="x"),
            decision("tavily_search", query="docs"), decision("web_fetcher", urls=[URL]), decision("finish")], plan=plan)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(call.args[0] != "mcp_github_search" for call in execute.call_args_list))

    def test_github_403_quota_cools_down_but_permission_error_blocks_only_input(self):
        from app.agent.tool_recovery import observe_result, unavailable_reason
        for category, expected in [("rate_limited", "cooldown"), ("forbidden", None)]:
            with self.subTest(category=category):
                state = {}
                observe_result(state, "mcp_github_search", {"query": "private repo"}, ToolResult(success=False,
                    metadata={"error_type": category, "http_status": 403, "retry_after_seconds": 30}), 3)
                self.assertEqual(unavailable_reason(state, "mcp_github_search", 3, {"query": "public repo"}), expected)

    def test_optional_disabled_capability_is_hidden_without_blocking_web_research(self):
        _, result, client, execute = self.run_script([decision("mcp_github_search", query="x"),
            decision("tavily_search", query="docs"), decision("web_fetcher", urls=[URL]), decision("finish")],
            settings=self.settings.model_copy(update={"github_public_api_enabled": False}))
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("mcp_github_search", client.payloads[0]["allowed_tools"])
        self.assertTrue(all(call.args[0] != "mcp_github_search" for call in execute.call_args_list))

    def test_default_optional_mcp_tools_exclude_interactive_and_write_channels(self):
        from app.tools.base import ToolSpec
        for channel in ("readonly", "interactive", "write"):
            name = "fixture_" + channel
            registry._tool_specs[name] = ToolSpec(name=name, description="fixture", input_schema={},
                read_only=True, metadata={"tool_source": "mcp_remote", "mcp_channel": channel})
        allowed = self.skill_plan()["allowed_tools"]
        self.assertIn("fixture_readonly", allowed)
        self.assertNotIn("fixture_interactive", allowed)
        self.assertNotIn("fixture_write", allowed)

    def test_guard_rejects_unauthorized_tool_without_counting_an_attempt(self):
        from app.agent.execution_policy import execute_with_policy
        from app.agent.tool_recovery import observe_result
        execute = Mock()
        result = execute_with_policy("web_fetcher", {"urls": [URL]}, {"allowed_tools": []}, self.settings, execute)
        execute.assert_not_called()
        state = {}
        observe_result(state, "web_fetcher", {}, result, 3)
        self.assertEqual(state["tool_call_counts"]["web_fetcher"], 0)

    def test_sequential_and_parallel_reject_fallback_before_evidence(self):
        from app.agent.executor import run_plan
        from app.agent.parallel_executor import run_plan_parallel
        from app.agent.evidence import build_evidence_bundle
        from tests.test_research_integrity import web_plan
        for module, runner in [("executor", run_plan), ("parallel_executor", run_plan_parallel)]:
            with self.subTest(executor=module):
                run = store.create_agent_run(self.db, "fixture", "summary", "real")
                plan = web_plan()
                store.update_agent_run_plan(self.db, run.run_id, plan)
                fake = ToolResult(success=True, output={"results": [{"url": URL, "content": "Untrusted fixture"}]},
                                  metadata={"data_source": "fallback"})
                with patch(f"app.agent.{module}.execute_tool", return_value=fake) as execute:
                    result = runner(self.db, run.run_id, settings_obj=self.settings)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(execute.call_count, 1)
                traces = store.list_tool_traces(self.db, run.run_id)
                self.assertEqual(build_evidence_bundle(run, plan, [], traces).total_evidence_items, 0)
                self.assertFalse(run.report_path)

    def test_parallel_worker_cannot_execute_an_unpermitted_step(self):
        from app.agent.parallel_executor import _execute_step
        with patch("app.agent.parallel_executor.execute_tool") as execute:
            result = _execute_step({"tool_name": "tavily_search", "arguments": {"query": "x"}}, 1,
                                   plan={"allowed_tools": []}, settings_obj=self.settings)
        execute.assert_not_called()
        self.assertFalse(result.result.success)

    def test_retry_discards_recovery_state_but_preserves_permissions_and_source_mode(self):
        from app.api.tasks import retry_task
        run = store.create_agent_run(self.db, "fixture", "summary", "real", allowed_tools=["tavily_search"])
        plan = self.skill_plan(["tavily_search"])
        plan["react_state"] = {"tool_recovery": {"tavily_search": {"status": "disabled"}}}
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_status(self.db, run.run_id, "failed", "fixture")
        with patch("app.api.tasks.settings", self.settings):
            response = retry_task(run.run_id, db=self.db)
        new = store.get_agent_run(self.db, response.run_id)
        new_plan = json.loads(new.plan_json)
        self.assertNotIn("react_state", new_plan)
        self.assertEqual(json.loads(new.allowed_tools_json), ["tavily_search"])
        self.assertEqual(new.source_mode, "real")
        self.assertEqual(new_plan["parent_run_id"], run.run_id)
        self.assertIn("react_state", json.loads(run.plan_json))

    def test_deepening_inherits_even_empty_explicit_permissions(self):
        from app.agent.deepening import _run_single_round
        for allowed in ([], ["tavily_search", "web_fetcher"]):
            with self.subTest(allowed=allowed):
                run = store.create_agent_run(self.db, "fixture", "summary", "real", allowed_tools=allowed)
                store.update_agent_run_plan(self.db, run.run_id, self.skill_plan())
                with patch("app.agent.deepening.run_react_task", return_value={"status": "failed"}) as execute:
                    _run_single_round(self.db, run.run_id, "fixture", ["follow up"], self.settings)
                child = store.get_agent_run(self.db, execute.call_args.args[1])
                self.assertEqual(child.source_mode, "real")
                self.assertEqual(json.loads(child.allowed_tools_json), allowed)
                self.assertEqual(json.loads(child.plan_json)["allowed_tools"], allowed)

    def test_cancelled_run_does_not_resume_recovery(self):
        from app.agent.react_executor import run_react_task
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, self.skill_plan())
        store.update_agent_run_status(self.db, run.run_id, "cancelled", "fixture")
        client = ScriptedLLM([decision("tavily_search", query="x")])
        with patch("app.agent.react_executor.execute_tool") as execute:
            result = run_react_task(self.db, run.run_id, self.settings, client)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(client.payloads, [])
        execute.assert_not_called()

    def test_source_guard_checks_nested_provenance_not_mentions_in_content(self):
        from app.agent.execution_policy import execute_with_policy
        for metadata, expected in [({"is_mock": True}, False), ({"data_source": "web"}, True)]:
            execute = Mock(return_value=ToolResult(success=True, output={"pages": [
                {"url": URL, "content": "Documentation of mock testing", "metadata": metadata}]}))
            result = execute_with_policy("web_fetcher", {"urls": [URL]}, self.skill_plan(), self.settings, execute)
            self.assertEqual(result.success, expected)


if __name__ == "__main__":
    unittest.main()
