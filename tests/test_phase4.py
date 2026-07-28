"""Tests for Phase 4: user profile extraction, memory retrieval, and integration."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
    for table in reversed(Base.metadata.sorted_tables):
        try:
            db.execute(table.delete())
        except Exception:
            pass
    db.commit()


_setup_tables()


def _make_run(
    db: Session,
    task: str = "test task",
    status: str = "completed",
    session_id: str | None = None,
    tenant_id: str = "demo",
    user_id: str = "user-1",
) -> "AgentRun":
    import json as _json
    from app.trace.store import create_agent_run

    run = create_agent_run(
        db,
        task=task,
        report_type="summary",
        source_mode="real",
        session_id=session_id,
        run_config_snapshot=_json.dumps({"tenant_id": tenant_id, "user_id": user_id}),
    )
    run.status = status
    db.commit()
    return run


# ── Extractor: Language Detection ────────────────────────────────────

class LanguageDetectionTests(unittest.TestCase):
    def test_detect_chinese_high_cjk_ratio(self) -> None:
        from app.memory.extractor import _detect_language_preference

        result = _detect_language_preference("请帮我调研一下Agent框架的最新进展和对比分析")
        self.assertEqual(result, "zh")

    def test_detect_english_pure_ascii(self) -> None:
        from app.memory.extractor import _detect_language_preference

        result = _detect_language_preference(
            "Please research the latest developments in multi-agent orchestration frameworks"
        )
        self.assertEqual(result, "en")

    def test_detect_mixed_returns_none(self) -> None:
        from app.memory.extractor import _detect_language_preference

        # Short mixed text with not enough CJK
        result = _detect_language_preference("Compare Agent vs 对比")
        self.assertIsNone(result)

    def test_detect_short_text_returns_none(self) -> None:
        from app.memory.extractor import _detect_language_preference

        result = _detect_language_preference("Hi")
        self.assertIsNone(result)


# ── Extractor: Format Detection ──────────────────────────────────────

class FormatDetectionTests(unittest.TestCase):
    def test_detect_word_format(self) -> None:
        from app.memory.extractor import _detect_format_preference

        self.assertEqual(_detect_format_preference("Export as Word document"), "word")
        self.assertEqual(_detect_format_preference("Save to .docx"), "word")

    def test_detect_pdf_format(self) -> None:
        from app.memory.extractor import _detect_format_preference

        self.assertEqual(_detect_format_preference("Generate PDF report"), "pdf")

    def test_detect_markdown_format(self) -> None:
        from app.memory.extractor import _detect_format_preference

        self.assertEqual(_detect_format_preference("Output as Markdown .md"), "markdown")

    def test_no_format_detected(self) -> None:
        from app.memory.extractor import _detect_format_preference

        self.assertIsNone(_detect_format_preference("Just do the research"))


# ── Extractor: Domain Keywords ───────────────────────────────────────

class DomainKeywordTests(unittest.TestCase):
    def test_detect_rag_keyword(self) -> None:
        from app.memory.extractor import _detect_domain_keywords

        kw = _detect_domain_keywords("Research RAG and retrieval methods")
        self.assertIn("RAG", kw)

    def test_detect_multiple_keywords(self) -> None:
        from app.memory.extractor import _detect_domain_keywords

        kw = _detect_domain_keywords("Compare LLM, RAG, and Agent frameworks")
        self.assertGreaterEqual(len(kw), 3)

    def test_no_keywords(self) -> None:
        from app.memory.extractor import _detect_domain_keywords

        kw = _detect_domain_keywords("Hello world")
        self.assertEqual(len(kw), 0)

    def test_keywords_deduplicated(self) -> None:
        from app.memory.extractor import _detect_domain_keywords

        kw = _detect_domain_keywords("RAG RAG RAG and more RAG")
        self.assertEqual(len(kw), 1)


# ── Extractor: Main extraction ───────────────────────────────────────

class ExtractPreferencesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_extract_language_preference(self) -> None:
        from app.memory.extractor import extract_preferences_from_run

        run = _make_run(self.db, task="请帮我调研RAG技术的最新进展")
        candidates = extract_preferences_from_run(self.db, run, "demo", "user-1")
        contents = [c["content"] for c in candidates]
        self.assertTrue(any("Chinese" in c for c in contents))

    def test_extract_format_preference(self) -> None:
        from app.memory.extractor import extract_preferences_from_run

        run = _make_run(self.db, task="Research and export as Markdown")
        candidates = extract_preferences_from_run(self.db, run, "demo", "user-1")
        contents = [c["content"] for c in candidates]
        self.assertTrue(any("MARKDOWN" in c for c in contents))

    def test_extract_interest_keywords(self) -> None:
        from app.memory.extractor import extract_preferences_from_run

        run = _make_run(self.db, task="Research RAG and LLM evaluation benchmarks")
        candidates = extract_preferences_from_run(self.db, run, "demo", "user-1")
        interest_candidates = [c for c in candidates if c["kind"] == "interest"]
        self.assertGreaterEqual(len(interest_candidates), 1)

    def test_extract_empty_task(self) -> None:
        from app.memory.extractor import extract_preferences_from_run

        run = _make_run(self.db, task="")
        candidates = extract_preferences_from_run(self.db, run, "demo", "user-1")
        self.assertEqual(len(candidates), 0)


# ── Extractor: Sample threshold ──────────────────────────────────────

class CommitPendingMemoriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_first_signal_not_committed(self) -> None:
        """A single run's signal should NOT produce a pending memory (<2 runs)."""
        from app.memory.extractor import commit_pending_memories
        from app.memory.store import list_user_memories

        run = _make_run(self.db, task="请帮我调研RAG技术")
        candidates = [{
            "kind": "interest",
            "extraction_method": "rule",
            "content": "User researches RAG",
            "confidence": 0.5,
            "source_run_id": run.run_id,
        }]
        count = commit_pending_memories(self.db, "demo", "user-1", run, candidates)
        self.assertEqual(count, 0)
        memories = list_user_memories(self.db, "demo", "user-1")
        self.assertEqual(len(memories), 0)

    def test_second_signal_committed(self) -> None:
        """Same signal from 2 distinct runs should produce pending memory."""
        from app.memory.extractor import commit_pending_memories
        from app.memory.store import list_user_memories

        # First run — no memory created
        run1 = _make_run(self.db, task="Research RAG", status="completed")
        candidates1 = [{
            "kind": "interest", "extraction_method": "rule",
            "content": "User researches RAG", "confidence": 0.5,
            "source_run_id": run1.run_id,
        }]
        commit_pending_memories(self.db, "demo", "user-1", run1, candidates1)

        # Second run — same signal, should commit
        run2 = _make_run(self.db, task="More RAG research", status="completed")
        candidates2 = [{
            "kind": "interest", "extraction_method": "rule",
            "content": "User researches RAG", "confidence": 0.5,
            "source_run_id": run2.run_id,
        }]
        count = commit_pending_memories(self.db, "demo", "user-1", run2, candidates2)
        self.assertEqual(count, 1)

        memories = list_user_memories(self.db, "demo", "user-1")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].status, "pending")
        self.assertEqual(memories[0].kind, "interest")

    def test_already_active_skipped(self) -> None:
        """Signal already active should not create duplicate."""
        from app.memory.extractor import commit_pending_memories
        from app.memory.store import create_user_memory, update_memory_status, list_user_memories

        # Pre-create an active memory
        mem = create_user_memory(
            self.db, "demo", "user-1", "interest", "rule",
            "User researches RAG", confidence=0.7,
        )
        update_memory_status(self.db, mem.memory_id, "active")

        run = _make_run(self.db, task="Research RAG again", status="completed")
        candidates = [{
            "kind": "interest", "extraction_method": "rule",
            "content": "User researches RAG", "confidence": 0.5,
            "source_run_id": run.run_id,
        }]
        count = commit_pending_memories(self.db, "demo", "user-1", run, candidates)
        self.assertEqual(count, 0)
        all_mem = list_user_memories(self.db, "demo", "user-1")
        self.assertEqual(len(all_mem), 1)  # only the active one

    def test_empty_candidates_returns_zero(self) -> None:
        from app.memory.extractor import commit_pending_memories

        run = _make_run(self.db, task="test", status="completed")
        count = commit_pending_memories(self.db, "demo", "user-1", run, [])
        self.assertEqual(count, 0)


# ── Extractor: should_extract_for_run ────────────────────────────────

class ShouldExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_no_completed_runs_returns_false(self) -> None:
        from app.memory.extractor import should_extract_for_run

        self.assertFalse(should_extract_for_run(self.db, "demo", "user-1"))

    def test_one_completed_run_returns_false(self) -> None:
        from app.memory.extractor import should_extract_for_run

        _make_run(self.db, status="completed")
        self.assertFalse(should_extract_for_run(self.db, "demo", "user-1"))

    def test_two_completed_runs_returns_true(self) -> None:
        from app.memory.extractor import should_extract_for_run

        _make_run(self.db, status="completed")
        _make_run(self.db, status="completed")
        self.assertTrue(should_extract_for_run(self.db, "demo", "user-1"))

    def test_count_completed_runs(self) -> None:
        from app.memory.extractor import count_completed_runs

        self.assertEqual(count_completed_runs(self.db, "demo", "user-1"), 0)
        _make_run(self.db, status="completed")
        _make_run(self.db, status="completed")
        _make_run(self.db, status="failed")
        self.assertEqual(count_completed_runs(self.db, "demo", "user-1"), 2)


# ── Retriever: keyword scoring ───────────────────────────────────────

class KeywordScoreTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        from app.memory.retriever import _keyword_score

        score = _keyword_score("RAG research", "User researches RAG")
        self.assertGreater(score, 0.0)

    def test_no_match(self) -> None:
        from app.memory.retriever import _keyword_score

        score = _keyword_score("RAG", "User prefers PDF reports")
        self.assertEqual(score, 0.0)

    def test_empty_query(self) -> None:
        from app.memory.retriever import _keyword_score

        score = _keyword_score("", "Some text")
        self.assertEqual(score, 0.0)


# ── Retriever: retrieve_memories ─────────────────────────────────────

class RetrieveMemoriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _create_active_memory(self, content: str, **kwargs) -> None:
        from app.memory.store import create_user_memory, update_memory_status

        mem = create_user_memory(
            self.db, "demo", "user-1",
            kind=kwargs.get("kind", "preference"),
            extraction_method="rule",
            content=content,
            confidence=kwargs.get("confidence", 0.5),
        )
        update_memory_status(self.db, mem.memory_id, "active")

    def test_retrieve_relevant_memory(self) -> None:
        from app.memory.retriever import retrieve_memories

        self._create_active_memory("User prefers Chinese reports")
        self._create_active_memory("User researches RAG and Agent frameworks")

        results = retrieve_memories(self.db, "demo", "user-1", "Chinese reports")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("Chinese", results[0].content)

    def test_no_active_memories_returns_empty(self) -> None:
        from app.memory.retriever import retrieve_memories

        results = retrieve_memories(self.db, "demo", "user-1", "anything")
        self.assertEqual(len(results), 0)

    def test_empty_query_returns_recent(self) -> None:
        from app.memory.retriever import retrieve_memories

        self._create_active_memory("Preference A")
        self._create_active_memory("Preference B")

        results = retrieve_memories(self.db, "demo", "user-1", "")
        self.assertGreaterEqual(len(results), 1)

    def test_top_k_respected(self) -> None:
        from app.memory.retriever import retrieve_memories

        for i in range(5):
            self._create_active_memory(f"User prefers option {i}")

        results = retrieve_memories(self.db, "demo", "user-1", "prefers", top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_only_active_memories_returned(self) -> None:
        from app.memory.retriever import retrieve_memories
        from app.memory.store import create_user_memory

        self._create_active_memory("Active memory")
        create_user_memory(self.db, "demo", "user-1", "preference", "rule", "Pending memory")

        results = retrieve_memories(self.db, "demo", "user-1", "memory")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content, "Active memory")


# ── Retriever: retrieve_for_injection ────────────────────────────────

class RetrieveForInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _create_active_memory(self, content: str) -> None:
        from app.memory.store import create_user_memory, update_memory_status

        mem = create_user_memory(
            self.db, "demo", "user-1", "preference", "rule", content,
        )
        update_memory_status(self.db, mem.memory_id, "active")

    def test_injection_returns_formatted_context(self) -> None:
        from app.memory.retriever import retrieve_for_injection

        self._create_active_memory("User prefers Chinese reports")

        selected, context = retrieve_for_injection(
            self.db, "demo", "user-1", "Chinese research",
        )
        self.assertGreaterEqual(len(selected), 1)
        self.assertIn("User Context", context)
        self.assertIn("Chinese reports", context)

    def test_no_memories_returns_empty(self) -> None:
        from app.memory.retriever import retrieve_for_injection

        selected, context = retrieve_for_injection(
            self.db, "demo", "user-1", "anything",
        )
        self.assertEqual(len(selected), 0)
        self.assertEqual(context, "")

    def test_budget_respected(self) -> None:
        from app.memory.retriever import retrieve_for_injection

        long_content = "x" * 500
        for i in range(5):
            self._create_active_memory(f"{long_content} option {i}")

        selected, context = retrieve_for_injection(
            self.db, "demo", "user-1", "option", max_chars=600,
        )
        total_chars = sum(len(m.content) for m in selected)
        self.assertLessEqual(total_chars, 600)


# ── memory_search handler ────────────────────────────────────────────

class MemorySearchHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _create_active_memory(self, content: str) -> None:
        from app.memory.store import create_user_memory, update_memory_status

        mem = create_user_memory(
            self.db, "demo", "local-user", "preference", "rule", content,
        )
        update_memory_status(self.db, mem.memory_id, "active")

    def test_handler_returns_memories(self) -> None:
        from app.memory.retriever import memory_search_handler
        from unittest.mock import patch

        self._create_active_memory("User prefers Chinese reports")

        with patch("app.memory.retriever.SessionLocal", return_value=self.db):
            result = memory_search_handler({
                "query": "Chinese",
                "top_k": 5,
                "tenant_id": "demo",
                "user_id": "local-user",
            })
        self.assertTrue(result.success, f"Handler failed: {result.error_message}")
        self.assertEqual(result.output["recalled"], 1)

    def test_handler_empty_query(self) -> None:
        from app.memory.retriever import memory_search_handler
        from unittest.mock import patch

        self._create_active_memory("Test memory")

        with patch("app.memory.retriever.SessionLocal", return_value=self.db):
            result = memory_search_handler({"query": "", "tenant_id": "demo", "user_id": "local-user"})
        self.assertTrue(result.success, f"Handler failed: {result.error_message}")
        self.assertGreaterEqual(result.output["recalled"], 1)

    def test_handler_no_matches(self) -> None:
        from app.memory.retriever import memory_search_handler
        from unittest.mock import patch

        with patch("app.memory.retriever.SessionLocal", return_value=self.db):
            result = memory_search_handler({
                "query": "nonexistent",
                "tenant_id": "demo",
                "user_id": "local-user",
            })
        self.assertTrue(result.success)
        self.assertEqual(result.output["recalled"], 0)

    def test_handler_top_k_bounds(self) -> None:
        from app.memory.retriever import memory_search_handler
        from unittest.mock import patch

        for i in range(10):
            self._create_active_memory(f"Memory {i}")

        with patch("app.memory.retriever.SessionLocal", return_value=self.db):
            result = memory_search_handler({
                "query": "Memory",
                "top_k": 2,
                "tenant_id": "demo",
                "user_id": "local-user",
            })
        self.assertTrue(result.success)
        self.assertLessEqual(result.output["recalled"], 2)


# ── Tool Registry: memory_search has handler ─────────────────────────

class MemorySearchToolRegistrationTests(unittest.TestCase):
    def test_memory_search_has_handler(self) -> None:
        from app.tools.registry import get_tool, execute_tool
        from app.tools.defaults import register_default_tools

        register_default_tools()
        spec = get_tool("memory_search")
        self.assertIsNotNone(spec)
        # Should no longer return "not_implemented"
        from app.memory.store import create_user_memory, update_memory_status

        db: Session = SessionLocal()
        _truncate_all(db)
        try:
            mem = create_user_memory(
                db, "demo", "local-user", "preference", "rule",
                "User prefers Chinese reports",
            )
            update_memory_status(db, mem.memory_id, "active")

            result = execute_tool("memory_search", {
                "query": "Chinese",
                "tenant_id": "demo",
                "user_id": "local-user",
            })
            self.assertTrue(result.success, f"memory_search failed: {result.error_message}")
            self.assertIn("recalled", result.output)
        finally:
            db.close()

    def test_memory_search_listed_in_tools(self) -> None:
        from app.tools.registry import list_tools
        from app.tools.defaults import register_default_tools

        register_default_tools()
        names = [spec.name for spec in list_tools()]
        self.assertIn("memory_search", names)


# ── Integration: after_run_completed ─────────────────────────────────

class AfterRunCompletedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_chat_turn_created_when_session_id_present(self) -> None:
        from app.agent.executor import _after_run_completed
        from app.memory.store import create_session, list_chat_turns

        session = create_session(self.db, "demo", "user-1", title="Test")
        run = _make_run(self.db, task="test task", session_id=session.session_id)

        _after_run_completed(self.db, run, "# Report\n\nSome content.", step_no=0)

        turns = list_chat_turns(self.db, session.session_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].role, "agent")
        self.assertEqual(turns[0].run_id, run.run_id)

    def test_no_chat_turn_without_session_id(self) -> None:
        from app.agent.executor import _after_run_completed
        from app.memory.store import create_session, list_chat_turns

        # Create a session but don't associate it with the run
        session = create_session(self.db, "demo", "user-1", title="Test")
        run = _make_run(self.db, task="test task", session_id=None)

        _after_run_completed(self.db, run, "# Report", step_no=0)

        turns = list_chat_turns(self.db, session.session_id)
        self.assertEqual(len(turns), 0)

    def test_after_run_completed_does_not_raise(self) -> None:
        """Verify the hook is safe and never raises."""
        from app.agent.executor import _after_run_completed

        run = _make_run(self.db, task="test task")
        # Should not raise
        _after_run_completed(self.db, run, "# Report", step_no=0)


# ── Integration: executor hook flow ──────────────────────────────────

class ExecutorMemoryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db: Session = SessionLocal()
        _truncate_all(self.db)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_two_runs_same_signal_produces_pending_memory(self) -> None:
        """Simulate the full flow: 2 completed runs with same preference."""
        from app.agent.executor import _after_run_completed
        from app.memory.store import create_session, list_user_memories

        session = create_session(self.db, "demo", "user-1", title="RAG Research")

        # First run — no memory yet
        run1 = _make_run(self.db, task="请帮我调研RAG技术", session_id=session.session_id)
        _after_run_completed(self.db, run1, "# RAG Research Report\n\nContent here.", step_no=0)

        # Second run — same domain interest
        run2 = _make_run(self.db, task="RAG和Agent框架的深度对比分析", session_id=session.session_id)
        _after_run_completed(self.db, run2, "# RAG vs Agent Report\n\nMore content.", step_no=0)

        # Check pending memories were created
        memories = list_user_memories(self.db, "demo", "user-1")
        # At least one pending memory should exist for the recurring interest
        pending = [m for m in memories if m.status == "pending"]
        self.assertGreaterEqual(len(pending), 1, f"No pending memories found: {memories}")


if __name__ == "__main__":
    unittest.main()
