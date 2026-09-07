"""Offline regressions for goal failure, child plans and source recovery."""
import asyncio
import json
from datetime import datetime
from unittest.mock import patch
import unittest

from app.agent.budget import BudgetExceeded
from app.trace import store
from app.trace.logger import record_trace_event
from tests import test_r8_recovery as recovery
from tests.test_r8_recovery import decision, URL


class GoalRecoveryTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    skill_plan = recovery.RecoveryTests.skill_plan
    run_script = recovery.RecoveryTests.run_script

    def test_unavailable_finish_cannot_be_completed_with_nonempty_evidence(self):
        stop = decision("finish", summary="Task cannot be completed with current capabilities.")
        stop["finish_reason"] = "tool_unavailable"
        run, result, _, _ = self.run_script([
            decision("tavily_search", query="data"), decision("web_fetcher", urls=[URL]), stop])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(run.report_path)
        self.assertEqual(json.loads(run.plan_json)["research_outcome"]["error_code"], "goal_not_met")

    def test_empty_dataset_summary_cannot_override_completion_status(self):
        run, result, _, _ = self.run_script([decision("web_fetcher", urls=[URL]),
            decision("finish", summary="No suitable dataset was found.", goal_status="achieved")])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(run.report_path)

    def test_child_plan_missing_steps_is_readable_without_rewriting(self):
        from app.api.tasks import get_task_plan
        run = store.create_agent_run(self.db, "child fixture", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, {
            "version": "deepening-v1", "parent_run_id": "parent", "task": run.task,
            "source_mode": "real", "allowed_tools": [], "notes": [], "execution_mode": "react"})
        before = run.plan_json
        view = asyncio.run(get_task_plan(run.run_id, self.db))
        self.assertEqual(view.steps, [])
        self.assertEqual(run.plan_json, before)

    def test_budget_exception_is_not_swallowed_by_reporter(self):
        from app.agent.reporter import _llm_synthesize_answer
        from unittest.mock import Mock
        client = Mock()
        client.is_available.return_value = True
        client.complete.side_effect = BudgetExceeded("tokens")
        with self.assertRaises(BudgetExceeded):
            _llm_synthesize_answer("fixture", [{"tool_name": "file_reader", "success": True,
                "output": {"content": "Substantive source evidence."}}], client)

    def test_relative_period_is_anchored_to_creation_not_model_memory(self):
        from app.agent.research_goal import build_task_contract
        contract = build_task_contract("某股票近10年的涨幅变化数据", datetime(2026, 9, 3))
        self.assertEqual(contract["period"], {"start": "2016-09-03", "end": "2026-09-03"})
        self.assertEqual(contract["goal_kind"], "price_series")
        self.assertIn("adjustment", contract["unresolved_fields"])

    def test_template_shell_is_not_effective_web_evidence(self):
        from app.agent.evidence import build_evidence_bundle
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        trace = record_trace_event(self.db, run.run_id, 1, "web_fetcher", "success", {}, "page", {
            "pages": [{"url": URL, "content": "行情 {{name}} {{price}} {{date}} 正在加载中 暂无相关数据",
                       "content_basis": "full_text"}]})
        self.assertEqual(build_evidence_bundle(run, {}, [], [trace]).total_evidence_items, 0)

    def test_source_context_keeps_discovery_excerpt_and_read_reference(self):
        from app.agent.source_context import build_source_context
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        record_trace_event(self.db, run.run_id, 1, "tavily_search", "success", {}, "search",
            {"results": [{"url": URL, "content": "Useful specific discovery excerpt."}]})
        record_trace_event(self.db, run.run_id, 2, "web_fetcher", "success", {}, "page",
            {"pages": [{"url": URL, "content": "Menu " * 200 + "Relevant ending."}]})
        source = build_source_context(store.list_tool_traces(self.db, run.run_id))["sources"][0]
        self.assertEqual(source["search_snippet"], "Useful specific discovery excerpt.")
        self.assertGreater(source["content_length"], 1000)

    def test_citation_sentence_preserves_decimal_and_url(self):
        from app.evidence.citation_validator import _find_citation_sentence
        sentence = "增长为 12.50%，详见 https://example.org/data.csv [CIT-001-01]。"
        self.assertEqual(_find_citation_sentence(sentence, sentence.index("CIT-")), sentence)

    def test_snapshot_read_uses_registry_without_network_or_new_evidence(self):
        from app.agent.budget import BudgetRuntime, _active
        from app.agent.execution_policy import execute_with_policy
        from app.agent.source_context import build_source_context
        from app.agent.evidence import build_evidence_bundle
        from app.tools.registry import execute_tool
        run = store.create_agent_run(self.db, "fixture", "summary", "real", allowed_tools=["web_fetcher"])
        origin = record_trace_event(self.db, run.run_id, 1, "web_fetcher", "success", {}, "page",
            {"pages": [{"url": URL, "content": "Heading. " + "Substantive text " * 300 + "ENDING"}]})
        source = build_source_context([origin])["sources"][0]
        runtime = BudgetRuntime(self.db, run.run_id, self.settings)
        token = _active.set(runtime)
        try:
            with patch("app.tools.web_fetcher.httpx.Client") as network:
                result = execute_with_policy("web_fetcher", {"source_id": source["source_id"], "offset": 4700},
                    {"allowed_tools": ["web_fetcher"], "source_mode": "real"}, self.settings, execute_tool)
                network.assert_not_called()
            self.assertTrue(result.success, result)
            self.assertIn("ENDING", result.output["source_content"]["text"])
            self.assertEqual(result.output["source_content"]["origin_trace_id"], origin.trace_id)
            reread = record_trace_event(self.db, run.run_id, 2, "web_fetcher", "success", {}, "reread", result.output)
            self.assertEqual(build_evidence_bundle(run, {}, [], [origin, reread]).total_evidence_items, 1)
            self.assertEqual(runtime.snapshot()["tool_calls"], 1)
        finally:
            _active.reset(token)

    def test_snapshot_cannot_read_another_run_or_expand_permissions(self):
        from app.agent.budget import BudgetRuntime, _active
        from app.agent.execution_policy import execute_with_policy
        from app.agent.source_context import build_source_context
        from app.tools.registry import execute_tool
        first = store.create_agent_run(self.db, "first", "summary", "real")
        trace = record_trace_event(self.db, first.run_id, 1, "web_fetcher", "success", {}, "page",
                                   {"pages": [{"url": URL, "content": "Private run-specific text."}]})
        source_id = build_source_context([trace])["sources"][0]["source_id"]
        second = store.create_agent_run(self.db, "second", "summary", "real")
        token = _active.set(BudgetRuntime(self.db, second.run_id, self.settings))
        try:
            args = {"source_id": source_id, "_source_snapshot": {"text": "Injected"}}
            result = execute_with_policy("web_fetcher", args, {"allowed_tools": ["web_fetcher"], "source_mode": "real"}, self.settings, execute_tool)
            self.assertFalse(result.success)
            blocked = execute_with_policy("web_fetcher", args, {"allowed_tools": [], "source_mode": "real"}, self.settings, execute_tool)
            self.assertFalse(blocked.success)
            self.assertNotIn("Private", str(result))
        finally:
            _active.reset(token)

    def test_repeat_fetch_and_invented_file_path_are_rejected_without_confirmation(self):
        from app.agent.tool_recovery import unavailable_reason
        state = {"source_context": {"sources": [{"url": URL, "source_id": "S123abc", "fetch_status": "fetched"}]}}
        self.assertEqual(unavailable_reason(state, "web_fetcher", 5, {"urls": [URL], "max_chars": 50000}), "already_fetched_use_source_id")
        self.assertEqual(unavailable_reason(state, "file_reader", 5, {"path": "/workspace/docs/S123abc.html"}), "source_id_is_not_a_file_use_web_fetcher")
        self.assertIsNone(unavailable_reason(state, "web_fetcher", 5, {"source_id": "S123abc"}))

    def test_mixed_fetch_prunes_completed_and_duplicate_urls_but_keeps_new_page(self):
        second = "https://example.net/new-source"
        calls = []

        def handler(name, args):
            self.assertEqual(name, "web_fetcher")
            calls.append(list(args["urls"]))
            return recovery.ToolResult(success=True, output={"pages": [{"url": url,
                "content": "Substantive current-run source content for " + url} for url in args["urls"]]})

        _, result, _, _ = self.run_script([
            decision("web_fetcher", urls=[URL]),
            decision("web_fetcher", urls=[URL, second, second]),
            decision("finish", goal_status="achieved")], handler=handler)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, [[URL], [second]])

    def test_all_completed_fetch_is_rejected_without_spending_an_attempt(self):
        _, result, _, execute = self.run_script([
            decision("web_fetcher", urls=[URL]),
            decision("web_fetcher", urls=[URL]),
            decision("finish", goal_status="achieved")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(execute.call_count, 1)

    def test_unread_candidates_survive_static_tier_selection(self):
        from app.agent.source_governance import govern_tool_result
        from app.tools.base import ToolResult
        from tests.test_source_governance import _plan
        plan = _plan("generic")
        old = [f"https://old{n}.example.org/data" for n in range(3)]
        fresh = "https://fresh.example.net/data"
        plan["react_state"] = {"source_context": {"sources": [{"url": url, "fetch_status": "fetched"} for url in old]}}
        output = ToolResult(success=True, output={"results": [{"url": url, "content": "Specific data"} for url in [*old, fresh]]})
        result = govern_tool_result("tavily_search", output, plan, self.settings)
        self.assertIn(fresh, [row["url"] for row in result.output["results"]])
        self.assertEqual(len(result.output["discovery_candidates"]), 4)

    def test_ambiguous_price_request_is_blocked_before_any_tool(self):
        from app.agent.preflight import check_plan_readiness
        run = store.create_agent_run(self.db, "某股票近10年的涨幅变化数据", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, self.skill_plan())
        checked = check_plan_readiness(json.loads(run.plan_json), self.settings, llm_available=True)
        self.assertFalse(checked["ready"])
        self.assertTrue(any(item["code"] == "task_requirements_unresolved" for item in checked["blockers"]))

    def test_real_text_without_dated_rows_cannot_satisfy_data_goal(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        contract = build_task_contract("某股票 2020-01-01 至 2021-01-01 年度不复权收盘价")
        self.assertEqual(contract["unresolved_fields"], [])
        obs = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [{"url": URL, "content": "Company overview and financial ratios"}]}}]
        self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")

    def test_actual_csv_rows_pass_but_truncation_wrong_dates_and_wrong_metric_do_not(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("某股票 2020-01-01 至 2021-01-01 年度不复权收盘价")
        tables = csv_tables("date,unadjusted_close\n2020-01-01,10\n2021-01-01,12\n")
        obs = [{"tool_name": "file_reader", "success": True, "output": {"tables": tables}}]
        self.assertIsNone(structured_goal_failure(contract, obs))
        tables[0]["truncated"] = True
        self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")
        tables[0]["truncated"] = False
        tables[0]["columns"][1] = "financial_ratio"
        self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")
        tables[0]["columns"][1] = "unadjusted_close"
        tables[0]["rows"] = [["2014-01-01", 10], ["2015-01-01", 12]]
        self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")

    def test_html_table_extraction_keeps_rows_not_template_numbers(self):
        from app.tools.structured_tables import html_tables
        table = html_tables("<table><tr><th>date</th><th>close</th></tr><tr><td>2020-01-01</td><td>10</td></tr></table>")[0]
        self.assertEqual(table["columns"], ["date", "close"])
        self.assertEqual(table["rows"], [["2020-01-01", "10"]])

    def test_new_integrity_version_flags_old_false_success_without_rewrite(self):
        from app.agent.outcome import result_integrity, trusted_run_ids
        run = store.create_agent_run(self.db, "legacy", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, {"research_outcome": {
            "version": "research-integrity-v1", "status": "passed", "effective_evidence_count": 12}})
        store.update_agent_run_status(self.db, run.run_id, "completed", None)
        before = run.plan_json
        self.assertTrue(result_integrity(run)["requires_review"])
        self.assertNotIn(run.run_id, self.db.scalars(trusted_run_ids()).all())
        self.assertEqual(run.plan_json, before)

    def test_model_cannot_replace_application_task_requirements(self):
        run = store.create_agent_run(self.db, "某股票近10年的涨幅数据", "summary", "real")
        plan = {**self.skill_plan(), "task_contract": {"goal_kind": "research", "as_of": "2000-01-01"}}
        store.update_agent_run_plan(self.db, run.run_id, plan)
        actual = json.loads(run.plan_json)["task_contract"]
        self.assertEqual(actual["goal_kind"], "price_series")
        self.assertNotEqual(actual["as_of"], "2000-01-01")

    def test_price_evidence_cannot_borrow_identifier_from_another_page(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("股票 123456.SZ 2020-01-01 至 2021-01-01 年度不复权收盘价")
        tables = csv_tables("date,unadjusted_close\n2020-01-01,10\n2021-01-01,12\n")
        obs = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [
            {"url": URL, "content": "123456.SZ has no price data"},
            {"url": "https://example.net/another", "content": "Another instrument", "tables": tables}]}}]
        self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")

    def test_named_stock_requires_same_page_identity_and_accepts_common_return_columns(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("我需要平安银行股票 2020-01-01 至 2021-01-01 年度不复权涨幅")
        self.assertEqual(contract["instrument_labels"], ["平安银行"])
        tables = csv_tables("日期,不复权涨跌幅(%)\n2020/01/01,1.2\n2021/01/01,3.4\n")
        wrong = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [{
            "url": URL, "content": "另一家银行历史行情", "tables": tables}]}}]
        self.assertEqual(structured_goal_failure(contract, wrong), "structured_data_unavailable")
        correct = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [{
            "url": URL, "content": "平安银行历史行情", "tables": tables}]}}]
        self.assertIsNone(structured_goal_failure(contract, correct))

    def test_stock_code_next_to_chinese_is_authoritative_over_the_name(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("平安银行000001.SZ股票 2020-01-01 至 2021-01-01 年度不复权收盘价")
        self.assertEqual(contract["instrument_codes"], ["000001.SZ"])
        tables = csv_tables("日期,不复权收盘价\n2020-01-01,10\n2021-01-01,12\n")
        wrong = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [{
            "url": URL, "content": "平安银行简介，表格代码为 999999", "tables": tables}]}}]
        self.assertEqual(structured_goal_failure(contract, wrong), "structured_data_unavailable")
        correct = [{"tool_name": "web_fetcher", "success": True, "output": {"pages": [{
            "url": URL, "content": "证券代码 000001 历史行情", "tables": tables}]}}]
        self.assertIsNone(structured_goal_failure(contract, correct))

    def test_documentation_explaining_templates_is_not_an_empty_shell(self):
        from app.tools.web_content_cleaner import page_content_issue
        self.assertIsNone(page_content_issue("This tutorial explains template interpolation. Use {{name}} and {{date}} to bind these values to the model. The renderer updates them when state changes."))

    def test_provider_review_is_not_forced_into_price_data_requirements(self):
        from app.agent.research_goal import build_task_contract
        self.assertEqual(build_task_contract("比较股票历史数据 API 的功能与安全性")["goal_kind"], "research")

    def test_invalid_finish_goal_payload_fails_closed(self):
        from app.agent.research_goal import finish_failure
        self.assertEqual(finish_failure("completed", "Completed", {"achieved": True}), "goal_not_met")

    def test_price_returns_do_not_accept_prices_or_unsupported_adjustment(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("某股票 2020-01-01 至 2021-01-01 年度不复权涨幅")
        for header in ("close", "unadjusted_close", "forward_adjusted_return"):
            with self.subTest(header=header):
                obs = [{"tool_name": "file_reader", "success": True, "output": {
                    "tables": csv_tables(f"date,{header}\n2020-01-01,10\n2021-01-01,12\n")}}]
                self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")

    def test_report_budget_reserve_is_only_available_for_final_root_report(self):
        from app.agent.budget import BudgetRuntime, _active, report_budget
        run = store.create_agent_run(self.db, "budget", "summary", "real")
        runtime = BudgetRuntime(self.db, run.run_id, self.settings.model_copy(update={"research_max_tokens": 1000}))
        token = _active.set(runtime)
        try:
            runtime.reserve(llm=1, tokens=850)
            self.assertFalse(runtime.can_deepen())
            @report_budget
            def report(run, plan):
                runtime.reserve(llm=1, tokens=100)
                return "final"
            self.assertEqual(report(run, {}), "final")
            self.assertEqual(runtime.snapshot()["accounted_tokens"], 950)
        finally:
            _active.reset(token)

    def test_metric_cannot_borrow_other_columns_basis_or_outside_period_rows(self):
        from app.agent.research_goal import build_task_contract, structured_goal_failure
        from app.tools.structured_tables import csv_tables
        contract = build_task_contract("某股票 2020-01-01 至 2021-01-01 年度不复权收盘价")
        for text in (
            "date,unadjusted_open,forward_adjusted_close\n2020-01-01,10,11\n2021-01-01,12,13\n",
            "date,unadjusted_close\n2019-01-01,10\n2020-06-01,11\n2020-07-01,12\n2022-01-01,13\n",
        ):
            obs = [{"tool_name": "file_reader", "success": True, "output": {"tables": csv_tables(text)}}]
            self.assertEqual(structured_goal_failure(contract, obs), "structured_data_unavailable")

    def test_dynamic_upgrade_gets_separate_steps_and_keeps_monotonic_trace_numbers(self):
        plan = self.skill_plan()
        plan["adaptive_upgrade"] = True
        run = store.create_agent_run(self.db, "fixture", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_progress(self.db, run.run_id, 3)
        from app.agent.react_executor import run_react_task
        from app.tools.base import ToolResult
        with (patch("app.agent.react_executor.execute_tool", return_value=ToolResult(success=True, output={
                "pages": [{"url": URL, "content": "Substantive documentation."}]})),
              patch("app.agent.react_executor.generate_markdown_report", return_value="# Fixture"),
              patch("app.agent.react_executor.save_report", return_value="not-written.md")):
            result = run_react_task(self.db, run.run_id, self.settings.model_copy(update={"react_max_steps": 2}),
                recovery.ScriptedLLM([decision("web_fetcher", urls=[URL]), decision("finish")]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["current_step"], 5)
        self.assertEqual(result["total_steps"], 5)

    def test_full_retry_can_use_its_own_finalization_reserve(self):
        from app.agent.budget import BudgetRuntime, _active, report_budget
        run = store.create_agent_run(self.db, "retry", "summary", "real")
        runtime = BudgetRuntime(self.db, run.run_id, self.settings.model_copy(update={"research_max_tokens": 1000}))
        token = _active.set(runtime)
        try:
            runtime.reserve(llm=1, tokens=850)
            @report_budget
            def report(run, plan):
                runtime.reserve(llm=1, tokens=100)
                return "final"
            self.assertEqual(report(run, {"parent_run_id": "previous-failed-run"}), "final")
            self.assertEqual(runtime.snapshot()["accounted_tokens"], 950)
        finally:
            _active.reset(token)

    def test_child_cannot_spend_reserved_root_report_tokens(self):
        from app.agent.budget import BudgetRuntime, _active, ensure_budget, report_budget
        root = store.create_agent_run(self.db, "root", "summary", "real")
        child = store.create_agent_run(self.db, "child", "summary", "real")
        settings = self.settings.model_copy(update={"research_max_tokens": 1000})
        ensure_budget(self.db, child.run_id, settings, parent_run_id=root.run_id)
        runtime = BudgetRuntime(self.db, child.run_id, settings)
        token = _active.set(runtime)
        try:
            runtime.reserve(llm=1, tokens=850)
            @report_budget
            def report(run, plan):
                runtime.reserve(llm=1, tokens=100)
            with self.assertRaises(BudgetExceeded) as stopped:
                report(child, {})  # Removing the model-visible parent cannot grant headroom.
            self.assertEqual(stopped.exception.reason, "finalization_reserve")
            self.assertEqual(runtime.snapshot()["accounted_tokens"], 850)
        finally:
            _active.reset(token)

    def test_child_budget_stops_parent_and_preserves_exportable_child_link(self):
        from app.agent.deepening import run_deepening
        from app.agent.budget import current_budget
        from app.llm.base import LLMResponse
        from app.tools.base import ToolResult
        run = store.create_agent_run(self.db, "Research a documented feature", "summary", "real")
        store.update_agent_run_plan(self.db, run.run_id, self.skill_plan())
        parent_id = run.run_id

        class FamilyLLM(recovery.ScriptedLLM):
            def complete(inner, messages, **kwargs):
                if "research director" in messages[0].content:
                    return LLMResponse(success=True, provider="fixture", content=json.dumps({
                        "learnings": ["Initial source available"], "follow_up_queries": ["Follow-up one", "Must not create two"]}))
                if current_budget().run_id != parent_id:
                    current_budget().stop("tokens")
                return super(FamilyLLM, inner).complete(messages, **kwargs)

        client = FamilyLLM([decision("web_fetcher", urls=[URL]), decision("finish")])
        settings = self.settings.model_copy(update={"deep_research_enabled": True})
        with (patch("app.agent.react_executor.execute_tool", return_value=ToolResult(success=True,
                output={"pages": [{"url": URL, "content": "Documented feature behavior."}]})),
              patch("app.agent.react_executor.generate_markdown_report", return_value="# Intermediate"),
              patch("app.agent.react_executor.save_report", return_value="not-written.md"),
              patch("app.agent.deepening.generate_markdown_report") as final_report):
            result = run_deepening(self.db, run.run_id, settings, client)
        self.assertEqual(result["status"], "failed")
        final_report.assert_not_called()
        plan = json.loads(run.plan_json)
        self.assertEqual(plan["research_outcome"]["error_code"], "budget_exhausted")
        self.assertEqual(plan["deepening_phase"], "failed")
        self.assertEqual(len(plan["deepening_sub_run_ids"]), 1)
        child = store.get_agent_run(self.db, plan["deepening_sub_run_ids"][0])
        self.assertEqual(child.status, "failed")
        from app.api.tasks import get_task_plan
        self.assertEqual(asyncio.run(get_task_plan(child.run_id, self.db)).steps, [])
        gates = [t for t in store.list_tool_traces(self.db, run.run_id) if t.tool_name == "research_quality_gate"]
        self.assertEqual(len(gates), 1)  # Initial gate only; no success gate after stop.

    def test_actual_csv_reader_goal_gate_and_saved_report_pipeline(self):
        import tempfile
        from pathlib import Path
        from app.agent.react_executor import run_react_task
        from app.evidence.service import get_provenance_bundle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs = root / "docs"
            docs.mkdir()
            source = docs / "123456.SZ.csv"
            source.write_text("date,unadjusted_close\n2020-01-01,10\n2021-01-01,12\n", encoding="utf-8")
            settings = self.settings.model_copy(update={"evidence_pipeline_version": "v2",
                "evidence_artifact_root": str(root / "artifacts"), "reference_verification_enabled": False,
                "file_reader_allowed_roots": str(docs)})
            for metric, expected in (("收盘价", "completed"), ("涨幅", "failed")):
                with self.subTest(metric=metric):
                    task = f"股票 123456.SZ 2020-01-01 至 2021-01-01 年度不复权{metric}"
                    run = store.create_agent_run(self.db, task, "summary", "real", allowed_tools=["file_reader", "report_writer"])
                    store.update_agent_run_plan(self.db, run.run_id, {"task": task, "source_mode": "real",
                        "execution_mode": "react", "steps": [], "allowed_tools": ["file_reader", "report_writer"]})
                    client = recovery.ScriptedLLM([decision("file_reader", path=str(source)),
                                                  decision("finish", goal_status="achieved")])
                    with (patch("app.agent.file_access_policy.DOCS_ROOT", docs),
                          patch("app.config.settings", settings), patch("app.agent.executor._after_run_completed"),
                          patch("app.agent.reporter.ROOT", root), patch("app.agent.reporter.REPORTS_ROOT", root / "reports")):
                        result = run_react_task(self.db, run.run_id, settings, client)
                    self.assertEqual(result["status"], expected, result)
                    if expected == "failed":
                        self.assertIsNone(run.report_path)
                        self.assertEqual(json.loads(run.plan_json)["research_outcome"]["error_code"], "structured_data_unavailable")
                        continue
                    markdown = (root / run.report_path).read_text()
                    provenance = get_provenance_bundle(self.db, run.run_id)
                    passages = {p["passage_id"]: p for p in provenance["passages"]}
                    traces = {t.trace_id for t in store.list_tool_traces(self.db, run.run_id) if t.tool_name == "file_reader"}
                    self.assertTrue(provenance["citations"])
                    for citation in provenance["citations"]:
                        self.assertIn(citation["citation_label"], markdown)
                        self.assertIn(passages[citation["passage_id"]]["trace_id"], traces)
                    self.assertIn("2020-01-01", markdown)
