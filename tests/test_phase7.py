"""Regression coverage for the Phase 7 approval and citation workflows."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.file_access_policy import file_reader_execution_arguments
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.research import models as research_models  # noqa: F401
from app.trace import models as trace_models  # noqa: F401


class _FakeCitationLLM:
    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": "fake", "model": "citation-judge"}

    def complete(self, messages, temperature=0.0, max_tokens=2000):
        from app.llm.base import LLMResponse, LLMUsage

        return LLMResponse(
            success=True,
            content=json.dumps(
                {
                    "verdicts": [
                        {
                            "citation_label": "CIT-001-01",
                            "verdict": "supported",
                        }
                    ]
                }
            ),
            provider="fake",
            model="citation-judge",
            usage=LLMUsage(prompt_tokens=12, completion_tokens=5, total_tokens=17),
        )


class Phase7DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from app.tools.defaults import register_default_tools

        register_default_tools()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()


class BoundedProvenanceContextTests(unittest.TestCase):
    def test_deep_budget_reserves_report_headroom_inside_existing_hard_limit(self) -> None:
        from types import SimpleNamespace

        from app.agent.budget import limits

        settings = SimpleNamespace(
            research_max_tool_calls=80,
            research_max_llm_calls=64,
            research_max_tokens=200000,
            research_max_seconds=1800,
            research_max_estimated_cost=0.0,
            research_tool_cost_estimate=None,
            research_llm_cost_per_million_tokens=None,
        )
        budget = limits(settings)
        self.assertEqual(budget["max_tokens"], 200000)
        self.assertEqual(budget["final_report_tokens"], 60000)
        self.assertEqual(budget["final_report_llm_calls"], 16)

    def test_default_evidence_budget_is_seventy_percent_of_report_reserve(self) -> None:
        from app.agent.budget import final_report_evidence_token_budget

        self.assertEqual(final_report_evidence_token_budget(), 5600)

    def test_context_contains_only_complete_json_claim_units(self) -> None:
        from app.agent.budget import estimate_text_tokens
        from app.agent.reporter import build_bounded_provenance_context

        bundle = {
            "schema_version": "fixture",
            "claims": [
                {"claim_id": "claim-1", "claim_text": "first", "research_node_id": "node-1"},
                {"claim_id": "claim-2", "claim_text": "second", "research_node_id": "node-2"},
            ],
            "report_claims": [
                {"report_claim_id": "report-1", "claim_id": "claim-1", "claim_text": "first"},
                {"report_claim_id": "report-2", "claim_id": "claim-2", "claim_text": "second"},
            ],
            "passages": [
                {"passage_id": "passage-1", "text": "A" * 2000, "research_node_id": "node-1"},
                {"passage_id": "passage-2", "text": "B" * 2000, "research_node_id": "node-2"},
            ],
            "citations": [
                {"report_claim_id": "report-1", "passage_id": "passage-1", "citation_label": "CIT-001-01"},
                {"report_claim_id": "report-2", "passage_id": "passage-2", "citation_label": "CIT-002-01"},
            ],
        }

        context = build_bounded_provenance_context(bundle, token_budget=600)
        payload = json.loads(context)

        self.assertLessEqual(estimate_text_tokens(context), 600)
        self.assertTrue(payload["claims"])
        self.assertTrue(all(item["citations"] for item in payload["claims"]))
        self.assertTrue(all(
            len(item["citations"][0]["text"]) <= 1200
            for item in payload["claims"]
        ))

    def test_context_selects_highest_quality_citation_for_claim_group(self) -> None:
        from app.agent.reporter import build_bounded_provenance_context

        bundle = {
            "claims": [{"claim_id": "claim-1", "claim_text": "claim", "research_node_id": "node"}],
            "report_claims": [{"report_claim_id": "report-1", "claim_id": "claim-1"}],
            "passages": [
                {"passage_id": "low", "text": "low", "research_node_id": "node"},
                {"passage_id": "high", "text": "high", "research_node_id": "node"},
            ],
            "citations": [
                {"report_claim_id": "report-1", "passage_id": "low", "citation_label": "CIT-001-01"},
                {"report_claim_id": "report-1", "passage_id": "high", "citation_label": "CIT-001-02"},
            ],
            "scope_claim_groups": [{
                "group_id": "group-1",
                "representative_claim_text": "claim",
                "members": [{"claim_id": "claim-1", "origin_run_id": "run"}],
            }],
            "scope_resolutions": [{
                "group_id": "group-1",
                "status": "resolved",
                "confidence": 0.9,
                "rationale": {"relations": [
                    {"passage_id": "low", "score": 0.2},
                    {"passage_id": "high", "score": 0.95},
                ]},
            }],
        }

        payload = json.loads(build_bounded_provenance_context(bundle, 1000))

        self.assertEqual(
            payload["claims"][0]["citations"][0]["citation_id"],
            "CIT-001-02",
        )


class PlanApprovalTests(Phase7DatabaseTestCase):
    @staticmethod
    def _plan() -> dict:
        return {
            "version": "1.0",
            "task": "research a topic",
            "source_mode": "mock",
            "allowed_tools": ["tavily_search", "web_fetcher", "report_writer"],
            "execution_mode": "planned",
            "notes": [],
            "steps": [
                {
                    "step_no": 1,
                    "tool_name": "tavily_search",
                    "goal": "discover",
                    "arguments": {"query": "old", "max_results": 5},
                    "expected_output": "urls",
                    "completion_criteria": "results exist",
                    "risk_level": "low",
                    "requires_confirmation": False,
                },
                {
                    "step_no": 2,
                    "tool_name": "web_fetcher",
                    "goal": "fetch",
                    "arguments": {"urls": []},
                    "arguments_from": {"step_no": 1, "field": "results"},
                    "expected_output": "pages",
                    "completion_criteria": "pages exist",
                    "risk_level": "low",
                    "requires_confirmation": False,
                },
                {
                    "step_no": 3,
                    "tool_name": "report_writer",
                    "goal": "report",
                    "arguments": {},
                    "expected_output": "markdown",
                    "completion_criteria": "report exists",
                    "risk_level": "low",
                    "requires_confirmation": False,
                },
            ],
        }

    def test_merge_preserves_metadata_and_remaps_dependencies(self) -> None:
        from app.api.tasks import _merge_approved_steps

        modified = [
            {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "new"}},
            {"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": []}},
        ]
        merged = _merge_approved_steps(self._plan()["steps"], modified)
        self.assertEqual(merged[0]["arguments"]["query"], "new")
        self.assertEqual(merged[0]["expected_output"], "urls")
        self.assertEqual(merged[1]["arguments_from"], {"step_no": 1, "field": "results"})
        self.assertEqual(merged[1]["completion_criteria"], "pages exist")

    def test_merge_rejects_disabled_dependency(self) -> None:
        from app.api.tasks import _merge_approved_steps

        with self.assertRaises(HTTPException) as raised:
            _merge_approved_steps(
                self._plan()["steps"],
                [{"step_no": 2, "tool_name": "web_fetcher", "arguments": {"urls": []}}],
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_approval_endpoint_persists_full_plan_and_trace(self) -> None:
        from app.api import tasks
        from app.schemas import PlanApproveRequest
        from app.trace import store

        run = store.create_agent_run(self.db, "research a topic", "summary", "mock")
        store.update_agent_run_plan(self.db, run.run_id, self._plan())
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan")

        modified = [dict(step) for step in self._plan()["steps"]]
        modified[0]["arguments"] = {"query": "approved query", "max_results": 4}

        def fake_run(db, run_id):
            completed = store.update_agent_run_status(db, run_id, "completed")
            return tasks._run_summary(completed, "completed in test")

        from app.config import Settings
        with (patch("app.api.tasks.run_task_by_mode", side_effect=fake_run),
              patch("app.api.tasks.settings", Settings(tavily_api_key="test-only"))):
            response = tasks.approve_plan(
                run.run_id,
                PlanApproveRequest(approved=True, modified_steps=modified),
                BackgroundTasks(),
                db=self.db,
            )

        self.assertEqual(response.status, "completed")
        saved = json.loads(store.get_agent_run(self.db, run.run_id).plan_json)
        self.assertEqual(saved["steps"][0]["arguments"]["query"], "approved query")
        self.assertIn("arguments_from", saved["steps"][1])
        self.assertIn("expected_output", saved["steps"][1])
        traces = store.list_tool_traces(self.db, run.run_id)
        self.assertTrue(any(trace.tool_name == "plan_approval" for trace in traces))

    def test_approval_edit_cannot_authorize_model_invented_fetch_url(self) -> None:
        """Approval edits retain only URLs that the original task supplied."""
        from app.api import tasks
        from app.schemas import PlanApproveRequest
        from app.trace import store

        run = store.create_agent_run(self.db, "research a topic", "summary", "mock")
        store.update_agent_run_plan(self.db, run.run_id, self._plan())
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan")
        modified = [dict(step) for step in self._plan()["steps"]]
        modified[1] = {**modified[1], "arguments": {"urls": ["https://example.com/placeholder"]}}

        def fake_run(db, run_id):
            completed = store.update_agent_run_status(db, run_id, "completed")
            return tasks._run_summary(completed, "completed in test")

        from app.config import Settings
        with (patch("app.api.tasks.run_task_by_mode", side_effect=fake_run),
              patch("app.api.tasks.settings", Settings(tavily_api_key="test-only"))):
            tasks.approve_plan(
                run.run_id,
                PlanApproveRequest(approved=True, modified_steps=modified),
                BackgroundTasks(), db=self.db,
            )

        saved = json.loads(store.get_agent_run(self.db, run.run_id).plan_json)
        fetch = next(step for step in saved["steps"] if step["tool_name"] == "web_fetcher")
        self.assertEqual(fetch["arguments"]["urls"], [])
        self.assertEqual(fetch["arguments_from"], {"step_no": 1, "field": "results"})

    def test_retry_rebinds_legacy_model_fetch_url_to_task_authorized_search(self) -> None:
        from app.api import tasks
        from app.schemas import TaskRetryRequest
        from app.trace import store

        plan = self._plan()
        plan["steps"][1]["arguments"] = {"urls": ["https://example.com/placeholder"]}
        plan["steps"][1].pop("arguments_from", None)
        run = store.create_agent_run(self.db, "research a topic", "summary", "mock",
                                     allowed_tools=plan["allowed_tools"])
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_status(self.db, run.run_id, "incomplete")

        response = tasks.retry_task(run.run_id, TaskRetryRequest(reuse_plan=True), self.db)

        saved = json.loads(store.get_agent_run(self.db, response.run_id).plan_json)
        fetch = next(step for step in saved["steps"] if step["tool_name"] == "web_fetcher")
        self.assertEqual(fetch["arguments"]["urls"], [])
        self.assertEqual(fetch["arguments_from"], {"step_no": 1, "field": "results"})

    def test_dispatch_blocks_legacy_untrusted_fetch_plan_for_review(self) -> None:
        from app.agent.dispatcher import run_task_by_mode
        from app.config import Settings
        from app.trace import store

        plan = self._plan()
        plan["steps"][1]["arguments"] = {"urls": ["https://example.com/placeholder"]}
        plan["steps"][1].pop("arguments_from", None)
        run = store.create_agent_run(self.db, "research a topic", "summary", "mock",
                                     allowed_tools=plan["allowed_tools"])
        store.update_agent_run_plan(self.db, run.run_id, plan)

        result = run_task_by_mode(self.db, run.run_id, Settings())

        saved_run = store.get_agent_run(self.db, run.run_id)
        saved = json.loads(saved_run.plan_json)
        self.assertEqual(result["status"], "waiting_human_plan")
        self.assertEqual(saved["steps"][1]["arguments"]["urls"], ["https://example.com/placeholder"])
        self.assertTrue(saved["plan_review_required"])
        self.assertTrue(any(trace.tool_name == "plan_revalidation" for trace in store.list_tool_traces(self.db, run.run_id)))

    def test_plan_creation_records_memory_trace_before_waiting(self) -> None:
        from app.api import tasks
        from app.schemas import TaskCreateRequest
        from app.trace import store

        plan = self._plan()
        plan["memory_recall_trace"] = {
            "event_type": "memory_recall",
            "recalled": 0,
            "injected_chars": 0,
            "memory_ids": [],
            "reason": "cold_start",
        }
        with patch("app.api.tasks.plan_task_for_review", return_value=plan):
            response = tasks.create_task(
                TaskCreateRequest(task="research", require_plan_approval=True),
                self.db,
            )
        run = store.get_agent_run(self.db, response.run_id)
        self.assertEqual(run.status, "waiting_human_plan")
        self.assertNotIn("memory_recall_trace", json.loads(run.plan_json))
        traces = store.list_tool_traces(self.db, response.run_id)
        self.assertEqual([trace.tool_name for trace in traces], ["memory_recall"])

    def test_rejected_plan_fails_with_audit_trace(self) -> None:
        from app.api import tasks
        from app.schemas import PlanApproveRequest
        from app.trace import store

        run = store.create_agent_run(self.db, "research", "summary", "mock")
        store.update_agent_run_plan(self.db, run.run_id, self._plan())
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan")
        response = tasks.approve_plan(
            run.run_id,
            PlanApproveRequest(approved=False, comment="cancelled in test"),
            BackgroundTasks(),
            db=self.db,
        )
        self.assertEqual(response.status, "failed")
        traces = store.list_tool_traces(self.db, run.run_id)
        self.assertEqual(traces[-1].tool_name, "plan_approval")
        self.assertEqual(traces[-1].status, "rejected")

    def test_waiting_plan_emits_review_sse_event(self) -> None:
        from app.trace import store
        from app.trace.events import TraceEventCursor, build_incremental_events

        run = store.create_agent_run(self.db, "research", "summary", "mock")
        plan = self._plan()
        plan["estimated_total_tokens"] = 1200
        store.update_agent_run_plan(self.db, run.run_id, plan)
        store.update_agent_run_status(self.db, run.run_id, "waiting_human_plan")
        events, should_close = build_incremental_events(
            self.db,
            run.run_id,
            TraceEventCursor(),
        )
        review = next(event for event in events if event["event_type"] == "plan_review")
        self.assertEqual(review["metadata"]["estimated_total_tokens"], 1200)
        self.assertEqual(len(review["metadata"]["steps"]), 3)
        self.assertTrue(should_close)


class CitationValidationTests(Phase7DatabaseTestCase):
    @staticmethod
    def _bundle(text: str = "该系统支持完整的证据追踪和审计能力") -> dict:
        return {
            "passages": [
                {
                    "passage_id": "p1",
                    "text": text,
                    # New integrity policy requires an explicit capability
                    # role; unclassified evidence fails closed.
                    "metadata": {"evidence_role": "primary_content"},
                }
            ],
            "citations": [{"citation_label": "CIT-001-01", "passage_id": "p1"}],
        }

    def test_duplicate_labels_are_validated_per_occurrence(self) -> None:
        from app.evidence.citation_validator import validate_citations

        report = (
            "该系统支持完整的证据追踪和审计能力 [CIT-001-01]。\n\n"
            "## 9. 引用索引\n\n| [CIT-001-01] | 原文 |"
        )
        result = validate_citations(report, self._bundle())
        self.assertEqual(result.total, 2)
        self.assertEqual(result.occurrence_total, 2)
        self.assertEqual(result.unique_citation_count, 1)
        self.assertEqual(result.supported, 1)
        self.assertEqual(result.unsupported, 1)
        self.assertLess(result.details[0].marker_start, result.details[1].marker_start)
        self.assertGreater(result.details[0].sentence_end, result.details[0].sentence_start)

    def test_same_label_can_have_supported_and_unsupported_occurrences(self) -> None:
        from app.evidence.citation_validator import validate_citations

        report = (
            "Alpha revenue reached 100 USD [CIT-001-01]. "
            "Unrelated weather report [CIT-001-01]."
        )
        result = validate_citations(
            report,
            self._bundle("Alpha revenue reached 100 USD"),
        )

        self.assertEqual(result.occurrence_total, 2)
        self.assertEqual(result.supported_occurrences, 1)
        self.assertEqual(result.unsupported_occurrences, 1)
        self.assertEqual(result.occurrence_accuracy, 0.5)

    def test_llm_secondary_judgment_is_explicit_and_metered(self) -> None:
        from app.evidence.citation_validator import validate_citations

        result = validate_citations(
            "Unrelated claim [CIT-001-01].",
            self._bundle("Different evidence passage"),
            llm_client=_FakeCitationLLM(),
            use_llm=True,
        )
        self.assertTrue(result.llm_used)
        self.assertEqual(result.supported, 1)
        self.assertEqual(result.token_in, 12)
        self.assertEqual(result.details[0].judgment_source, "llm")

    def test_metadata_role_only_supports_bibliographic_claims(self) -> None:
        from app.evidence.citation_validator import validate_citations

        bundle = {
            "source_documents": [
                {
                    "document_id": "d1",
                    "metadata": {"evidence_role": "official_metadata"},
                }
            ],
            "source_snapshots": [{"snapshot_id": "s1", "document_id": "d1"}],
            "passages": [
                {
                    "passage_id": "p1",
                    "snapshot_id": "s1",
                    "text": "Paper Alpha was published in 2024 with DOI 10.1000/alpha and reports 97% accuracy.",
                }
            ],
            "citations": [{"citation_label": "CIT-001-01", "passage_id": "p1"}],
        }

        bibliographic = validate_citations(
            "Paper Alpha was published in 2024 with DOI 10.1000/alpha [CIT-001-01].",
            bundle,
        )
        performance = validate_citations(
            "Paper Alpha reports 97% accuracy [CIT-001-01].",
            bundle,
            llm_client=_FakeCitationLLM(),
            use_llm=True,
        )

        self.assertEqual(bibliographic.supported, 1)
        self.assertEqual(performance.unsupported, 1)
        self.assertEqual(performance.details[0].judgment_source, "evidence_role")
        self.assertEqual(performance.details[0].evidence_role, "official_metadata")

    def test_no_citations_reports_not_evaluated(self) -> None:
        from app.evidence.citation_validator import (
            render_citation_validation_section,
            validate_citations,
        )

        result = validate_citations("No references.", self._bundle())
        self.assertEqual(result.total, 0)
        self.assertIn("不可评估", "\n".join(render_citation_validation_section(result)))

    def test_metrics_and_trace_are_persisted_from_rendered_result(self) -> None:
        from app.agent.executor import _persist_citation_validation
        from app.evidence.citation_validator import validate_citations
        from app.trace import store

        run = store.create_agent_run(self.db, "research", "summary", "mock")
        validation = validate_citations(
            "该系统支持完整的证据追踪和审计能力 [CIT-001-01]。",
            self._bundle(),
        )
        updated = _persist_citation_validation(self.db, run.run_id, [validation], [])
        self.assertEqual(updated.citation_total, 1)
        self.assertEqual(updated.citation_supported, 1)
        self.assertEqual(updated.citation_accuracy, 1.0)
        traces = store.list_tool_traces(self.db, run.run_id)
        self.assertEqual([trace.tool_name for trace in traces], ["citation_validator"])


# ── File access policy: HITL approval token injection ─────────────────


class FileAccessPolicyTests(unittest.TestCase):
    """Verify file_reader_execution_arguments strips plan-injected approval tokens."""

    def test_strips_plan_injected_approved_path(self):
        """A plan that already contains _approved_file_reader_path must be stripped."""
        prepared = file_reader_execution_arguments(
            arguments={
                "path": "C:/outside/file.txt",
                "_approved_file_reader_path": "C:/outside/file.txt",
            },
            plan=None,
        )
        # The injected token must be removed regardless of plan approval
        self.assertNotIn("_approved_file_reader_path", prepared)

    def test_strips_plan_injected_path_even_when_approved(self):
        """When plan is approved, plan-supplied token is stripped, then executor
        re-adds the correct token — this is the security guarantee: the executor
        is the only authority allowed to attach this field."""
        prepared = file_reader_execution_arguments(
            arguments={
                "path": "C:/outside/file.txt",
                "_approved_file_reader_path": "C:/outside/file.txt",
            },
            plan={
                "confirmation": {
                    "approved": True,
                    "approved_file_reader_paths": ["C:/outside/file.txt"],
                }
            },
        )
        # The final token was added by the executor (not the original plan-supplied value)
        self.assertIn("_approved_file_reader_path", prepared)
        self.assertEqual(
            prepared["_approved_file_reader_path"],
            "C:\\outside\\file.txt",
        )

    def test_inside_allowed_root_no_token(self):
        """Paths inside allowed roots should not get an approval token."""
        prepared = file_reader_execution_arguments(
            arguments={"path": "workspace/docs/readme.txt"},
            plan=None,
        )
        self.assertNotIn("_approved_file_reader_path", prepared)

    def test_outside_allowed_root_approved_adds_token(self):
        """When path is outside allowed roots and plan is approved, token is added."""
        prepared = file_reader_execution_arguments(
            arguments={"path": "C:/outside/file.txt"},
            plan={
                "confirmation": {
                    "approved": True,
                    "approved_file_reader_paths": ["C:/outside/file.txt"],
                }
            },
        )
        self.assertIn("_approved_file_reader_path", prepared)
        self.assertEqual(
            prepared["_approved_file_reader_path"],
            "C:\\outside\\file.txt",
        )

    def test_outside_allowed_root_not_approved_no_token(self):
        """When path is outside allowed roots and plan is NOT approved, no token."""
        prepared = file_reader_execution_arguments(
            arguments={"path": "C:/outside/file.txt"},
            plan={"confirmation": {"approved": False}},
        )
        self.assertNotIn("_approved_file_reader_path", prepared)

    def test_empty_path_no_token(self):
        """Empty path should not inject any token."""
        prepared = file_reader_execution_arguments(
            arguments={"path": ""},
            plan=None,
        )
        self.assertNotIn("_approved_file_reader_path", prepared)


if __name__ == "__main__":
    unittest.main()
