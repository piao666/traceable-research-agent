"""Offline R8.3–R8.5 source identity, context and shared-budget regressions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from app.config import Settings
from app.trace import store
from app.trace.logger import record_trace_event
from app.tools.base import ToolResult
from tests import test_r8_recovery as recovery
from tests.test_r8_recovery import decision, URL


class ContextIdentityTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    skill_plan = recovery.RecoveryTests.skill_plan
    run_script = recovery.RecoveryTests.run_script

    def test_search_urls_survive_prompt_compaction_and_fetch_updates_state(self):
        run, result, client, _ = self.run_script([decision("tavily_search", query="docs"),
            decision("web_fetcher", urls=[URL]), decision("finish")])
        context = client.payloads[1]["research_context"]
        self.assertEqual(context["sources"][0]["url"], URL)
        self.assertEqual(context["sources"][0]["fetch_status"], "pending")
        self.assertEqual(client.payloads[2]["research_context"]["sources"][0]["fetch_status"], "fetched")
        self.assertTrue(json.loads(run.plan_json)["react_state"]["source_context"]["sources"])

    def test_context_rebuilds_from_all_traces_not_last_twenty_summaries(self):
        from app.agent.source_context import build_source_context
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "one URL",
                           {"results": [{"url": URL, "title": "Official", "content": "Useful excerpt"}]})
        for step in range(2, 25):
            record_trace_event(self.db, run.run_id, step, "react_decision", "rejected", {}, "diagnostic", {})
        context = build_source_context(store.list_tool_traces(self.db, run.run_id))
        self.assertEqual(context["sources"][0]["url"], URL)
        self.assertTrue(context["sources"][0]["trace_ids"])

    def test_ambiguous_observations_do_not_swap_refetch_trace_identity(self):
        from app.agent.evidence import build_evidence_bundle
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        a = record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "first",
                              {"results": [{"url": URL, "content": "First source"}]})
        b = record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "refetch",
                              {"results": [{"url": "https://example.net/second", "content": "Second source"}]})
        observations = [{"trace_id": b.trace_id, "step_no": 99, "tool_name": "mcp_github_search", "success": True}]
        bundle = build_evidence_bundle(run, {}, observations, [a, b])
        by_id = {item.trace_id: item for item in bundle.evidence_items}
        self.assertEqual(by_id[b.trace_id].tool_name, "tavily_search")
        self.assertEqual(by_id[b.trace_id].step_no, 1)
        self.assertEqual(len(bundle.evidence_items), 2)

    def test_dynamic_trace_cannot_support_unrelated_planned_goal(self):
        from app.agent.evidence import build_evidence_bundle
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        trace = record_trace_event(self.db, run.run_id, 1, "web_fetcher", "success", {}, "page",
                                   {"pages": [{"url": URL, "content": "Documented API behavior."}]})
        plan = {"evidence_mapping_version": "trace-source-v2", "execution_mode": "react", "steps": [
            {"step_no": 1, "tool_name": "mcp_github_search", "goal": "Repository has 100000 stars"}]}
        bundle = build_evidence_bundle(run, plan, [], [trace])
        self.assertTrue(bundle.claims)
        self.assertTrue(all("100000" not in claim.claim for claim in bundle.claims))
        self.assertEqual(bundle.claims[0].claim, "Documented API behavior.")

    def test_parent_bundle_cannot_relabel_a_child_trace_as_parent_evidence(self):
        from app.agent.evidence import build_evidence_bundle
        parent = store.create_agent_run(self.db, "parent", "summary", "real")
        child = store.create_agent_run(self.db, "child", "summary", "real")
        trace = record_trace_event(self.db, child.run_id, 1, "tavily_search", "success", {}, "child",
                                   {"results": [{"url": URL, "content": "Child-only content"}]})
        self.assertEqual(build_evidence_bundle(parent, {}, [], [trace]).total_evidence_items, 0)

    def test_pdf_and_failed_page_states_and_sensitive_url_filter(self):
        from app.agent.source_context import build_source_context
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        record_trace_event(self.db, run.run_id, 1, "pdf_reader", "success", {}, "pdf",
            {"documents": [{"path": URL, "pages": [{"text": "Paper content"}], "content_basis": "partial"}]})
        record_trace_event(self.db, run.run_id, 2, "web_fetcher", "failed", {}, "failure",
            {"pages": [{"url": "https://example.net/blocked", "error": "403"}]})
        record_trace_event(self.db, run.run_id, 3, "tavily_search", "success", {}, "signed",
            {"results": [{"url": "https://example.net/?token=secret", "content": "secret-bearing"}]})
        context = build_source_context(store.list_tool_traces(self.db, run.run_id))
        self.assertEqual([s["fetch_status"] for s in context["sources"]], ["fetched", "failed"])
        self.assertNotIn("secret", str(context))

    def test_report_and_evidence_api_share_authoritative_records(self):
        from app.agent.evidence import _evidence_records
        from app.agent.reporter import _evidence_records as report_records
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        trace = record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "canonical",
            {"results": [{"url": URL, "content": "Canonical content"}]})
        stale = [{"step_no": 1, "tool_name": "tavily_search", "success": True, "output": {"content": "wrong"}}]
        self.assertEqual(_evidence_records(stale, [trace]), report_records(stale, [trace]))

    def test_identical_source_outputs_keep_distinct_trace_snapshots(self):
        from app.agent.evidence import build_evidence_bundle
        from app.evidence.service import materialize_provenance_bundle
        from app.evidence.artifact_store import ArtifactStore
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        traces = [record_trace_event(self.db, run.run_id, 1, "web_fetcher", "success", {}, "same",
            {"pages": [{"url": URL, "content": "Same source on two actual calls."}]}) for _ in range(2)]
        plan = {"evidence_mapping_version": "trace-source-v2"}
        with tempfile.TemporaryDirectory() as temporary:
            bundle = build_evidence_bundle(run, plan, [], traces)
            payload = materialize_provenance_bundle(self.db, run, bundle, traces, ArtifactStore(Path(temporary)), extractor_version="r8-test")
            self.assertEqual(len(payload["source_snapshots"]), 2)
            snapshots = {s["snapshot_id"]: s for s in payload["source_snapshots"]}
            for passage in payload["passages"]:
                self.assertEqual(passage["trace_id"], snapshots[passage["snapshot_id"]]["trace_id"])

    def test_queue_is_bounded_and_prompt_preserves_host_diversity(self):
        from app.agent.source_context import build_source_context, prompt_source_context
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        trace = record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "many",
            {"results": [{"url": f"https://example.org/{n}", "content": "x" * 10000} for n in range(10)] +
                        [{"url": "https://example.net/paper", "content": "Paper"}]})
        context = build_source_context([trace], max_sources=12)
        prompt = prompt_source_context(context, limit=2)
        self.assertEqual(len(prompt["sources"]), 2)
        self.assertIn("example.net", prompt["sources"][1]["url"])
        self.assertLessEqual(len(prompt["sources"][0]["snippet"]), 600)
        self.assertEqual(len(build_source_context([trace], max_sources=2)["sources"]), 2)

    def test_deepening_prompt_preserves_urls_and_child_run_identity(self):
        from app.agent.deepening import _build_deepening_messages
        messages = _build_deepening_messages("fixture", [{"trace_id": "trace-child", "run_id": "child",
            "tool_name": "tavily_search", "success": True,
            "output": {"results": [{"url": URL, "content": "Child source"}]}}], [], 1)
        self.assertIn(URL, messages[-1].content)
        self.assertIn("trace-child", messages[-1].content)
        self.assertIn("child", messages[-1].content)

    def test_legacy_react_mapping_is_flagged_without_rewriting_history(self):
        from app.agent.outcome import result_integrity, trusted_run_ids
        run = store.create_agent_run(self.db, "legacy", "summary", "real")
        plan = {"execution_mode": "react", "steps": [{"step_no": 1, "tool_name": "web_fetcher"}],
            "research_outcome": {"version": "research-integrity-v1", "status": "passed", "effective_evidence_count": 2}}
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_status(self.db, run.run_id, "completed", None)
        before = run.plan_json
        self.assertTrue(result_integrity(run)["requires_review"])
        self.assertNotIn(run.run_id, self.db.scalars(trusted_run_ids()).all())
        self.assertEqual(run.plan_json, before)

    def test_real_mode_fixture_pipeline_generates_actual_report_with_resolvable_citations(self):
        from app.agent.react_executor import run_react_task
        from app.evidence.service import get_provenance_bundle
        from app.llm.base import LLMResponse

        class ContextDrivenLLM(recovery.ScriptedLLM):
            def complete(self, messages, **kwargs):
                payload = json.loads(messages[-1].content)
                self.payloads.append(payload)
                action = next(self.actions)
                if action["action"] == "web_fetcher":
                    action["args"]["urls"] = [payload["research_context"]["sources"][0]["url"]]
                return LLMResponse(success=True, content=json.dumps(action), provider="fixture")

        def handler(name, args):
            if name == "mcp_github_search":
                return ToolResult(success=False, error_message="HTTP 401", metadata={"error_type": "auth_error"})
            if name == "tavily_search":
                return ToolResult(success=True, output={"results": [{"url": URL, "title": "Official docs", "content": "Graph execution docs"}]})
            self.assertEqual(args["urls"], [URL])
            return ToolResult(success=True, output={"pages": [{"url": URL, "title": "Official docs",
                "content": "The framework supports deterministic graph execution.", "content_basis": "full_text"}]})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self.settings.model_copy(update={"evidence_pipeline_version": "v2",
                "evidence_artifact_root": str(root / "artifacts"), "reference_verification_enabled": False})
            run = store.create_agent_run(self.db, "Compare graph frameworks", "summary", "real")
            store.update_agent_run_plan(self.db, run.run_id, self.skill_plan())
            client = ContextDrivenLLM([decision("tavily_search", query="official"), decision("mcp_github_search", query="repos"),
                                      decision("web_fetcher"), decision("finish")])
            with (patch("app.agent.react_executor.execute_tool", side_effect=handler),
                  patch("app.agent.executor._after_run_completed"),
                  patch("app.config.settings", settings),
                  patch("app.agent.reporter.ROOT", root), patch("app.agent.reporter.REPORTS_ROOT", root / "reports")):
                result = run_react_task(self.db, run.run_id, settings, client)
            self.assertEqual(result["status"], "completed", result)
            # R8.6: actual executor output must satisfy the page's typed API,
            # including recovery explanations, source identities and live budget.
            import asyncio
            from app.api.tasks import get_task_plan
            view = asyncio.run(get_task_plan(run.run_id, self.db))
            self.assertEqual(view.execution_budget.tool_calls, 3)
            self.assertEqual(view.execution_insights.source_context.gaps.fetched, 1)
            self.assertEqual(next(t for t in view.execution_insights.tools if t.name == "mcp_github_search").status, "disabled")
            markdown = (root / run.report_path).read_text()
            provenance = get_provenance_bundle(self.db, run.run_id)
            passages = {p["passage_id"]: p for p in provenance["passages"]}
            self.assertTrue(provenance["citations"])
            for citation in provenance["citations"]:
                self.assertIn(citation["citation_label"], markdown)
                self.assertIn(citation["passage_id"], passages)
                self.assertIn(passages[citation["passage_id"]]["trace_id"], {t.trace_id for t in store.list_tool_traces(self.db, run.run_id)})
            self.assertIn("deterministic graph execution", markdown)
            self.assertTrue(all(c["origin"] == "source_excerpt" for c in provenance["report_claims"] if "未取得" not in c["claim_text"]))


class SharedBudgetTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    skill_plan = recovery.RecoveryTests.skill_plan
    run_script = recovery.RecoveryTests.run_script

    def runtime(self, **changes):
        from app.agent.budget import BudgetRuntime
        run = store.create_agent_run(self.db, "budget fixture", "summary", "real")
        return BudgetRuntime(self.db, run.run_id, self.settings.model_copy(update=changes))

    def test_tool_exhaustion_stops_before_another_external_call(self):
        run, result, _, execute = self.run_script([decision("tavily_search", query="docs"),
            decision("web_fetcher", urls=[URL])], settings=self.settings.model_copy(update={"research_max_tool_calls": 1}))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(json.loads(run.plan_json)["research_outcome"]["error_code"], "budget_exhausted")
        self.assertFalse(run.report_path)

    def test_budget_failure_preserves_http_response_contract(self):
        from app.api.tasks import _task_run_response
        run, result, _, execute = self.run_script([decision("tavily_search", query="docs")],
            settings=self.settings.model_copy(update={"research_max_tokens": 1}))
        execute.assert_not_called()
        response = _task_run_response(result)
        self.assertEqual(response.status, "failed")
        self.assertEqual(response.current_step, 0)
        self.assertIn(run.run_id, response.trace_url)
        self.assertEqual(response.research_outcome["error_code"], "budget_exhausted")

    def test_children_share_parent_budget_and_settings_snapshot(self):
        from app.agent.budget import BudgetRuntime, BudgetExceeded, ensure_budget
        parent = self.runtime(research_max_tool_calls=2)
        parent.tool("web_fetcher")
        child = store.create_agent_run(self.db, "child", "summary", "real")
        ensure_budget(self.db, child.run_id, self.settings, parent_run_id=parent.run_id)
        runtime = BudgetRuntime(self.db, child.run_id, self.settings.model_copy(update={"research_max_tool_calls": 999}))
        runtime.tool("tavily_search")
        with self.assertRaises(BudgetExceeded):
            runtime.tool("tavily_search")
        self.assertEqual(parent.snapshot()["tool_calls"], 2)
        self.assertEqual(runtime.snapshot()["root_run_id"], parent.run_id)
        self.assertEqual(runtime.limits["max_tool_calls"], 2)

    def test_llm_budget_counts_unknown_usage_and_blocks_next_call(self):
        from app.agent.budget import BudgetClient, BudgetExceeded, _active
        from app.llm.base import LLMMessage, LLMResponse
        runtime = self.runtime(research_max_llm_calls=1)
        client = Mock()
        client.complete.return_value = LLMResponse(success=True, provider="fixture", content="done")
        token = _active.set(runtime)
        try:
            wrapped = BudgetClient(client)
            wrapped.complete([LLMMessage(role="user", content="question")], max_tokens=20)
            self.assertGreater(runtime.snapshot()["accounted_tokens"], 20)
            with self.assertRaises(BudgetExceeded):
                wrapped.complete([LLMMessage(role="user", content="question")])
            self.assertEqual(client.complete.call_count, 1)
        finally:
            _active.reset(token)

    def test_deadline_and_unknown_price_block_admission(self):
        from app.agent.budget import BudgetExceeded
        runtime = self.runtime(research_max_seconds=1)
        with patch("app.agent.budget.time.time", return_value=runtime.snapshot()["deadline"] + 1):
            with self.assertRaises(BudgetExceeded):
                runtime.tool("tavily_search")
        self.assertEqual(runtime.snapshot()["tool_calls"], 0)
        runtime = self.runtime(research_max_estimated_cost=1)
        with self.assertRaises(BudgetExceeded) as error:
            runtime.tool("tavily_search")
        self.assertEqual(error.exception.reason, "tool_price_unconfigured")

    def test_estimated_cost_cap_and_token_cap_are_checked_before_call(self):
        from app.agent.budget import BudgetExceeded
        runtime = self.runtime(research_max_estimated_cost=1, research_tool_cost_estimate=0.6)
        runtime.tool("tavily_search")
        with self.assertRaises(BudgetExceeded):
            runtime.tool("web_fetcher")
        self.assertAlmostEqual(runtime.snapshot()["estimated_cost"], 0.6)
        runtime = self.runtime(research_max_tokens=5)
        with self.assertRaises(BudgetExceeded):
            runtime.reserve(llm=1, tokens=6)
        self.assertEqual(runtime.snapshot()["llm_calls"], 0)

    def test_parent_cancel_blocks_child_and_fresh_retry_gets_new_budget(self):
        from app.agent.budget import BudgetRuntime, BudgetExceeded, ensure_budget
        from app.api.tasks import retry_task
        parent = self.runtime()
        store.update_agent_run_plan(self.db, parent.run_id, self.skill_plan())
        parent.tool("tavily_search")
        child = store.create_agent_run(self.db, "child", "summary", "real")
        ensure_budget(self.db, child.run_id, self.settings, parent_run_id=parent.run_id)
        store.update_agent_run_status(self.db, parent.run_id, "cancelled", "fixture")
        with self.assertRaises(BudgetExceeded) as error:
            BudgetRuntime(self.db, child.run_id, self.settings).tool("web_fetcher")
        self.assertEqual(error.exception.reason, "parent_cancelled")
        with patch("app.api.tasks.settings", self.settings):
            retried = retry_task(parent.run_id, db=self.db)
        fresh = BudgetRuntime(self.db, retried.run_id, self.settings)
        self.assertEqual(fresh.snapshot()["tool_calls"], 0)
        self.assertNotEqual(fresh.root_id, parent.root_id)

    def test_concurrent_sessions_cannot_overbook_and_restart_keeps_ledger(self):
        from concurrent.futures import ThreadPoolExecutor
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.database import Base
        from app.agent.budget import BudgetRuntime, BudgetExceeded
        with tempfile.TemporaryDirectory() as temporary:
            engine = create_engine(f"sqlite:///{temporary}/budget.sqlite", connect_args={"timeout": 10})
            Base.metadata.create_all(engine)
            config = self.settings.model_copy(update={"research_max_tool_calls": 3})
            with Session(engine) as db:
                run_id = store.create_agent_run(db, "atomic", "summary", "real").run_id
                BudgetRuntime(db, run_id, config)
            def attempt(_):
                with Session(engine) as db:
                    try:
                        BudgetRuntime(db, run_id, config).tool("tavily_search")
                        return 1
                    except BudgetExceeded:
                        return 0
            with ThreadPoolExecutor(max_workers=4) as executor:
                self.assertEqual(sum(executor.map(attempt, range(8))), 3)
            engine.dispose()
            engine = create_engine(f"sqlite:///{temporary}/budget.sqlite")
            with Session(engine) as db:
                snapshot = BudgetRuntime(db, run_id, config).snapshot()
                self.assertEqual(snapshot["tool_calls"], 3)
                self.assertEqual(snapshot["stop_reason"], "tool_calls")
            engine.dispose()

    def test_parallel_executor_admits_only_remaining_calls(self):
        from app.agent.parallel_executor import run_plan_parallel
        run = store.create_agent_run(self.db, "parallel", "summary", "real")
        plan = {"source_mode": "real", "allowed_tools": ["tavily_search"], "steps": [
            {"step_no": n, "tool_name": "tavily_search", "arguments": {"query": str(n)}} for n in (1, 2, 3)]}
        store.update_agent_run_plan(self.db, run.run_id, plan)
        with patch("app.agent.parallel_executor.execute_tool", return_value=ToolResult(success=True, output={"results": []})) as execute:
            result = run_plan_parallel(self.db, run.run_id, settings_obj=self.settings.model_copy(update={"research_max_tool_calls": 2}))
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(json.loads(run.plan_json)["execution_budget"]["tool_calls"], 2)

    def test_report_client_cannot_obtain_a_separate_llm_budget(self):
        from app.agent.budget import _active, BudgetExceeded
        from app.agent.report_generation import resolve_report_llm_client
        from app.llm.base import LLMMessage
        runtime = self.runtime(research_max_llm_calls=1)
        runtime.reserve(llm=1)
        client = Mock()
        token = _active.set(runtime)
        try:
            report_client = resolve_report_llm_client(self.settings.model_copy(update={"report_generation_mode": "llm"}), client)
            with self.assertRaises(BudgetExceeded):
                report_client.complete([LLMMessage(role="user", content="report")])
            client.complete.assert_not_called()
        finally:
            _active.reset(token)

    def test_known_usage_reconciles_reservation(self):
        from app.agent.budget import _active, BudgetClient
        from app.llm.base import LLMMessage, LLMResponse, LLMUsage
        runtime = self.runtime()
        client = Mock()
        client.complete.return_value = LLMResponse(success=True, provider="fixture", content="ok",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        token = _active.set(runtime)
        try:
            BudgetClient(client).complete([LLMMessage(role="user", content="hello")], max_tokens=100)
            self.assertEqual(runtime.snapshot()["accounted_tokens"], 15)
        finally:
            _active.reset(token)

    def test_exhausted_budget_does_not_create_more_deepening_children(self):
        from app.agent.budget import _active, BudgetExceeded
        from app.agent.deepening import _run_single_round
        runtime = self.runtime(research_max_tool_calls=1)
        store.update_agent_run_plan(self.db, runtime.run_id, self.skill_plan())
        runtime.tool("tavily_search")
        token = _active.set(runtime)
        try:
            with patch("app.agent.deepening.store.create_agent_run") as create:
                with self.assertRaises(BudgetExceeded):
                    _run_single_round(self.db, runtime.run_id, "fixture", ["next"], self.settings)
            create.assert_not_called()
        finally:
            _active.reset(token)

    def test_budget_inspection_does_not_initialize_historical_run(self):
        from app.agent.budget import budget_snapshot
        from app.trace.models import RunBudget
        run = store.create_agent_run(self.db, "history", "summary", "real")
        store.update_agent_run_status(self.db, run.run_id, "completed", None)
        self.assertIsNone(budget_snapshot(self.db, run.run_id))
        self.assertIsNone(self.db.get(RunBudget, run.run_id))


if __name__ == "__main__":
    unittest.main()
