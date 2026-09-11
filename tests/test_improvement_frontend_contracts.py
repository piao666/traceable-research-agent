"""Regression coverage for final-run improvement and frontend contracts."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.dispatcher import run_task_by_mode
from app.config import Settings
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.improvement.api import (
    improvement_by_category,
    improvement_run,
    improvement_stats,
    improvement_trend,
)
from app.improvement.lifecycle import finalize_improvement_cycle
from app.improvement.models import ImprovementLog
from app.main import app
from app.memory import models as memory_models  # noqa: F401
from app.research.scope import create_research_node, create_research_scope
from app.schemas import TaskPlanResponse
from app.trace import models as trace_models  # noqa: F401
from app.trace import store
from app.trace.events import TraceEventCursor, build_incremental_events


def _plan(**extra) -> dict:
    plan = {
        "version": "contract-v1",
        "task": "contract task",
        "source_mode": "mock",
        "allowed_tools": [],
        "execution_mode": "planned",
        "requested_execution_mode": "planned",
        "planner_source": "composed",
        "skill_routing": {
            "composed": True,
            "composed_from": ["systematic_review", "technical_docs_research"],
        },
        "steps": [],
        "notes": [],
    }
    plan.update(extra)
    return plan


class ImprovementFrontendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _create_run(self, *, plan: dict | None = None, status: str = "pending"):
        run = store.create_agent_run(self.db, "contract task", "summary", "mock")
        run = store.update_agent_run_plan(self.db, run.run_id, plan or _plan())
        if status != "pending":
            run = store.update_agent_run_status(self.db, run.run_id, status, None)
        return run

    def test_days_filter_is_real_and_empty_stats_shape_is_stable(self) -> None:
        now = datetime.now(timezone.utc)
        from app.agent.outcome import INTEGRITY_VERSION
        for run_id in ("recent", "old"):
            self.db.add(trace_models.AgentRun(run_id=run_id, task="verified fixture", report_type="summary",
                source_mode="real", status="completed", plan_json=json.dumps({"research_outcome": {
                    "version": INTEGRITY_VERSION, "status": "passed", "effective_evidence_count": 1}})))
        self.db.flush()
        self.db.add_all(
            [
                ImprovementLog(
                    run_id="recent",
                    question_category="recent",
                    overall_score=8.0,
                    created_at=now,
                ),
                ImprovementLog(
                    run_id="old",
                    question_category="old",
                    overall_score=2.0,
                    created_at=now - timedelta(days=100),
                ),
            ]
        )
        self.db.commit()

        stats = improvement_stats(days=1, db=self.db)
        self.assertEqual(stats.total_runs, 1)
        self.assertEqual(stats.latest_score, 8.0)
        self.assertEqual([item.run_id for item in stats.trend], ["recent"])
        categories = improvement_by_category(days=1, db=self.db)
        self.assertEqual([item.category for item in categories.categories], ["recent"])
        trend = improvement_trend(days=1, db=self.db)
        self.assertEqual(len(trend.trend), 1)

        for row in self.db.query(ImprovementLog).all():
            self.db.delete(row)
        self.db.commit()
        empty = improvement_stats(days=30, db=self.db).model_dump()
        self.assertEqual(
            empty,
            {
                "total_runs": 0,
                "avg_overall": 0.0,
                "best_score": 0.0,
                "worst_score": 0.0,
                "latest_score": 0.0,
                "trend": [],
            },
        )

    def test_improvement_openapi_responses_are_typed(self) -> None:
        schema = app.openapi()
        for path in (
            "/api/improvement/stats",
            "/api/improvement/by-category",
            "/api/improvement/by-strategy",
            "/api/improvement/trend",
            "/api/improvement/regressions",
            "/api/improvement/runs/{run_id}",
            "/api/improvement/state",
        ):
            response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]
            self.assertIn("$ref", response_schema, path)

    def test_plan_response_preserves_composed_and_adaptive_metadata(self) -> None:
        response = TaskPlanResponse(
            run_id="run-1",
            **_plan(
                adaptive_upgrade=True,
                adaptive_upgrade_reason="quality gate",
                adaptive_phase="react_execution",
            ),
        )
        self.assertTrue(response.skill_routing["composed"])
        self.assertEqual(
            response.skill_routing["composed_from"],
            ["systematic_review", "technical_docs_research"],
        )
        self.assertTrue(response.adaptive_upgrade)
        self.assertEqual(response.adaptive_upgrade_reason, "quality gate")

    def test_intermediate_report_does_not_close_sse(self) -> None:
        run = self._create_run(
            plan=_plan(deepening_pending=True, deepening_phase="initial_react"),
            status="completed",
        )
        store.update_agent_run_report(self.db, run.run_id, "workspace/reports/intermediate.md")
        cursor = TraceEventCursor()

        events, should_close = build_incremental_events(self.db, run.run_id, cursor)
        self.assertFalse(should_close)
        self.assertEqual(events[0]["status"], "running")
        self.assertNotIn("report_ready", [event["event_type"] for event in events])
        self.assertNotIn("done", [event["event_type"] for event in events])

        plan = _plan(deepening_pending=False, deepening_phase="completed")
        store.replace_agent_run_plan(self.db, run.run_id, plan)
        events, should_close = build_incremental_events(self.db, run.run_id, cursor)
        self.assertTrue(should_close)
        self.assertIn("report_ready", [event["event_type"] for event in events])
        self.assertIn("done", [event["event_type"] for event in events])

    def test_planned_quality_gate_uses_one_final_terminal_boundary(self) -> None:
        run = self._create_run()
        seen_completion_statuses: list[str] = []

        def fake_planned(db, run_id, **kwargs):
            completion_status = kwargs["completion_status"]
            seen_completion_statuses.append(completion_status)
            store.update_agent_run_report(db, run_id, "workspace/reports/planned.md")
            current = store.update_agent_run_status(db, run_id, completion_status, None)
            return {
                "run_id": run_id,
                "status": current.status,
                "current_step": current.current_step,
                "total_steps": current.total_steps,
                "total_tool_calls": current.total_tool_calls,
                "report_url": f"/api/reports/{run_id}",
                "trace_url": f"/api/tasks/{run_id}/trace",
                "error_message": None,
            }

        finalized_statuses: list[str] = []

        def fake_finalize(db, run_id):
            finalized_statuses.append(store.get_fresh_agent_run(db, run_id).status)

        with (
            patch("app.agent.dispatcher.run_plan", side_effect=fake_planned),
            patch("app.agent.dispatcher._adaptive_upgrade_reason", return_value=None),
            patch("app.improvement.lifecycle.finalize_improvement_cycle", side_effect=fake_finalize),
        ):
            result = run_task_by_mode(
                self.db,
                run.run_id,
                Settings(react_enabled=True, parallel_execution_enabled=False, qwen_api_key="test-only"),
            )

        self.assertEqual(seen_completion_statuses, ["running"])
        self.assertEqual(finalized_statuses, ["completed"])
        self.assertEqual(result["status"], "completed")
        stored_plan = json.loads(store.get_fresh_agent_run(self.db, run.run_id).plan_json)
        self.assertFalse(stored_plan["adaptive_gate_pending"])
        self.assertEqual(stored_plan["adaptive_phase"], "completed")

    def test_adaptive_result_is_finalized_after_react(self) -> None:
        run = self._create_run()

        def fake_planned(db, run_id, **kwargs):
            store.update_agent_run_report(db, run_id, "workspace/reports/planned.md")
            current = store.update_agent_run_status(db, run_id, kwargs["completion_status"], None)
            return {
                "run_id": run_id,
                "status": current.status,
                "current_step": 0,
                "total_steps": 0,
                "total_tool_calls": 0,
                "report_url": f"/api/reports/{run_id}",
                "trace_url": f"/api/tasks/{run_id}/trace",
                "error_message": None,
            }

        def fake_react(db, run_id, _settings, llm_client=None):
            del llm_client
            self.assertEqual(store.get_fresh_agent_run(db, run_id).status, "running")
            store.update_agent_run_report(db, run_id, "workspace/reports/final.md")
            current = store.update_agent_run_status(db, run_id, "completed", None)
            return {
                "run_id": run_id,
                "status": current.status,
                "current_step": 1,
                "total_steps": 1,
                "total_tool_calls": 1,
                "report_url": f"/api/reports/{run_id}",
                "trace_url": f"/api/tasks/{run_id}/trace",
                "error_message": None,
            }

        finalized_plans: list[dict] = []

        def fake_finalize(db, run_id):
            final_run = store.get_fresh_agent_run(db, run_id)
            self.assertEqual(final_run.status, "completed")
            finalized_plans.append(json.loads(final_run.plan_json))

        with (
            patch("app.agent.dispatcher.run_plan", side_effect=fake_planned),
            patch("app.agent.dispatcher._adaptive_upgrade_reason", return_value="quality gate"),
            patch("app.agent.react_executor.run_react_task", side_effect=fake_react),
            patch("app.improvement.lifecycle.finalize_improvement_cycle", side_effect=fake_finalize),
        ):
            result = run_task_by_mode(
                self.db,
                run.run_id,
                Settings(react_enabled=True, parallel_execution_enabled=False, qwen_api_key="test-only"),
            )

        self.assertEqual(len(finalized_plans), 1)
        self.assertTrue(finalized_plans[0]["adaptive_upgrade"])
        self.assertFalse(finalized_plans[0]["adaptive_gate_pending"])
        self.assertEqual(finalized_plans[0]["requested_execution_mode"], "planned")
        self.assertEqual(result["execution_mode"], "react")
        self.assertTrue(result["adaptive_upgrade"])

    def test_resumed_adaptive_react_preserves_original_requested_mode(self) -> None:
        run = self._create_run(
            plan=_plan(
                execution_mode="react",
                adaptive_upgrade=True,
                adaptive_gate_pending=False,
                adaptive_phase="react_execution",
            ),
            status="running",
        )

        def fake_react(db, run_id, _settings, llm_client=None):
            del llm_client
            active = store.get_fresh_agent_run(db, run_id)
            react_plan = json.loads(active.plan_json or "{}")
            react_plan["requested_execution_mode"] = "react"
            store.replace_agent_run_plan(db, run_id, react_plan)
            completed = store.update_agent_run_status(db, run_id, "completed", None)
            return {
                "run_id": run_id,
                "status": completed.status,
                "current_step": 1,
                "total_steps": 1,
                "total_tool_calls": 1,
                "report_url": f"/api/reports/{run_id}",
                "trace_url": f"/api/tasks/{run_id}/trace",
                "error_message": None,
            }

        with (
            patch("app.agent.react_executor.run_react_task", side_effect=fake_react),
            patch("app.improvement.lifecycle.finalize_improvement_cycle"),
        ):
            result = run_task_by_mode(
                self.db,
                run.run_id,
                Settings(react_enabled=True, deep_research_enabled=False, qwen_api_key="test-only"),
            )

        final_plan = json.loads(store.get_fresh_agent_run(self.db, run.run_id).plan_json)
        self.assertEqual(final_plan["requested_execution_mode"], "planned")
        self.assertFalse(final_plan["adaptive_gate_pending"])
        self.assertEqual(final_plan["adaptive_phase"], "completed")
        self.assertEqual(result["requested_execution_mode"], "planned")

    def test_failed_planned_run_clears_adaptive_pending_marker(self) -> None:
        run = self._create_run()

        def fake_planned(db, run_id, **kwargs):
            self.assertEqual(kwargs["completion_status"], "running")
            failed = store.update_agent_run_status(db, run_id, "failed", "planned failure")
            return {
                "run_id": run_id,
                "status": failed.status,
                "current_step": 0,
                "total_steps": 0,
                "total_tool_calls": 0,
                "report_url": f"/api/reports/{run_id}",
                "trace_url": f"/api/tasks/{run_id}/trace",
                "error_message": failed.error_message,
            }

        with (
            patch("app.agent.dispatcher.run_plan", side_effect=fake_planned),
            patch("app.improvement.lifecycle.finalize_improvement_cycle"),
        ):
            result = run_task_by_mode(
                self.db,
                run.run_id,
                Settings(react_enabled=True, parallel_execution_enabled=False, qwen_api_key="test-only"),
            )

        final_plan = json.loads(store.get_fresh_agent_run(self.db, run.run_id).plan_json)
        self.assertFalse(final_plan["adaptive_gate_pending"])
        self.assertEqual(final_plan["adaptive_phase"], "failed")
        self.assertEqual(result["status"], "failed")

    def test_improvement_lifecycle_skips_nonterminal_and_records_final_result(self) -> None:
        run = self._create_run(status="running")
        with patch("app.improvement.evaluator.auto_evaluate_and_log") as evaluate:
            self.assertIsNone(finalize_improvement_cycle(self.db, run.run_id))
            evaluate.assert_not_called()

        store.update_agent_run_status(self.db, run.run_id, "completed", None)
        log_entry = SimpleNamespace(overall_score=7.4)
        with (
            patch("app.improvement.evaluator.auto_evaluate_and_log", return_value=log_entry) as evaluate,
            patch("app.improvement.weight_updater.maybe_update_weights", return_value=False),
            patch("app.improvement.few_shot.promote_to_few_shot", return_value=False),
        ):
            returned = finalize_improvement_cycle(self.db, run.run_id)
        self.assertIs(returned, log_entry)
        evaluate.assert_called_once_with(self.db, run.run_id)
        traces = store.list_tool_traces(self.db, run.run_id)
        self.assertEqual(traces[-1].tool_name, "improvement_evaluation")
        self.assertEqual(traces[-1].status, "success")

    def test_deep_v2_evaluator_uses_effective_scope_result_boundary(self) -> None:
        from app.agent.outcome import SCOPE_INTEGRITY_VERSION
        from app.improvement.evaluator import auto_evaluate_and_log
        from app.reporting.integrity import REPORT_INTEGRITY_VERSION

        root = self._create_run(
            plan=_plan(
                execution_mode="deep_research_v2",
                deepening_phase="deprecated",
                research_outcome={
                    "version": SCOPE_INTEGRITY_VERSION,
                    "status": "passed",
                    "effective_evidence_count": 9,
                },
                report_integrity={
                    "version": REPORT_INTEGRITY_VERSION,
                    "status": "passed",
                },
            )
        )
        scope = create_research_scope(self.db, root.run_id, {})
        root_node = create_research_node(
            self.db,
            scope.scope_id,
            parent_node_id=None,
            run_id=root.run_id,
            node_type="discovery",
            topic="Discovery",
            query="scope",
            research_goal="scope",
            depth=0,
            priority=0,
            status="completed",
        )
        child_run_ids: list[str] = []
        for priority, topic in enumerate(("Branch A", "Branch B"), 1):
            child = store.create_agent_run(
                self.db,
                topic,
                "summary",
                "real",
                parent_run_id=root.run_id,
                root_run_id=root.run_id,
                run_role="research_branch",
                research_scope_id=scope.scope_id,
                engine_version="v2",
            )
            child_run_ids.append(child.run_id)
            create_research_node(
                self.db,
                scope.scope_id,
                parent_node_id=root_node.node_id,
                run_id=child.run_id,
                node_type="query",
                topic=topic,
                query=topic,
                research_goal=topic,
                depth=1,
                priority=priority,
                status="completed",
            )
        store.update_agent_run_citation_validation(
            self.db,
            root.run_id,
            total=15,
            supported=15,
            weakly_supported=0,
            unsupported=0,
            accuracy=1.0,
        )
        store.update_agent_run_status(self.db, root.run_id, "completed", None)

        tiers = ["T2", "T2", *("T0" for _ in range(4)), *("T1" for _ in range(3))]
        origin_run_ids = [
            root.run_id,
            root.run_id,
            *([child_run_ids[0]] * 4),
            *([child_run_ids[1]] * 3),
        ]
        documents = [
            {
                "document_id": f"doc-{index}",
                "origin_run_id": origin_run_ids[index],
                "canonical_uri": f"https://example.com/{index}",
                "metadata": {
                    "research_eligible": True,
                    "source_tier": tier,
                },
            }
            for index, tier in enumerate(tiers)
        ]
        passages = [
            {
                "passage_id": f"passage-{index}",
                "content_basis": (
                    "full_text" if index < 5 else "partial" if index < 7 else "snippet_only"
                ),
            }
            for index in range(9)
        ]
        provenance = {
            "source_documents": documents,
            "source_snapshots": [],
            "passages": passages,
            "assertions": [],
            "scope_identity": {
                "source_aliases": [
                    {"representative_document_id": item["document_id"]}
                    for item in documents
                ],
                "passage_aliases": [
                    {"representative_passage_id": item["passage_id"]}
                    for item in passages
                ],
            },
        }
        with (
            patch(
                "app.improvement.evaluator.get_result_provenance_bundle",
                return_value=provenance,
            ) as get_provenance,
            patch(
                "app.improvement.evaluator.list_result_traces",
                return_value=[],
            ) as get_traces,
        ):
            entry = auto_evaluate_and_log(self.db, root.run_id)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.execution_mode, "deep_research_v2")
        self.assertEqual((entry.tier_t0, entry.tier_t1, entry.tier_t2), (4, 3, 2))
        self.assertEqual(entry.citation_count, 15)
        self.assertEqual(
            {item["origin_run_id"] for item in documents},
            {root.run_id, *child_run_ids},
        )
        metadata = json.loads(entry.evaluation_metadata_json)
        self.assertEqual(metadata["result_scope"], "research_scope")
        self.assertEqual(metadata["scope_id"], scope.scope_id)
        self.assertEqual(metadata["effective_source_count"], 9)
        self.assertEqual(metadata["effective_passage_count"], 9)
        self.assertFalse(metadata["coverage_evaluable"])
        get_provenance.assert_called_once()
        get_traces.assert_called_once()

    def test_per_run_quality_response_exposes_all_dimensions(self) -> None:
        self.db.add(
            ImprovementLog(
                run_id="quality-run",
                overall_score=7.2,
                relevance_score=6.0,
                factual_accuracy=0.8,
                coverage_score=6.0,
                source_quality_score=7.0,
                auditability_score=8.0,
                citation_count=5,
                tier_t0=1,
                tier_t1=2,
                tier_t2=2,
            )
        )
        self.db.commit()
        response = improvement_run("quality-run", self.db)
        self.assertEqual(response.overall_score, 7.2)
        self.assertEqual(response.factual_accuracy, 0.8)
        self.assertEqual(response.auditability_score, 8.0)


if __name__ == "__main__":
    unittest.main()
