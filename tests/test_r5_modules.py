"""R5 isolated API, audit/expiry and migration tests; no provider requests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import memory, sessions, tasks, runtime, skills, tools
from app.database import Base, get_db
from app.config import Settings
from app.improvement import api as improvement
from app.memory import store
from app.memory.models import UserMemory, MemoryAuditEvent
from app.memory.retriever import retrieve_memories
from app.trace.models import AgentRun
from app.security import require_api_key
from app.skills.models import SkillMeta


class R5ModulesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.app = FastAPI()
        for router in (memory.router, sessions.router, tasks.router, runtime.router, skills.router, tools.router, improvement.router):
            self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[require_api_key] = lambda: None
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close(); self.db.close(); self.engine.dispose()

    def add_memory(self, status="pending", expired=False):
        m = store.create_user_memory(self.db, "preference", "rule", "private content",
            valid_until=datetime.now(timezone.utc) - timedelta(days=1) if expired else None)
        if status != "pending":
            store.update_memory_status(self.db, m.memory_id, status)
        return m

    def test_empty_modules_are_truthful(self):
        self.assertEqual(self.client.get("/api/sessions").json(), [])
        self.assertEqual(self.client.get("/api/memory").json(), {"memories": [], "total": 0, "active_count": 0, "pending_count": 0})
        self.assertEqual(self.client.get("/api/memory/audit").json(), [])
        self.assertEqual(self.client.get("/api/improvement/stats").json()["total_runs"], 0)
        self.assertEqual(self.client.get("/api/improvement/runs/missing").status_code, 404)

    def test_session_followup_records_question_and_filters_runs(self):
        first = self.client.post("/api/sessions", json={"title": "First"}).json()
        second = self.client.post("/api/sessions", json={"title": "Second"}).json()
        plan = {"steps": [], "execution_mode": "planned", "allowed_tools": []}
        with patch.object(tasks, "plan_task_for_review", return_value=plan), patch.object(tasks, "_run_task_in_background") as execute:
            response = self.client.post("/api/tasks", json={"task": "follow up", "session_id": first["session_id"], "require_plan_approval": True})
            self.assertEqual(response.status_code, 200, response.text)
            execute.assert_not_called()
        detail = self.client.get(f'/api/sessions/{first["session_id"]}').json()
        self.assertEqual(len(detail["turns"]), 1)
        self.assertEqual(detail["turns"][0]["content"], "follow up")
        self.assertEqual(detail["turns"][0]["run_id"], response.json()["run_id"])
        self.assertEqual(self.client.get(f'/api/tasks?session_id={first["session_id"]}').json()["total"], 1)
        self.assertEqual(self.client.get(f'/api/tasks?session_id={second["session_id"]}').json()["total"], 0)
        renamed = self.client.patch(f'/api/sessions/{first["session_id"]}', json={"title": "Renamed"}).json()
        self.assertEqual(renamed["turn_count"], 1)
        self.assertEqual(renamed["title"], "Renamed")

    def test_unknown_session_does_not_create_or_plan(self):
        with patch.object(tasks, "plan_task_for_review") as plan:
            response = self.client.post("/api/tasks", json={"task": "x", "session_id": "unknown", "require_plan_approval": True})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(self.db.scalars(select(AgentRun))), [])
        plan.assert_not_called()

    def test_missing_session_read_and_rename_return_404(self):
        self.assertEqual(self.client.get("/api/sessions/missing").status_code, 404)
        self.assertEqual(self.client.patch("/api/sessions/missing", json={"title": "x"}).status_code, 404)

    def test_expiry_is_read_only_and_excluded_from_recall(self):
        expired = self.add_memory("active", True)
        self.add_memory("pending", True)
        self.add_memory("active")
        payload = self.client.get("/api/memory").json()
        self.assertEqual(payload["active_count"], 1)
        self.assertEqual(payload["pending_count"], 0)
        self.assertEqual(len(self.client.get("/api/memory?status=expired").json()["memories"]), 2)
        self.assertEqual(len(retrieve_memories(self.db, "private")), 1)
        self.db.refresh(expired)
        self.assertEqual(expired.status, "active", "GET must not rewrite stored historical state")
        self.assertEqual(self.client.get("/api/memory/audit").json(), [])

    def test_expired_pending_cannot_be_activated(self):
        m = self.add_memory(expired=True)
        self.assertEqual(self.client.post(f"/api/memory/{m.memory_id}/confirm", json={"approved": True}).status_code, 400)
        self.assertEqual(self.client.get("/api/memory/audit").json(), [])

    def test_approve_is_once_and_audited_without_content(self):
        m = self.add_memory()
        path = f"/api/memory/{m.memory_id}/confirm"
        self.assertEqual(self.client.post(path, json={"approved": True}).json()["status"], "active")
        self.assertEqual(self.client.post(path, json={"approved": True}).status_code, 400)
        records = self.client.get("/api/memory/audit").json()
        self.assertEqual(len(records), 1); self.assertEqual(records[0]["action"], "confirm")
        self.assertNotIn("private content", str(records))

    def test_stale_pending_claim_fails_without_audit(self):
        m = self.add_memory("active")
        self.assertFalse(store.decide_pending_memory(self.db, m.memory_id, True))
        self.assertEqual(list(self.db.scalars(select(MemoryAuditEvent))), [])

    def test_reject_deletes_and_audits(self):
        m = self.add_memory(); id_ = m.memory_id
        response = self.client.post(f"/api/memory/{id_}/confirm", json={"approved": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "deleted")
        self.assertIsNone(self.db.get(UserMemory, id_))
        self.assertEqual(self.client.get("/api/memory/audit").json()[0]["action"], "reject")

    def test_delete_and_clear_all_statuses_preserve_sessions(self):
        session = store.create_session(self.db, "keep")
        first = self.add_memory(); id_ = first.memory_id
        self.add_memory("active"); self.add_memory("superseded"); self.add_memory("expired")
        self.assertEqual(self.client.delete(f"/api/memory/{id_}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/memory/{id_}").status_code, 404)
        self.assertEqual(self.client.delete("/api/memory").json()["count"], 3)
        self.assertEqual(self.client.get("/api/memory").json()["total"], 0)
        self.assertEqual(self.client.get(f"/api/sessions/{session.session_id}").status_code, 200)
        audit = self.client.get("/api/memory/audit").json()
        self.assertEqual({r["action"] for r in audit}, {"delete", "clear"})

    def test_audit_failure_rolls_back_destructive_action(self):
        m = self.add_memory(); id_ = m.memory_id
        with patch.object(store, "_audit", side_effect=RuntimeError("fixture")):
            self.assertEqual(self.client.delete(f"/api/memory/{id_}").status_code, 500)
            self.assertEqual(self.client.delete("/api/memory").status_code, 500)
            self.assertEqual(self.client.post(f"/api/memory/{id_}/confirm", json={"approved": True}).status_code, 500)
        self.assertEqual(self.db.get(UserMemory, id_).status, "pending")

    def test_memory_filter_and_audit_limit_validation(self):
        self.assertEqual(self.client.get("/api/memory?status=madeup").status_code, 422)
        self.assertEqual(self.client.get("/api/memory/audit?limit=10000").status_code, 422)

    def test_runtime_discloses_local_presence_not_keys_or_connectivity(self):
        secret = "DO-NOT-LEAK-fixture"
        settings = Settings(tavily_api_key=secret, mcp_remote_servers=f"https://{secret}@invalid")
        with patch.object(runtime, "settings", settings):
            response = self.client.get("/api/runtime/diagnostics")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, response.text)
        payload = response.json()
        self.assertTrue(payload["capabilities"]["tavily_configured"])
        self.assertFalse(payload["capabilities"]["connectivity_verified"])
        self.assertEqual(next(c for c in payload["checks"] if c["name"] == "database")["status"], "ok")

    def test_runtime_missing_schema_and_workspace_are_not_healthy(self):
        self.db.execute(text("DROP TABLE memory_audit_events")); self.db.commit()
        with patch.object(runtime, "WORKSPACE_DIR", Path("/nonexistent-r5-fixture")):
            payload = self.client.get("/api/runtime/diagnostics").json()
        self.assertEqual({c["name"] for c in payload["checks"] if c["status"] == "error"}, {"database", "workspace"})

    def test_runtime_database_error_does_not_expose_exception(self):
        with patch.object(self.db, "execute", side_effect=RuntimeError("secret-database-path")):
            response = self.client.get("/api/runtime/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret-database-path", response.text)
        self.assertEqual(next(c for c in response.json()["checks"] if c["name"] == "database")["status"], "error")

    def test_skill_list_is_typed_and_does_not_execute(self):
        meta = SkillMeta(name="fixture", version="1", description="local", required_tools=["file_reader"], parameters={})
        with patch.object(skills, "list_skills", return_value=[meta]):
            response = self.client.get("/api/skills")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["skills"][0]["required_tools"], ["file_reader"])


class R5MigrationTests(unittest.TestCase):
    def test_memory_delete_openapi_responses_are_named_contracts(self):
        app = FastAPI()
        app.include_router(memory.router, prefix="/api")
        schema = app.openapi()
        for path, name in [("/api/memory/{memory_id}", "MemoryDeleteResponse"), ("/api/memory", "MemoryClearResponse")]:
            response = schema["paths"][path]["delete"]["responses"]["200"]["content"]["application/json"]["schema"]
            self.assertEqual(response["$ref"], f"#/components/schemas/{name}")

    def test_upgrade_preserves_data_and_second_start_is_idempotent(self):
        from scripts import migrate_database as migration
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{directory}/fixture.sqlite")
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "migrations"))
            config.set_main_option("sqlalchemy.url", str(engine.url))
            command.upgrade(config, "0009_improvement_log")
            with Session(engine) as db:
                item = store.create_user_memory(db, "preference", "rule", "preserve")
                id_ = item.memory_id
            with patch.object(migration, "engine", engine):
                migration.migrate_database()
                migration.migrate_database()
            self.assertIn("memory_audit_events", inspect(engine).get_table_names())
            with Session(engine) as db:
                self.assertEqual(db.get(UserMemory, id_).content, "preserve")
                self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "0011_run_budgets")
                self.assertIn("run_budgets", inspect(engine).get_table_names())
            engine.dispose()
