"""R4 contracts: isolated HTTP/store/SSE tests, no provider calls or user data."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import events, reports, tasks
from app.agent.outcome import INTEGRITY_VERSION
from app.config import Settings
from app.database import Base, get_db
from app.security import require_api_key
from app.trace import store
from app.trace.events import TraceEventCursor, build_incremental_events


class R4WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.run = store.create_agent_run(self.db, "Read local fixture", "summary", "real")
        self.plan = {"version": "r4", "task": "Read local fixture", "source_mode": "real",
                     "execution_mode": "planned", "allowed_tools": ["file_reader"], "notes": [],
                     "steps": [{"step_no": 1, "tool_name": "file_reader", "goal": "Read",
                                "arguments": {"path": "fixture.md"}, "requires_confirmation": True}]}
        store.update_agent_run_plan(self.db, self.run.run_id, self.plan)
        app = FastAPI()
        app.include_router(tasks.router, prefix="/api")
        app.include_router(reports.router, prefix="/api")
        app.include_router(events.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_api_key] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def endpoint(self, suffix=""):
        return f"/api/tasks/{self.run.run_id}{suffix}"

    def test_async_confirmation_claims_once_and_schedules_without_sync_execution(self):
        store.update_agent_run_status(self.db, self.run.run_id, "waiting_human")
        with patch.object(tasks, "_run_task_in_background") as background, patch.object(tasks, "run_task_by_mode") as execute:
            response = self.client.post(self.endpoint("/confirm?start_async=true"), json={"approved": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "running")
            self.assertTrue(response.json()["resumed"])
            background.assert_called_once_with(self.run.run_id)
            execute.assert_not_called()
            duplicate = self.client.post(self.endpoint("/confirm?start_async=true"), json={"approved": True})
            self.assertIn(duplicate.status_code, (400, 409))
            background.assert_called_once()

    def test_async_confirmation_rechecks_missing_configuration_before_claim(self):
        store.replace_agent_run_plan(self.db, self.run.run_id, {**self.plan, "allowed_tools": ["tavily_search"],
            "steps": [{"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "test"}, "requires_confirmation": True}]})
        store.update_agent_run_status(self.db, self.run.run_id, "waiting_human")
        with patch.object(tasks, "settings", Settings(offline_mode=False, tavily_api_key=None)), patch.object(tasks, "_run_task_in_background") as background:
            response = self.client.post(self.endpoint("/confirm?start_async=true"), json={"approved": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_agent_run(self.db, self.run.run_id).status, "waiting_human")
        background.assert_not_called()

    def test_rejection_never_schedules_background_work(self):
        store.update_agent_run_status(self.db, self.run.run_id, "waiting_human")
        with patch.object(tasks, "_run_task_in_background") as background:
            response = self.client.post(self.endpoint("/confirm?start_async=true"), json={"approved": False})
        self.assertEqual(response.json()["status"], "failed")
        background.assert_not_called()

    def test_cancel_and_full_retry_preserve_original_and_do_not_execute(self):
        with patch.object(tasks, "_run_task_in_background") as background, patch.object(tasks, "run_task_by_mode") as execute:
            self.assertEqual(self.client.post(self.endpoint("/cancel"), json={"reason": "fixture"}).json()["status"], "cancelled")
            response = self.client.post(self.endpoint("/retry"), json={"reuse_plan": True, "from_failed_step": False})
            self.assertEqual(response.status_code, 200, response.text)
            new_id = response.json()["run_id"]
            self.assertNotEqual(new_id, self.run.run_id)
            self.assertEqual(store.get_agent_run(self.db, self.run.run_id).status, "cancelled")
            plan = json.loads(store.get_agent_run(self.db, new_id).plan_json)
            self.assertEqual(plan["parent_run_id"], self.run.run_id)
            self.assertNotIn("confirmation", plan)
            background.assert_not_called(); execute.assert_not_called()

    def test_report_not_generated_has_no_fake_markdown(self):
        data = self.client.get(f"/api/reports/{self.run.run_id}").json()
        self.assertEqual(data["availability"], "not_generated")
        self.assertEqual(data["markdown"], "")
        self.assertFalse(data["exists"])

    def test_recorded_missing_report_is_distinct(self):
        store.update_agent_run_status(self.db, self.run.run_id, "completed")
        store.update_agent_run_report(self.db, self.run.run_id, f"workspace/reports/missing-{self.run.run_id}.md")
        data = self.client.get(f"/api/reports/{self.run.run_id}").json()
        self.assertEqual(data["availability"], "missing")
        self.assertEqual(self.client.get(f"/api/reports/{self.run.run_id}/download").status_code, 404)

    def test_report_path_cannot_read_outside_reports_root(self):
        store.update_agent_run_status(self.db, self.run.run_id, "completed")
        store.update_agent_run_report(self.db, self.run.run_id, "README.md")
        response = self.client.get(f"/api/reports/{self.run.run_id}")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("Traceable Research Agent", response.text)

    def test_failed_intermediate_report_is_blocked(self):
        plan = {**self.plan, "research_outcome": {"version": INTEGRITY_VERSION, "status": "failed"}}
        store.replace_agent_run_plan(self.db, self.run.run_id, plan)
        store.update_agent_run_status(self.db, self.run.run_id, "failed")
        response = self.client.get(f"/api/reports/{self.run.run_id}")
        self.assertEqual(response.json()["availability"], "blocked")
        self.assertEqual(self.client.get(f"/api/reports/{self.run.run_id}/download").status_code, 409)

    def test_readable_report_returns_available_without_claiming_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fixture.md"
            file.write_text("# Fixture\nEvidence [CIT-001-01]", encoding="utf-8")
            store.update_agent_run_report(self.db, self.run.run_id, "workspace/reports/fixture.md")
            store.update_agent_run_status(self.db, self.run.run_id, "completed")
            with patch.object(reports, "resolve_report_path", return_value=file):
                data = self.client.get(f"/api/reports/{self.run.run_id}").json()
        self.assertTrue(data["exists"])
        self.assertTrue(data["requires_review"])
        self.assertEqual(data["availability"], "available")

    def test_deepening_intermediate_success_is_not_a_final_report(self):
        for flag in ("deepening_pending", "adaptive_gate_pending"):
            with self.subTest(flag=flag):
                store.replace_agent_run_plan(self.db, self.run.run_id, {**self.plan, flag: True,
                    "research_outcome": {"version": INTEGRITY_VERSION, "status": "passed"}})
                store.update_agent_run_status(self.db, self.run.run_id, "completed")
                store.update_agent_run_report(self.db, self.run.run_id, "workspace/reports/intermediate.md")
                data = self.client.get(f"/api/reports/{self.run.run_id}").json()
                self.assertEqual(data["availability"], "blocked")
                self.assertEqual(self.client.get(f"/api/reports/{self.run.run_id}/download").status_code, 409)
                output, _ = build_incremental_events(self.db, self.run.run_id, TraceEventCursor())
                self.assertNotIn("report_ready", [event["event_type"] for event in output])

    def test_legacy_failed_report_is_audit_only(self):
        store.update_agent_run_status(self.db, self.run.run_id, "failed")
        store.update_agent_run_report(self.db, self.run.run_id, "workspace/reports/legacy.md")
        self.assertEqual(self.client.get(f"/api/reports/{self.run.run_id}").json()["availability"], "blocked")

    def test_nginx_flushes_sse_and_keeps_nested_spa_routes(self):
        config = (Path(__file__).resolve().parents[1] / "web/nginx.conf").read_text()
        self.assertIn("proxy_buffering off", config)
        self.assertIn("proxy_read_timeout 3600s", config)
        self.assertIn("try_files $uri $uri/ /index.html", config)

    def test_pagination_filters_all_rows_not_only_first_page(self):
        for index in range(24):
            item = store.create_agent_run(self.db, f"MATCH {index:02}", "summary", "real")
            store.update_agent_run_status(self.db, item.run_id, "failed")
        first = self.client.get("/api/tasks?limit=20&status=failed&q=match").json()
        second = self.client.get("/api/tasks?limit=20&offset=20&status=failed&q=match").json()
        self.assertEqual(first["total"], 24)
        self.assertEqual(len(first["tasks"]), 20)
        self.assertEqual(len(second["tasks"]), 4)
        self.assertFalse({r["run_id"] for r in first["tasks"]} & {r["run_id"] for r in second["tasks"]})

    def test_literal_search_escapes_wildcards_and_matches_id(self):
        store.create_agent_run(self.db, "100%_literal", "summary", "real")
        self.assertEqual(self.client.get("/api/tasks", params={"q": "%_"}).json()["total"], 1)
        self.assertEqual(self.client.get("/api/tasks", params={"q": self.run.run_id}).json()["total"], 1)

    def test_waiting_alias_and_visible_running_state_match_counts(self):
        store.update_agent_run_status(self.db, self.run.run_id, "waiting_human")
        item = store.create_agent_run(self.db, "Review", "summary", "real")
        store.update_agent_run_status(self.db, item.run_id, "waiting_human_plan")
        self.assertEqual(self.client.get("/api/tasks?status=waiting").json()["total"], 2)
        store.replace_agent_run_plan(self.db, item.run_id, {"deepening_pending": True})
        store.update_agent_run_status(self.db, item.run_id, "completed")
        data = self.client.get("/api/tasks?status=running").json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["tasks"][0]["status"], "running")
        self.assertEqual(self.client.get("/api/tasks?status=completed").json()["total"], 0)

    def test_sse_updates_a_previously_seen_running_trace_once(self):
        store.update_agent_run_status(self.db, self.run.run_id, "running")
        trace = store.create_tool_trace(self.db, self.run.run_id, 1, "file_reader", "running")
        cursor = TraceEventCursor()
        first, _ = build_incremental_events(self.db, self.run.run_id, cursor)
        self.assertTrue(any(event["event_type"] == "trace_created" for event in first))
        trace.status = "success"; trace.finished_at = datetime.now(timezone.utc)
        self.db.commit()
        second, _ = build_incremental_events(self.db, self.run.run_id, cursor)
        self.assertEqual([event["event_type"] for event in second], ["trace_finished"])
        self.assertEqual(build_incremental_events(self.db, self.run.run_id, cursor)[0], [])

    def test_sse_resume_replays_unfinished_trace_but_not_acknowledged_finished_rows(self):
        finished = store.create_tool_trace(self.db, self.run.run_id, 1, "file_reader", "success")
        finished.finished_at = datetime.now(timezone.utc); self.db.commit()
        running = store.create_tool_trace(self.db, self.run.run_id, 2, "file_reader", "running")
        cursor = TraceEventCursor()
        self.assertTrue(events._seed_cursor_after_event_id(self.db, self.run.run_id, cursor, running.trace_id))
        output, _ = build_incremental_events(self.db, self.run.run_id, cursor)
        self.assertEqual([event["trace_id"] for event in output if event.get("trace_id")], [running.trace_id])

    def test_terminal_sse_replays_persisted_failure_and_closes(self):
        store.update_agent_run_status(self.db, self.run.run_id, "failed", "No evidence")
        with patch.object(events, "SessionLocal", sessionmaker(bind=self.engine)):
            response = self.client.get(self.endpoint("/events"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: done", response.text)
        self.assertIn('"status": "failed"', response.text)
        self.assertNotIn("event: report_ready", response.text)


if __name__ == "__main__":
    unittest.main()
