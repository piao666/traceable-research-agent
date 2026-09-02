"""Single-instance session and memory contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.trace import models as trace_models  # noqa: F401


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def truncate(db: Session) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Other suites may register additional models after module collection.
        Base.metadata.create_all(engine)
        self.db = SessionLocal()
        truncate(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_session_is_global_and_tracks_turns(self) -> None:
        from app.memory.store import create_chat_turn, create_session, list_chat_turns, list_sessions

        session = create_session(self.db, title="Research")
        create_chat_turn(self.db, session.session_id, "user", "question")
        create_chat_turn(self.db, session.session_id, "agent", "answer", run_id="run-1")
        self.assertEqual([item.session_id for item in list_sessions(self.db)], [session.session_id])
        self.assertEqual([turn.role for turn in list_chat_turns(self.db, session.session_id)], ["user", "agent"])
        self.assertNotIn("tenant_id", session.__table__.columns)
        self.assertNotIn("user_id", session.__table__.columns)

    def test_memory_lifecycle_and_global_listing(self) -> None:
        from app.memory.store import create_user_memory, list_user_memories, update_memory_status

        memory = create_user_memory(self.db, "preference", "rule", "Prefers Chinese reports")
        self.assertEqual(memory.status, "pending")
        update_memory_status(self.db, memory.memory_id, "active")
        self.assertEqual([item.memory_id for item in list_user_memories(self.db, status="active")], [memory.memory_id])
        self.assertNotIn("tenant_id", memory.__table__.columns)
        self.assertNotIn("user_id", memory.__table__.columns)

    def test_delete_all_memories(self) -> None:
        from app.memory.store import create_user_memory, delete_all_user_memories, list_user_memories

        create_user_memory(self.db, "fact", "rule", "A")
        create_user_memory(self.db, "fact", "rule", "B")
        self.assertEqual(delete_all_user_memories(self.db), 2)
        self.assertEqual(list_user_memories(self.db), [])

    def test_expire_memories(self) -> None:
        from app.memory.store import create_user_memory, expire_memories, update_memory_status

        memory = create_user_memory(
            self.db,
            "fact",
            "rule",
            "Temporary",
            valid_until=datetime.now(timezone.utc) - timedelta(days=1),
        )
        update_memory_status(self.db, memory.memory_id, "active")
        self.assertEqual(expire_memories(self.db), 1)
        self.db.refresh(memory)
        self.assertEqual(memory.status, "expired")

    def test_keyword_recall_only_returns_active_matches(self) -> None:
        from app.memory.retriever import retrieve_memories
        from app.memory.store import create_user_memory, update_memory_status

        active = create_user_memory(self.db, "preference", "rule", "Prefers Chinese reports")
        update_memory_status(self.db, active.memory_id, "active")
        create_user_memory(self.db, "preference", "rule", "Pending Chinese preference")
        results = retrieve_memories(self.db, "Chinese reports")
        self.assertEqual([item.memory_id for item in results], [active.memory_id])

    def test_injection_respects_budget(self) -> None:
        from app.memory.retriever import retrieve_for_injection
        from app.memory.store import create_user_memory, update_memory_status

        for index in range(4):
            memory = create_user_memory(self.db, "fact", "rule", f"option {index} " + "x" * 250)
            update_memory_status(self.db, memory.memory_id, "active")
        selected, context = retrieve_for_injection(self.db, "option", max_chars=550)
        self.assertLessEqual(sum(len(item.content) for item in selected), 550)
        self.assertIn("User Context", context)

    def test_sample_threshold_creates_one_pending_memory(self) -> None:
        from app.memory.extractor import commit_pending_memories
        from app.memory.store import list_user_memories
        from app.trace.store import create_agent_run

        runs = []
        for task in ("Research LLM systems", "Compare LLM systems"):
            run = create_agent_run(self.db, task, "summary", "mock")
            run.status = "completed"
            self.db.commit()
            runs.append(run)
        candidate = {
            "kind": "interest",
            "extraction_method": "rule",
            "content": "User researches LLM",
            "source_run_id": runs[-1].run_id,
        }
        self.assertEqual(commit_pending_memories(self.db, runs[-1], [candidate]), 1)
        self.assertEqual(len(list_user_memories(self.db, status="pending")), 1)

    def test_rule_extraction_generates_chinese_memory_content(self) -> None:
        from app.memory.extractor import extract_preferences_from_run
        from app.trace.store import create_agent_run

        run = create_agent_run(self.db, "用中文调研 LLM 并生成 PDF 报告", "summary", "mock")
        candidates = extract_preferences_from_run(self.db, run)
        contents = {item["content"] for item in candidates}

        self.assertIn("偏好使用中文研究报告", contents)
        self.assertIn("偏好 PDF 报告格式", contents)
        self.assertIn("经常调研：LLM", contents)

    def test_after_run_completion_adds_agent_turn(self) -> None:
        from app.agent.executor import _after_run_completed
        from app.memory.store import create_session, list_chat_turns
        from app.trace.store import create_agent_run

        session = create_session(self.db, title="Test")
        run = create_agent_run(self.db, "task", "summary", "mock", session_id=session.session_id)
        _after_run_completed(self.db, run, "# Report\n\nBody", 0)
        turns = list_chat_turns(self.db, session.session_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].role, "agent")


if __name__ == "__main__":
    unittest.main()
