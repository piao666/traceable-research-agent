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
            "passages": [{"passage_id": "p1", "text": text}],
            "citations": [{"citation_label": "CIT-001-01", "passage_id": "p1"}],
        }

    def test_duplicate_labels_are_counted_once_and_cjk_is_supported(self) -> None:
        from app.evidence.citation_validator import validate_citations

        report = (
            "该系统支持完整的证据追踪和审计能力 [CIT-001-01]。\n\n"
            "## 9. 引用索引\n\n| [CIT-001-01] | 原文 |"
        )
        result = validate_citations(report, self._bundle())
        self.assertEqual(result.total, 1)
        self.assertEqual(result.supported, 1)

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
