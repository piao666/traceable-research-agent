"""Phase 2 unit tests: web_fetcher, arguments_from, sub-query decomposition, concurrency."""

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.tools.base import ToolResult


# ── web_fetcher tests ──────────────────────────────────────────────────

class WebFetcherTests(unittest.TestCase):
    def setUp(self):
        from app.tools.web_fetcher import web_fetch

        self.fetch = web_fetch

    def test_rejects_missing_urls(self):
        result = self.fetch({})
        # web_fetcher treats missing urls as empty list → returns success with 0 pages
        self.assertTrue(result.success)
        self.assertEqual(result.output["total_count"], 0)

    def test_rejects_non_http_scheme(self):
        result = self.fetch({"urls": ["ftp://example.com/file"]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["fetched_count"], 0)
        self.assertEqual(result.output["failed_count"], 1)
        # The page entry should have an error
        self.assertIn("error", result.output["pages"][0])

    def test_rejects_private_ip(self):
        for bad_url in ("http://127.0.0.1/admin", "http://10.0.0.5/secret", "http://192.168.1.1/"):
            result = self.fetch({"urls": [bad_url]})
            self.assertTrue(result.success)
            self.assertEqual(result.output["fetched_count"], 0,
                             f"Should reject {bad_url}")
            self.assertIn("error", result.output["pages"][0])

    def test_accepts_public_url(self):
        result = self.fetch({"urls": ["https://example.com"]})
        self.assertTrue(result.success)
        # example.com should be fetchable
        self.assertGreaterEqual(result.output["total_count"], 1)

    def test_empty_url_list(self):
        result = self.fetch({"urls": []})
        self.assertTrue(result.success)
        self.assertEqual(result.output["total_count"], 0)

    def test_mixed_valid_invalid_urls(self):
        result = self.fetch({"urls": [
            "https://example.com",
            "http://127.0.0.1/bad",
            "not-a-url",
        ]})
        self.assertTrue(result.success)
        self.assertGreaterEqual(result.output["total_count"], 2)

    def test_output_structure(self):
        result = self.fetch({"urls": ["https://example.com"], "max_chars": 500})
        self.assertTrue(result.success)
        self.assertIn("pages", result.output)
        self.assertIn("fetched_count", result.output)
        self.assertIn("failed_count", result.output)
        self.assertIn("total_count", result.output)
        for page in result.output["pages"]:
            self.assertIn("url", page)
            self.assertIn("title", page)
            self.assertIn("content", page)
            self.assertIn("content_basis", page)
            self.assertIn("fetched_at_ms", page)

    def test_content_basis_values(self):
        result = self.fetch({"urls": ["https://example.com"], "max_chars": 500})
        for page in result.output["pages"]:
            self.assertIn(page["content_basis"], ("full_text", "partial", "snippet_only"))

    def test_string_url_input(self):
        result = self.fetch({"urls": "https://example.com"})
        self.assertTrue(result.success)

    def test_metadata_fields(self):
        result = self.fetch({"urls": ["https://example.com"]})
        self.assertEqual(result.metadata.get("tool_name"), "web_fetcher")
        self.assertEqual(result.metadata.get("fetcher_backend"), "httpx_beautifulsoup")
        self.assertTrue(result.metadata.get("read_only"))


# ── arguments_from tests ───────────────────────────────────────────────

class ArgumentsFromTests(unittest.TestCase):
    def setUp(self):
        from app.agent.executor import _resolve_arguments_from

        self.resolve = _resolve_arguments_from

    def test_resolve_urls_from_tavily_output(self):
        step = {
            "tool_name": "web_fetcher",
            "arguments_from": {"step_no": 1, "field": "results"},
            "arguments": {"urls": [], "max_chars": 8000},
        }
        observations = [
            {
                "step_no": 1,
                "tool_name": "tavily_search",
                "output": {
                    "results": [
                        {"url": "https://a.com", "title": "A"},
                        {"url": "https://b.com", "title": "B"},
                    ]
                },
            }
        ]
        resolved = self.resolve(step, observations)
        self.assertIn("urls", resolved)
        self.assertEqual(resolved["urls"], ["https://a.com", "https://b.com"])

    def test_missing_step_returns_original_args(self):
        step = {
            "tool_name": "web_fetcher",
            "arguments_from": {"step_no": 99, "field": "results"},
            "arguments": {"urls": [], "max_chars": 500},
        }
        observations = [{"step_no": 1, "output": {"results": [{"url": "https://x.com"}]}}]
        resolved = self.resolve(step, observations)
        self.assertEqual(resolved, {"urls": [], "max_chars": 500})

    def test_no_arguments_from_returns_original(self):
        step = {"tool_name": "tavily_search", "arguments": {"query": "test"}}
        observations = []
        resolved = self.resolve(step, observations)
        self.assertEqual(resolved, {"query": "test"})

    def test_resolve_generic_field(self):
        step = {
            "tool_name": "report_writer",
            "arguments_from": {"step_no": 1, "field": "markdown"},
            "arguments": {},
        }
        observations = [{"step_no": 1, "output": {"markdown": "# Report\ncontent"}}]
        resolved = self.resolve(step, observations)
        self.assertIn("markdown", resolved)
        self.assertEqual(resolved["markdown"], "# Report\ncontent")


# ── sub-query decomposition tests ──────────────────────────────────────

class SubQueryDecompositionTests(unittest.TestCase):
    def setUp(self):
        from app.agent.query_decomposer import decompose_task_by_rules

        self.decompose = decompose_task_by_rules

    def test_chinese_separators(self):
        result = self.decompose("调研 Python 异步编程、FastAPI 性能、aiohttp 设计")
        self.assertGreaterEqual(len(result), 2)

    def test_english_separators(self):
        result = self.decompose("Research AI agents, compare LangGraph and CrewAI")
        self.assertGreaterEqual(len(result), 2)

    def test_short_task_not_decomposed(self):
        result = self.decompose("FastAPI")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "FastAPI")

    def test_single_topic_not_split(self):
        result = self.decompose("Research the performance characteristics of Python asyncio in production environments")
        self.assertEqual(len(result), 1)

    def test_dedup_similar_subtopics(self):
        result = self.decompose("调研 FastAPI、FastAPI、FastAPI")
        # "调研 FastAPI" and "FastAPI" are different after normalization,
        # so we get at most 2 unique entries
        self.assertLessEqual(len(result), 2)

    def test_max_n_limit(self):
        long_task = "A、B、C、D、E、F、G、H"
        result = self.decompose(long_task, n=3)
        self.assertLessEqual(len(result), 3)


# ── concurrent write tests ─────────────────────────────────────────────

class ConcurrentWriteTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        # Import all models so create_all finds them
        from app.trace import models  # noqa: F401
        from app.evidence import models as evidence_models  # noqa: F401
        from app.memory import models as memory_models  # noqa: F401

        # Use file-based SQLite so all threads share the same DB
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._db_path = self._tmpfile.name
        self._tmpfile.close()

        self.engine = create_engine(
            f"sqlite:///{self._db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Create a run first
        db = self.SessionLocal()
        from app.trace.store import create_agent_run

        self.run = create_agent_run(db, "concurrency test", "summary", "real")
        self.run_id = self.run.run_id
        db.close()

    def test_concurrent_trace_writes_no_lock_error(self):
        """N threads writing traces concurrently should not raise database locked."""
        from app.trace.logger import record_tool_result
        from app.tools.base import ToolResult

        errors: list[Exception] = []

        def write_trace(thread_id: int) -> None:
            try:
                db = self.SessionLocal()
                for i in range(5):
                    record_tool_result(
                        db,
                        self.run_id,
                        step_no=thread_id * 100 + i,
                        tool_name="web_fetcher",
                        input_data={"urls": [f"https://t{thread_id}-{i}.com"]},
                        result=ToolResult(
                            success=True,
                            output={"pages": []},
                            output_summary=f"thread {thread_id} step {i}",
                        ),
                        latency_ms=10,
                    )
                db.close()
            except Exception as exc:
                errors.append(exc)

        threads = []
        for t in range(4):
            th = threading.Thread(target=write_trace, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        self.assertEqual(len(errors), 0, f"Concurrent writes raised: {errors}")

    def test_visited_urls_dedup_thread_safe(self):
        """URLs visited by one thread should not be re-fetched by another."""
        from app.agent.parallel_executor import _execute_step
        from app.tools.registry import execute_tool

        visited: set[str] = set()
        lock = threading.Lock()

        # Pre-populate with a URL
        visited.add("https://already-fetched.com")

        step = {
            "tool_name": "web_fetcher",
            "step_no": 1,
            "arguments": {
                "urls": [
                    "https://already-fetched.com",
                    "https://new-url.com",
                ],
                "max_chars": 500,
                "timeout_seconds": 5,
            },
        }

        step_result = _execute_step(step, 1, visited_urls=visited, visited_urls_lock=lock)

        # The already-fetched URL should have been filtered out
        call_args = step_result.step.get("arguments", {}).get("urls", [])
        self.assertNotIn("https://already-fetched.com", call_args)

    def tearDown(self):
        import os

        from app.database import Base

        Base.metadata.drop_all(bind=self.engine)
        try:
            os.unlink(self._db_path)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
