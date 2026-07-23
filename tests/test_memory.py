"""Offline unit tests for memory module models, policy, and store."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base


# ── Test database ────────────────────────────────────────────────────

engine = create_engine("sqlite:///:memory:", echo=False)
SessionLocal = sessionmaker(bind=engine)


def _setup_tables() -> None:
    from app.trace import models  # noqa: F401
    from app.evidence import models as evidence_models  # noqa: F401
    from app.memory import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def _truncate_all(db: Session) -> None:
    """Remove all rows from memory tables between test classes."""
    for table in reversed(Base.metadata.sorted_tables):
        try:
            db.execute(table.delete())
        except Exception:
            pass
    db.commit()


_setup_tables()


# ── Model Tests ──────────────────────────────────────────────────────

class MemoryModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_create_session_defaults(self) -> None:
        from app.memory.store import create_session

        session = create_session(self.db, "demo", "user-1")
        self.assertIsNotNone(session.session_id)
        self.assertEqual(session.tenant_id, "demo")
        self.assertEqual(session.user_id, "user-1")
        self.assertIsNone(session.title)

    def test_create_session_with_title(self) -> None:
        from app.memory.store import create_session

        session = create_session(self.db, "demo", "user-1", title="Research on RAG")
        self.assertEqual(session.title, "Research on RAG")

    def test_create_chat_turn(self) -> None:
        from app.memory.store import create_session, create_chat_turn

        session = create_session(self.db, "demo", "user-1")
        turn = create_chat_turn(
            self.db, session.session_id, "user", "test query", run_id="abc123"
        )
        self.assertEqual(turn.role, "user")
        self.assertEqual(turn.content, "test query")
        self.assertEqual(turn.run_id, "abc123")

    def test_create_user_memory_defaults(self) -> None:
        from app.memory.store import create_user_memory

        memory = create_user_memory(
            self.db,
            tenant_id="demo",
            user_id="user-1",
            kind="preference",
            extraction_method="rule",
            content="User prefers Chinese reports",
        )
        self.assertEqual(memory.status, "pending")
        self.assertEqual(memory.confidence, 0.5)
        self.assertEqual(memory.extraction_method, "rule")

    def test_user_memory_has_all_columns(self) -> None:
        from app.memory.store import create_user_memory

        memory = create_user_memory(
            self.db,
            tenant_id="demo",
            user_id="user-1",
            kind="fact",
            extraction_method="llm",
            content="User uses Python 3.12",
            confidence=0.8,
        )
        columns = [c.name for c in memory.__table__.columns]
        for col in [
            "memory_id", "tenant_id", "user_id", "kind",
            "extraction_method", "content", "confidence",
            "status", "source_session_id", "source_run_id",
            "valid_until", "created_at", "updated_at",
        ]:
            self.assertIn(col, columns, f"Column {col} missing from UserMemory")

    def test_conversation_session_has_all_columns(self) -> None:
        from app.memory.store import create_session

        session = create_session(self.db, "demo", "user-1")
        columns = [c.name for c in session.__table__.columns]
        for col in ["session_id", "tenant_id", "user_id", "title", "created_at", "updated_at"]:
            self.assertIn(col, columns)

    def test_chat_turn_has_all_columns(self) -> None:
        from app.memory.store import create_session, create_chat_turn

        session = create_session(self.db, "demo", "user-1")
        turn = create_chat_turn(self.db, session.session_id, "agent", "response")
        columns = [c.name for c in turn.__table__.columns]
        for col in ["turn_id", "session_id", "role", "content", "run_id", "created_at"]:
            self.assertIn(col, columns)


# ── Store Tests ──────────────────────────────────────────────────────

class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_list_sessions_by_tenant_user(self) -> None:
        from app.memory.store import create_session, list_sessions

        create_session(self.db, "demo", "user-1", title="A")
        create_session(self.db, "demo", "user-1", title="B")
        create_session(self.db, "demo", "user-2", title="C")

        u1_sessions = list_sessions(self.db, "demo", "user-1")
        u2_sessions = list_sessions(self.db, "demo", "user-2")

        self.assertEqual(len(u1_sessions), 2)
        self.assertEqual(len(u2_sessions), 1)
        self.assertEqual(u2_sessions[0].title, "C")

    def test_list_chat_turns_chronological(self) -> None:
        from app.memory.store import create_session, create_chat_turn, list_chat_turns

        session = create_session(self.db, "demo", "user-1")
        create_chat_turn(self.db, session.session_id, "user", "first")
        create_chat_turn(self.db, session.session_id, "agent", "second")
        create_chat_turn(self.db, session.session_id, "user", "third")

        turns = list_chat_turns(self.db, session.session_id)
        self.assertEqual(len(turns), 3)
        self.assertTrue(turns[0].created_at <= turns[1].created_at <= turns[2].created_at)

    def test_memory_status_lifecycle(self) -> None:
        from app.memory.store import (
            create_user_memory,
            update_memory_status,
            get_user_memory,
        )

        memory = create_user_memory(
            self.db, "demo", "user-1", "preference", "rule", "test",
        )
        self.assertEqual(memory.status, "pending")

        activated = update_memory_status(self.db, memory.memory_id, "active")
        self.assertEqual(activated.status, "active")

        superseded = update_memory_status(self.db, memory.memory_id, "superseded")
        self.assertEqual(superseded.status, "superseded")

        fetched = get_user_memory(self.db, memory.memory_id)
        self.assertEqual(fetched.status, "superseded")

    def test_supersede_memory(self) -> None:
        from app.memory.store import create_user_memory, supersede_memory

        memory = create_user_memory(
            self.db, "demo", "user-1", "fact", "rule", "test",
        )
        superseded = supersede_memory(self.db, memory.memory_id)
        self.assertEqual(superseded.status, "superseded")

    def test_expire_memories(self) -> None:
        from app.memory.store import create_user_memory, update_memory_status, expire_memories

        # Create an active memory with past valid_until
        memory = create_user_memory(
            self.db, "demo", "user-1", "fact", "rule", "expired content",
        )
        update_memory_status(self.db, memory.memory_id, "active")

        # Manually set valid_until to the past
        from app.memory.models import UserMemory
        mem = self.db.get(UserMemory, memory.memory_id)
        mem.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
        self.db.commit()

        count = expire_memories(self.db)
        self.assertGreaterEqual(count, 1)

        # Verify status changed
        self.db.refresh(mem)
        self.assertEqual(mem.status, "expired")

    def test_list_memories_filter_by_status(self) -> None:
        from app.memory.store import create_user_memory, update_memory_status, list_user_memories

        m1 = create_user_memory(self.db, "demo", "user-1", "preference", "rule", "a")
        m2 = create_user_memory(self.db, "demo", "user-1", "preference", "rule", "b")
        update_memory_status(self.db, m2.memory_id, "active")

        pending = list_user_memories(self.db, "demo", "user-1", status="pending")
        active = list_user_memories(self.db, "demo", "user-1", status="active")
        all_mem = list_user_memories(self.db, "demo", "user-1")

        self.assertEqual(len(pending), 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(len(all_mem), 2)

    def test_delete_user_memory(self) -> None:
        from app.memory.store import (
            create_user_memory,
            delete_user_memory,
            get_user_memory,
        )

        memory = create_user_memory(
            self.db, "demo", "user-1", "fact", "rule", "to delete",
        )
        memory_id = memory.memory_id
        delete_user_memory(self.db, memory_id)
        self.assertIsNone(get_user_memory(self.db, memory_id))

    def test_delete_all_user_memories(self) -> None:
        from app.memory.store import (
            create_user_memory,
            delete_all_user_memories,
            list_user_memories,
        )

        create_user_memory(self.db, "demo", "user-1", "fact", "rule", "a")
        create_user_memory(self.db, "demo", "user-1", "fact", "rule", "b")
        create_user_memory(self.db, "demo", "user-2", "fact", "rule", "c")

        count = delete_all_user_memories(self.db, "demo", "user-1")
        self.assertEqual(count, 2)

        remaining = list_user_memories(self.db, "demo", "user-1")
        self.assertEqual(len(remaining), 0)

        other = list_user_memories(self.db, "demo", "user-2")
        self.assertEqual(len(other), 1)

    def test_update_session_title(self) -> None:
        from app.memory.store import create_session, update_session_title

        session = create_session(self.db, "demo", "user-1")
        updated = update_session_title(self.db, session.session_id, "New Title")
        self.assertEqual(updated.title, "New Title")

    def test_count_turns_for_session(self) -> None:
        from app.memory.store import (
            create_session,
            create_chat_turn,
            count_turns_for_session,
        )

        session = create_session(self.db, "demo", "user-1")
        self.assertEqual(count_turns_for_session(self.db, session.session_id), 0)

        create_chat_turn(self.db, session.session_id, "user", "hello")
        create_chat_turn(self.db, session.session_id, "agent", "hi")
        self.assertEqual(count_turns_for_session(self.db, session.session_id), 2)


# ── Policy Tests ─────────────────────────────────────────────────────

class MemoryPolicyTests(unittest.TestCase):
    def test_cold_start_no_injection(self) -> None:
        from app.memory.policy import should_inject_memory, format_memory_context

        self.assertFalse(should_inject_memory([]))
        self.assertEqual(format_memory_context([]), "")

    def test_format_memory_context(self) -> None:
        from app.memory.policy import format_memory_context
        from app.memory.models import UserMemory

        mem = UserMemory(
            memory_id="test-1",
            tenant_id="demo",
            user_id="user-1",
            kind="preference",
            extraction_method="rule",
            content="User prefers Chinese reports",
            confidence=0.8,
            status="active",
        )
        result = format_memory_context([mem])
        self.assertIn("User prefers Chinese reports", result)
        self.assertIn("Preference", result)
        self.assertIn("confidence: 0.8", result)

    def test_injection_budget_respected(self) -> None:
        from app.memory.policy import select_memories_for_injection
        from app.memory.models import UserMemory

        memories = [
            UserMemory(
                memory_id=f"mem-{i}",
                tenant_id="demo",
                user_id="user-1",
                kind="fact",
                extraction_method="rule",
                content="x" * 300,
                confidence=0.5,
                status="active",
            )
            for i in range(5)
        ]
        selected = select_memories_for_injection(memories, max_chars=600)
        self.assertLessEqual(len(selected), 2)
        total_chars = sum(len(m.content) for m in selected)
        self.assertLessEqual(total_chars, 600)

    def test_select_memories_sorted_by_recency_and_confidence(self) -> None:
        from app.memory.policy import select_memories_for_injection
        from app.memory.models import UserMemory

        now = datetime.now(timezone.utc)
        memories = [
            UserMemory(
                memory_id="old-high-conf",
                tenant_id="demo", user_id="u1",
                kind="fact", extraction_method="rule",
                content="old but confident",
                confidence=0.9,
                status="active",
                updated_at=now - timedelta(days=10),
            ),
            UserMemory(
                memory_id="new-low-conf",
                tenant_id="demo", user_id="u1",
                kind="fact", extraction_method="rule",
                content="new but low confidence",
                confidence=0.3,
                status="active",
                updated_at=now,
            ),
        ]
        selected = select_memories_for_injection(memories, max_chars=500)
        self.assertEqual(len(selected), 2)
        # Newer + higher confidence should come first
        self.assertEqual(selected[0].memory_id, "new-low-conf")

    def test_build_cold_start_trace_event(self) -> None:
        from app.memory.policy import build_cold_start_trace_event

        event = build_cold_start_trace_event()
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 0)
        self.assertEqual(event["reason"], "cold_start")

    def test_build_memory_recall_trace_event(self) -> None:
        from app.memory.policy import build_memory_recall_trace_event

        event = build_memory_recall_trace_event(
            recalled=3, injected_chars=450, memory_ids=["a", "b", "c"],
        )
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 3)
        self.assertEqual(event["injected_chars"], 450)
        self.assertEqual(event["reason"], None)
        self.assertEqual(len(event["memory_ids"]), 3)

    def test_build_injection_trimmed_trace_event(self) -> None:
        from app.memory.policy import build_memory_injection_trimmed_trace_event

        event = build_memory_injection_trimmed_trace_event(
            total=10, selected=4, injected_chars=780, max_chars=800,
        )
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 4)
        self.assertEqual(event["total_available"], 10)
        self.assertEqual(event["budget_max_chars"], 800)
        self.assertEqual(event["reason"], "budget_trimmed")

    def test_min_sample_threshold_constant_exists(self) -> None:
        from app.memory.policy import MIN_SAMPLE_THRESHOLD

        self.assertEqual(MIN_SAMPLE_THRESHOLD, 2)

    def test_max_injection_chars_constant_exists(self) -> None:
        from app.memory.policy import MAX_INJECTION_CHARS

        self.assertEqual(MAX_INJECTION_CHARS, 800)


# ── Integration: AgentRun columns ────────────────────────────────────

class AgentRunMemoryColumnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_create_agent_run_with_session_id_and_snapshot(self) -> None:
        from app.trace.store import create_agent_run, get_agent_run

        run = create_agent_run(
            self.db,
            task="test task",
            report_type="summary",
            source_mode="real",
            session_id="sess-123",
            run_config_snapshot='{"mode":"offline"}',
        )
        self.assertEqual(run.session_id, "sess-123")
        self.assertEqual(run.run_config_snapshot, '{"mode":"offline"}')

        fetched = get_agent_run(self.db, run.run_id)
        self.assertEqual(fetched.session_id, "sess-123")
        self.assertEqual(fetched.run_config_snapshot, '{"mode":"offline"}')

    def test_create_agent_run_without_session_id(self) -> None:
        from app.trace.store import create_agent_run

        run = create_agent_run(
            self.db,
            task="test task",
            report_type="summary",
            source_mode="real",
        )
        self.assertIsNone(run.session_id)
        self.assertIsNone(run.run_config_snapshot)


if __name__ == "__main__":
    unittest.main()
