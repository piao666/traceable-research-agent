"""Offline regressions for the failed-search/empty-fetch false-success incident."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agent.evidence import build_evidence_bundle
from app.config import Settings
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.improvement import models as improvement_models  # noqa: F401
from app.tools.base import ToolResult
from app.tools.web_fetcher import web_fetch
from app.trace import store
from app.trace.logger import record_trace_event


def web_plan() -> dict:
    return {
        "version": "regression", "execution_mode": "planned",
        "requested_execution_mode": "planned", "source_mode": "real",
        "allowed_tools": ["tavily_search", "web_fetcher", "report_writer"],
        "steps": [
            {"step_no": 1, "tool_name": "tavily_search", "goal": "Find sources",
             "arguments": {"query": "compare frameworks"}},
            {"step_no": 2, "tool_name": "web_fetcher", "goal": "Read sources",
             "arguments": {"urls": []}, "arguments_from": {"step_no": 1, "field": "results"}},
            {"step_no": 3, "tool_name": "report_writer", "arguments": {}},
        ],
    }


class ResearchIntegrityTests(unittest.TestCase):
    def setUp(self):
        # Production startup registers tools. Keep direct executor tests equally
        # initialized and independent of which other test modules ran first.
        from app.tools import registry
        from app.tools.defaults import register_default_tools
        for mapping in (registry._tool_specs, registry._tool_handlers):
            guard = patch.dict(mapping, clear=True)
            guard.start()
            self.addCleanup(guard.stop)
        register_default_tools()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.run = store.create_agent_run(self.db, "Compare frameworks", "summary", "real")
        self.plan = web_plan()
        store.update_agent_run_plan(self.db, self.run.run_id, self.plan)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def trace(self, tool, status, output, step=0, summary="internal summary"):
        return record_trace_event(self.db, self.run.run_id, step, tool, status,
                                  {}, summary, output)

    def test_incident_audit_and_empty_results_produce_no_evidence(self):
        traces = [
            self.trace("memory_recall", "success", {"count": 0}),
            self.trace("plan_approval", "approved", {"approved": True}),
            self.trace("tavily_search", "failed", {"results": []}, 1),
            self.trace("web_fetcher", "success", {"pages": [], "fetched_count": 0}, 2),
            self.trace("finish", "success", {"summary": "An unsupported model conclusion"}, 3),
        ]
        bundle = build_evidence_bundle(self.run, self.plan, [], traces)
        self.assertEqual(bundle.total_evidence_items, 0)
        self.assertEqual(bundle.claims, [])
        self.assertTrue(bundle.warnings)

    def test_web_pages_are_extracted_individually_with_content_basis(self):
        trace = self.trace("web_fetcher", "success", {"pages": [
            {"url": "https://example.com/a", "content": "Actual source text", "content_basis": "partial"},
            {"url": "https://example.org/b", "content": "", "error": "timeout"},
        ]}, 2)
        bundle = build_evidence_bundle(self.run, self.plan, [], [trace])
        self.assertEqual(len(bundle.evidence_items), 1)
        item = bundle.evidence_items[0]
        self.assertEqual(item.source_ref, "https://example.com/a")
        self.assertEqual(item.snippet, "Actual source text")
        self.assertEqual(item.metadata["content_basis"], "partial")

    def test_empty_fetch_is_unsuccessful_without_http(self):
        with patch("app.tools.web_fetcher.httpx.Client") as client:
            result = web_fetch({"urls": []}, settings_obj=Settings(web_fetcher_cache_enabled=False))
        self.assertFalse(result.success)
        self.assertEqual(result.metadata["error_type"], "empty_input")
        client.assert_not_called()

    def test_preflight_checks_required_plan_capabilities_only(self):
        from app.agent.preflight import check_plan_readiness
        settings = Settings(offline_mode=False, tavily_api_key=None, qwen_api_key=None)
        result = check_plan_readiness(self.plan, settings)
        self.assertFalse(result["ready"])
        self.assertIn("TAVILY_API_KEY", str(result["blockers"]))
        local = {**self.plan, "steps": [{"tool_name": "file_reader"}], "allowed_tools": ["file_reader"]}
        self.assertTrue(check_plan_readiness(local, settings)["ready"])

    def test_preflight_does_not_expose_keys(self):
        from app.agent.preflight import capability_summary, check_plan_readiness
        settings = Settings(offline_mode=False, tavily_api_key="private-search-value",
                            qwen_api_key="private-model-value")
        payload = json.dumps([capability_summary(settings), check_plan_readiness(self.plan, settings)])
        self.assertNotIn("private-search-value", payload)
        self.assertNotIn("private-model-value", payload)

    def test_sequential_missing_configuration_never_executes(self):
        from app.agent.executor import run_plan
        with patch("app.agent.executor.execute_tool") as execute:
            result = run_plan(self.db, self.run.run_id,
                              settings_obj=Settings(offline_mode=False, tavily_api_key=None))
        self.assertEqual(result["status"], "failed")
        execute.assert_not_called()
        self.assertIsNone(self.run.report_path)
        self.assertTrue(any(t.tool_name == "execution_preflight" and t.status == "failed"
                            for t in store.list_tool_traces(self.db, self.run.run_id)))

    def test_empty_search_never_completes_or_generates_report(self):
        from app.agent.executor import run_plan
        with (
            patch("app.agent.executor.is_executable_tool", return_value=True),
            patch("app.agent.executor.execute_tool", return_value=ToolResult(success=True, output={"results": []})) as execute,
            patch("app.agent.executor.generate_markdown_report") as report,
        ):
            result = run_plan(self.db, self.run.run_id, settings_obj=Settings(
                offline_mode=False, tavily_api_key="test-only", max_refetch_rounds=0))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(execute.call_count, 1)
        report.assert_not_called()
        self.assertTrue(any(t.status == "skipped" for t in store.list_tool_traces(self.db, self.run.run_id)))

    def test_no_evidence_materialization_has_no_sources_or_citations(self):
        from app.evidence.artifact_store import ArtifactStore
        from app.evidence.service import materialize_provenance_bundle
        traces = [self.trace("memory_recall", "success", {"count": 0})]
        bundle = build_evidence_bundle(self.run, self.plan, [], traces)
        with tempfile.TemporaryDirectory() as directory:
            payload = materialize_provenance_bundle(self.db, self.run, bundle, traces,
                ArtifactStore(Path(directory)), extractor_version="research-integrity-v1")
        self.assertEqual(payload["source_documents"], [])
        self.assertEqual(payload["citations"], [])
        self.assertEqual(payload["integrity"]["citation_coverage"], 0)

    def test_parallel_empty_search_does_not_call_fetch(self):
        from app.agent.parallel_executor import run_plan_parallel
        with (patch("app.agent.parallel_executor.is_executable_tool", return_value=True),
              patch("app.agent.parallel_executor.execute_tool", return_value=ToolResult(success=True, output={"results": []})) as execute,
              patch("app.agent.parallel_executor.generate_markdown_report") as report):
            result = run_plan_parallel(self.db, self.run.run_id, Settings(
                tavily_api_key="test-only", offline_mode=False, max_refetch_rounds=0))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(execute.call_count, 1)
        report.assert_not_called()

    def test_parallel_missing_key_is_blocked(self):
        from app.agent.parallel_executor import run_plan_parallel
        with patch("app.agent.parallel_executor.execute_tool") as execute:
            result = run_plan_parallel(self.db, self.run.run_id, Settings(offline_mode=False, tavily_api_key=None))
        self.assertEqual(result["status"], "failed")
        execute.assert_not_called()

    def test_react_finish_without_external_evidence_fails(self):
        from app.agent.react_executor import _complete_report
        self.plan["execution_mode"] = "react"
        state = {"observation_history": [{"action": "finish", "success": True,
                                         "output": {"summary": "Unsupported answer"}}]}
        with patch("app.agent.react_executor.generate_markdown_report") as report:
            result = _complete_report(self.db, self.run.run_id, self.plan, state, "finish", Settings())
        self.assertEqual(result["status"], "failed")
        report.assert_not_called()

    def test_required_fetch_cannot_be_satisfied_by_search_snippets(self):
        from app.agent.outcome import assess_research_outcome
        traces = [self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.com/a", "content": "Search snippet"}]}, 1)]
        outcome = assess_research_outcome(self.run, self.plan, [], traces, Settings(offline_mode=False))
        self.assertEqual(outcome["error_code"], "required_fetch_failed")

    def test_search_answer_and_url_only_results_are_not_sources(self):
        trace = self.trace("tavily_search", "success", {"answer": "Model answer", "results": [
            {"url": "https://example.com/a", "title": "A"}]}, 1)
        self.assertEqual(build_evidence_bundle(self.run, self.plan, [], [trace]).total_evidence_items, 0)

    def test_same_step_refetch_records_are_not_lost(self):
        first = self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.com/a", "content": "First source"}]}, 1)
        second = self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.org/b", "content": "Second source"}]}, 1)
        observations = [{"trace_id": first.trace_id, "tool_name": "tavily_search", "step_no": 1,
                         "success": True, "output": json.loads(first.output_json)}]
        items = build_evidence_bundle(self.run, self.plan, observations, [first, second]).evidence_items
        self.assertEqual({item.trace_id for item in items}, {first.trace_id, second.trace_id})

    def test_zero_citations_are_not_evaluated_as_perfect(self):
        from app.evidence.citation_validator import CitationValidationReport
        self.assertEqual(CitationValidationReport().accuracy, 0)
        self.assertFalse(CitationValidationReport().to_dict()["evaluated"])

    def test_retry_refreshes_config_and_requires_new_plan_approval(self):
        from app.api.tasks import retry_task
        from app.schemas import TaskRetryRequest
        store.update_agent_run_status(self.db, self.run.run_id, "failed", "old failure")
        self.plan.update({"requires_plan_approval": True, "adaptive_upgrade": True,
                          "research_outcome": {"version": "old"}, "confirmation": {"approved": True}})
        store.replace_agent_run_plan(self.db, self.run.run_id, self.plan)
        with patch("app.api.tasks.settings", Settings(tavily_api_key="new-private-value", offline_mode=False)):
            response = retry_task(self.run.run_id, TaskRetryRequest(), self.db)
        new = store.get_agent_run(self.db, response.run_id)
        plan = json.loads(new.plan_json)
        self.assertEqual(new.status, "waiting_human_plan")
        self.assertNotIn("confirmation", plan)
        self.assertNotIn("research_outcome", plan)
        self.assertNotIn("adaptive_upgrade", plan)
        self.assertTrue(json.loads(new.run_config_snapshot)["tavily_configured"])
        self.assertNotIn("new-private-value", new.run_config_snapshot)

    def test_legacy_run_is_flagged_without_database_rewrite(self):
        from app.api.tasks import _task_status_response
        store.update_agent_run_status(self.db, self.run.run_id, "completed", None)
        self.run.citation_total = 3
        self.run.citation_accuracy = 0.6667
        self.db.commit()
        before = self.run.plan_json
        response = _task_status_response(self.run)
        self.assertTrue(response.requires_review)
        self.assertFalse(response.citation_evaluated)
        self.assertEqual(response.citation_accuracy, 0)
        self.assertEqual(self.run.citation_accuracy, 0.6667)
        self.assertEqual(self.run.plan_json, before)

    def test_http_preflight_preserves_draft_and_blocks_approval(self):
        # Direct endpoint calls avoid cross-thread use of the fixture's SQLite connection.
        from app.api.tasks import get_task_preflight, approve_plan
        from app.schemas import PlanApproveRequest
        from fastapi import BackgroundTasks, HTTPException
        store.update_agent_run_status(self.db, self.run.run_id, "waiting_human_plan", None)
        with patch("app.api.tasks.settings", Settings(offline_mode=False, tavily_api_key=None)):
            self.assertFalse(get_task_preflight(self.run.run_id, self.db).ready)
            with self.assertRaises(HTTPException) as caught:
                approve_plan(self.run.run_id, PlanApproveRequest(approved=True), BackgroundTasks(), self.db, True)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "configuration_not_ready")
        self.assertEqual(store.get_fresh_agent_run(self.db, self.run.run_id).status, "waiting_human_plan")
        self.assertEqual(store.list_tool_traces(self.db, self.run.run_id), [])

    def test_valid_web_run_completes_with_real_evidence_and_partial_warning(self):
        from app.agent.executor import run_plan
        results = [ToolResult(success=True, output={"results": [
            {"url": "https://example.com/a", "content": "Actual search evidence"}]}),
            ToolResult(success=True, output={"pages": [
                {"url": "https://example.com/a", "content": "Actual full article text", "content_basis": "full_text"},
                {"url": "https://example.org/b", "error": "timeout", "content": ""}], "failed_count": 1})]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("app.agent.executor.is_executable_tool", return_value=True),
            patch("app.agent.executor.execute_tool", side_effect=results),
            patch("app.agent.executor.save_report", return_value="workspace/reports/test-fixture.md"),
            patch("app.agent.executor._after_run_completed"),
        ):
            result = run_plan(self.db, self.run.run_id, settings_obj=Settings(
                offline_mode=False, tavily_api_key="test-only", max_refetch_rounds=0,
                evidence_reasoning_enabled=False, evidence_artifact_root=directory))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(json.loads(self.run.plan_json)["research_outcome"]["effective_evidence_count"], 2)
        self.assertTrue(json.loads(self.run.plan_json)["research_outcome"]["warnings"])

    def test_provenance_adds_new_evidence_on_active_run_without_deleting_old_revision(self):
        from app.evidence.artifact_store import ArtifactStore
        from app.evidence.service import materialize_provenance_bundle
        store.update_agent_run_status(self.db, self.run.run_id, "running", None)
        first = self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.com/a", "content": "First source"}]}, 1)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            bundle = build_evidence_bundle(self.run, self.plan, [], [first])
            initial = materialize_provenance_bundle(self.db, self.run, bundle, [first], artifacts, extractor_version="test")
            second = self.trace("web_fetcher", "success", {"pages": [
                {"url": "https://example.org/b", "content": "Second source"}]}, 2)
            bundle = build_evidence_bundle(self.run, self.plan, [], [first, second])
            latest = materialize_provenance_bundle(self.db, self.run, bundle, [first, second], artifacts, extractor_version="test")
            self.assertNotEqual(initial["extractor_version"], latest["extractor_version"])
            self.assertEqual(len(latest["source_documents"]), 2)
            for claim in initial["claims"]:
                self.assertIsNotNone(self.db.get(evidence_models.ResearchClaim, claim["claim_id"]))
            again = materialize_provenance_bundle(self.db, self.run, bundle, [first, second], artifacts, extractor_version="test")
            self.assertEqual(latest["citations"], again["citations"])

    def test_sync_and_async_http_block_missing_key_without_consuming_draft(self):
        import asyncio
        from fastapi import BackgroundTasks, HTTPException
        from app.api.tasks import run_task, run_task_async
        background = BackgroundTasks()
        with (patch("app.api.tasks.settings", Settings(offline_mode=False, tavily_api_key=None)),
              patch("app.api.tasks.run_task_by_mode") as execute):
            with self.assertRaises(HTTPException):
                run_task(self.run.run_id, self.db)
            with self.assertRaises(HTTPException):
                asyncio.run(run_task_async(self.run.run_id, background, self.db))
        self.assertEqual(store.get_fresh_agent_run(self.db, self.run.run_id).status, "pending")
        self.assertEqual(background.tasks, [])
        execute.assert_not_called()

    def test_dispatcher_missing_key_cannot_adapt_failure_to_completed(self):
        from app.agent.dispatcher import run_task_by_mode
        with (patch("app.agent.dispatcher.run_plan") as planned,
              patch("app.agent.react_executor.run_react_task") as react):
            result = run_task_by_mode(self.db, self.run.run_id, Settings(
                offline_mode=False, tavily_api_key=None, qwen_api_key=None))
        self.assertEqual(result["status"], "failed")
        planned.assert_not_called()
        react.assert_not_called()

    def test_explicit_react_missing_model_or_disabled_is_blocked(self):
        from app.agent.preflight import check_plan_readiness
        plan = {"execution_mode": "react", "steps": []}
        for settings in (Settings(qwen_api_key=None), Settings(react_enabled=False, qwen_api_key="test-only")):
            with self.subTest(enabled=settings.react_enabled):
                self.assertFalse(check_plan_readiness(plan, settings)["ready"])

    def test_cancelled_run_cannot_be_overwritten_by_stale_session(self):
        from sqlalchemy.pool import StaticPool
        engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(engine)
        with Session(engine) as first, Session(engine) as second:
            run = store.create_agent_run(first, "cancel fixture", "summary", "real")
            stale = store.get_agent_run(second, run.run_id)
            store.update_agent_run_status(first, run.run_id, "cancelled", "cancelled by user")
            result = store.update_agent_run_status(second, stale.run_id, "completed", None)
            self.assertEqual(result.status, "cancelled")
            self.assertEqual(result.error_message, "cancelled by user")
        engine.dispose()

    def test_run_claim_is_consumed_once(self):
        self.assertTrue(store.claim_pending_agent_run(self.db, self.run.run_id))
        self.assertFalse(store.claim_pending_agent_run(self.db, self.run.run_id))
        store.update_agent_run_status(self.db, self.run.run_id, "cancelled", None)
        self.assertFalse(store.claim_pending_agent_run(self.db, self.run.run_id))

    def test_all_page_fetch_failures_are_unsuccessful(self):
        import httpx
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client, patch(
            "app.tools.ssrf.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]
        ):
            result = web_fetch({"urls": ["https://example.com/a"]},
                settings_obj=Settings(web_fetcher_cache_enabled=False), client=client)
        self.assertFalse(result.success)
        self.assertEqual(result.output["fetched_count"], 0)

    def test_mock_fallback_cannot_satisfy_real_research(self):
        from app.agent.outcome import assess_research_outcome
        trace = self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.com/a", "content": "Fake source"}],
            "metadata": {"data_source": "fallback", "fallback_used": True}}, 1)
        result = assess_research_outcome(self.run, self.plan, [], [trace], Settings(offline_mode=False))
        self.assertEqual(result["effective_evidence_count"], 0)
        self.assertEqual(result["status"], "failed")

    def test_local_material_can_complete_without_external_credentials(self):
        from app.agent.executor import run_plan
        plan = {"execution_mode": "planned", "steps": [
            {"step_no": 1, "tool_name": "file_reader", "arguments": {"path": "fixture.txt"}}]}
        store.replace_agent_run_plan(self.db, self.run.run_id, plan)
        with (tempfile.TemporaryDirectory() as directory,
              patch("app.agent.executor.is_executable_tool", return_value=True),
              patch("app.agent.executor.execute_tool", return_value=ToolResult(success=True,
                  output={"path": "fixture.txt", "content": "Actual local material"})),
              patch("app.agent.executor.save_report", return_value="test-fixture.md"),
              patch("app.agent.executor._after_run_completed")):
            result = run_plan(self.db, self.run.run_id, Settings(offline_mode=False,
                tavily_api_key=None, qwen_api_key=None, evidence_artifact_root=directory,
                evidence_reasoning_enabled=False, max_refetch_rounds=0))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["research_outcome"]["effective_evidence_count"], 1)

    def test_runtime_failure_is_traced_and_exception_secret_is_not_persisted(self):
        from app.agent.outcome import fail_execution
        run = fail_execution(self.db, self.run.run_id, RuntimeError("private-provider-token-123"))
        self.assertEqual(run.status, "failed")
        traces = store.list_tool_traces(self.db, run.run_id)
        self.assertEqual(traces[-1].tool_name, "execution_failure")
        self.assertNotIn("private-provider-token-123", run.plan_json + run.error_message + traces[-1].output_json)

    def test_sse_failure_has_no_report_ready_event(self):
        from app.agent.outcome import fail_execution
        from app.trace.events import TraceEventCursor, build_incremental_events
        fail_execution(self.db, self.run.run_id, RuntimeError("fixture"))
        events, close = build_incremental_events(self.db, self.run.run_id, TraceEventCursor())
        self.assertTrue(close)
        self.assertNotIn("report_ready", [event["event_type"] for event in events])
        status = next(event for event in events if event["event_type"] == "run_status")
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["metadata"]["research_outcome"]["status"], "failed")

    def test_invalid_citations_cannot_be_remapped_to_nearest_number(self):
        from app.agent.reporter import _repair_synthesis_citations, _valid_synthesis_citations
        bundle = {"citations": [{"citation_label": "CIT-001-01"}]}
        text = "Unsupported statement [CIT-001-02]."
        self.assertFalse(_valid_synthesis_citations(text, bundle))
        repaired = _repair_synthesis_citations(text, bundle)
        self.assertNotIn("CIT-001-01", repaired)
        self.assertIn("待核验", repaired)
        self.assertFalse(_valid_synthesis_citations(text, {"citations": []}))

    def test_llm_report_failure_does_not_silently_become_rule_report(self):
        from unittest.mock import Mock
        from app.agent.reporter import generate_markdown_report
        plan = {**self.plan, "research_outcome": {"status": "passed"}}
        with patch("app.agent.reporter._llm_synthesize_answer", return_value=None):
            with self.assertRaisesRegex(ValueError, "report_synthesis_failed"):
                generate_markdown_report(self.run, plan, [], [], llm_client=Mock())

    def test_deepening_invalid_or_empty_response_is_not_comprehensive(self):
        from app.agent.deepening import _parse_deepening_response
        for value in ("", "garbage", "[]", '{"learnings": "invalid"}'):
            with self.subTest(value=value):
                parsed = _parse_deepening_response(value)
                self.assertFalse(parsed["is_comprehensive"])
                self.assertIn("error", parsed)

    def test_legacy_quality_rows_are_excluded_without_deletion(self):
        from app.improvement.api import improvement_stats
        from app.improvement.models import ImprovementLog
        store.update_agent_run_status(self.db, self.run.run_id, "completed", None)
        self.db.add(ImprovementLog(run_id=self.run.run_id, overall_score=10))
        self.db.commit()
        self.assertEqual(improvement_stats(days=30, db=self.db).total_runs, 0)
        self.assertIsNotNone(self.db.get(ImprovementLog, self.run.run_id))

    def test_active_provenance_revision_excludes_old_sources_without_deleting_them(self):
        from app.evidence.artifact_store import ArtifactStore
        from app.evidence.service import materialize_provenance_bundle
        store.update_agent_run_status(self.db, self.run.run_id, "running", None)
        trace = self.trace("tavily_search", "success", {"results": [
            {"url": "https://example.com/a", "content": "Valid source"}]}, 1)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            first = materialize_provenance_bundle(self.db, self.run,
                build_evidence_bundle(self.run, self.plan, [], [trace]), [trace], artifacts, extractor_version="test")
            empty = materialize_provenance_bundle(self.db, self.run,
                build_evidence_bundle(self.run, self.plan, [], []), [], artifacts, extractor_version="test")
        self.assertEqual(empty["source_documents"], [])
        self.assertEqual(empty["citations"], [])
        self.assertIsNotNone(self.db.get(evidence_models.SourceDocument, first["source_documents"][0]["document_id"]))

    def test_failed_intermediate_report_is_not_exposed_as_completed_research(self):
        import asyncio
        from app.api.reports import get_report, download_report
        from app.agent.outcome import fail_execution
        from fastapi import HTTPException
        store.update_agent_run_report(self.db, self.run.run_id, "workspace/reports/intermediate.md")
        fail_execution(self.db, self.run.run_id, RuntimeError("failure after intermediate report"))
        response = asyncio.run(get_report(self.run.run_id, self.db))
        self.assertFalse(response.exists)
        self.assertEqual(response.markdown, "")
        self.assertTrue(response.quality_warnings)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(download_report(self.run.run_id, "markdown", self.db))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.run.report_path, "workspace/reports/intermediate.md")

    def test_parallel_independent_empty_tools_cannot_complete(self):
        from app.agent.parallel_executor import run_plan_parallel
        plan = {"execution_mode": "planned", "steps": [
            {"step_no": 1, "tool_name": "file_reader", "arguments": {"path": "a.md"}},
            {"step_no": 2, "tool_name": "file_reader", "arguments": {"path": "b.md"}}]}
        store.replace_agent_run_plan(self.db, self.run.run_id, plan)
        with (patch("app.agent.parallel_executor.is_executable_tool", return_value=True),
              patch("app.agent.parallel_executor.execute_tool", return_value=ToolResult(success=True, output={})) as tool,
              patch("app.agent.parallel_executor.generate_markdown_report") as report):
            result = run_plan_parallel(self.db, self.run.run_id, Settings(max_refetch_rounds=0))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(tool.call_count, 2)
        report.assert_not_called()

    def test_react_dynamic_search_cannot_bypass_key_preflight(self):
        from unittest.mock import Mock
        from app.agent.react_executor import run_react_task
        from app.llm.base import LLMResponse
        from app.tools.base import ToolSpec
        plan = {"execution_mode": "react", "steps": [], "allowed_tools": ["tavily_search"]}
        store.replace_agent_run_plan(self.db, self.run.run_id, plan)
        client = Mock()
        client.is_available.return_value = True
        client.describe.return_value = {"provider": "fixture"}
        client.complete.return_value = LLMResponse(success=True, provider="fixture",
            content=json.dumps({"thought": "search", "action": "tavily_search", "args": {"query": "test"}}))
        with (patch("app.agent.react_executor.list_tools", return_value=[ToolSpec(name="tavily_search", description="fixture", input_schema={})]),
              patch("app.agent.react_executor.execute_tool") as tool):
            result = run_react_task(self.db, self.run.run_id, Settings(tavily_api_key=None), client)
        self.assertEqual(result["status"], "failed")
        tool.assert_not_called()

    def test_deepening_failed_synthesis_records_limitation_not_comprehensive(self):
        from unittest.mock import Mock
        from app.agent.deepening import run_deepening
        from app.llm.base import LLMResponse
        client = Mock()
        client.is_available.return_value = True
        client.complete.return_value = LLMResponse(success=False, provider="fixture", error_message="unavailable")

        def initial(db, run_id, settings, llm):
            self.trace("web_fetcher", "success", {"pages": [
                {"url": "https://example.com/a", "content": "Valid initial source"}]}, 2)
            store.update_agent_run_status(db, run_id, "completed", None)
            return {"run_id": run_id, "status": "completed"}

        with (tempfile.TemporaryDirectory() as directory,
              patch("app.agent.deepening.run_react_task", side_effect=initial),
              patch("app.agent.deepening.save_report", return_value="workspace/reports/fixture.md")):
            result = run_deepening(self.db, self.run.run_id, Settings(
                deep_research_enabled=True, evidence_reasoning_enabled=False,
                evidence_artifact_root=directory), client)
        self.assertEqual(result["status"], "completed")
        outcome = json.loads(self.run.plan_json)["research_outcome"]
        self.assertTrue(any("completeness was not established" in text for text in outcome["warnings"]))
        rounds = [trace for trace in store.list_tool_traces(self.db, self.run.run_id) if trace.tool_name == "deepening_round"]
        self.assertEqual(rounds[0].status, "failed")
        self.assertFalse(json.loads(rounds[0].output_json)["is_comprehensive"])

    def test_deepening_final_gate_rejects_no_evidence(self):
        from unittest.mock import Mock
        from app.agent.deepening import run_deepening
        from app.llm.base import LLMResponse
        client = Mock()
        client.is_available.return_value = True
        client.complete.return_value = LLMResponse(success=False, provider="fixture")

        def initial(db, run_id, settings, llm):
            store.update_agent_run_status(db, run_id, "completed", None)
            return {"run_id": run_id, "status": "completed"}

        with (patch("app.agent.deepening.run_react_task", side_effect=initial),
              patch("app.agent.deepening.generate_markdown_report") as report):
            result = run_deepening(self.db, self.run.run_id, Settings(deep_research_enabled=True), client)
        self.assertEqual(result["status"], "failed")
        report.assert_not_called()

    def test_transient_success_cannot_override_persisted_failed_trace(self):
        trace = self.trace("tavily_search", "failed", {"results": []}, 1)
        observations = [{"trace_id": trace.trace_id, "tool_name": "tavily_search", "step_no": 1,
            "success": True, "output": {"results": [{"url": "https://example.com/a", "content": "Stale content"}]}}]
        self.assertEqual(build_evidence_bundle(self.run, self.plan, observations, [trace]).total_evidence_items, 0)


if __name__ == "__main__":
    unittest.main()
