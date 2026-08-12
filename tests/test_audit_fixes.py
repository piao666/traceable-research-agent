"""Regression tests for the 2026-08-04 audit fixes."""

from __future__ import annotations

import io
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.trace import models as trace_models  # noqa: F401


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


class ToolTimeoutTests(unittest.TestCase):
    def test_registry_enforces_tool_timeout(self) -> None:
        from app.tools.base import ToolResult, ToolSpec
        from app.tools.registry import execute_tool, register_tool

        def slow_handler(_arguments):
            time.sleep(1.2)
            return ToolResult(success=True)

        register_tool(
            ToolSpec(name="audit_timeout", description="test", input_schema={}, timeout_seconds=1),
            slow_handler,
        )
        result = execute_tool("audit_timeout")
        self.assertFalse(result.success)
        self.assertEqual(result.metadata["error_type"], "timeout")


class AcademicSearchTests(unittest.TestCase):
    def test_arxiv_query_is_encoded_once_and_categories_are_parsed(self) -> None:
        from app.tools.arxiv_search import arxiv_search_handler

        xml = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>1</opensearch:totalResults><entry>
          <id>paper-1</id><title>Title</title><summary>Summary</summary>
          <author><name>Ada Smith</name></author>
          <arxiv:primary_category term="cs.AI"/><category term="cs.AI"/><category term="cs.CL"/>
          </entry></feed>'''
        seen_url = ""

        def opener(request, timeout=0):
            nonlocal seen_url
            seen_url = request.full_url
            return _Response(xml)

        with patch("app.tools.arxiv_search.urlopen", opener), patch(
            "app.tools.arxiv_search._respect_rate_limit", lambda: None
        ):
            result = arxiv_search_handler({"query": "agent safety", "max_results": 1})

        query = parse_qs(urlsplit(seen_url).query)["search_query"][0]
        self.assertEqual(query, "all:agent safety")
        self.assertEqual(result.output["papers"][0]["authors"], ["Ada Smith"])
        self.assertEqual(result.output["papers"][0]["primary_category"], "cs.AI")
        self.assertEqual(result.output["papers"][0]["categories"], ["cs.AI", "cs.CL"])

    def test_semantic_scholar_uses_public_paper_url(self) -> None:
        from app.tools.semantic_scholar import _parse_paper

        paper = _parse_paper({"paperId": "abc", "authors": []})
        self.assertEqual(paper["url"], "https://www.semanticscholar.org/paper/abc")


class DeepeningTests(unittest.TestCase):
    def test_sub_run_creation_uses_agent_run_id(self) -> None:
        from app.agent.deepening import _run_single_round
        from app.config import Settings
        from app.trace.models import AgentRun
        from app.trace.store import create_agent_run

        engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        parent = create_agent_run(db, "parent", "summary", "mock")
        with patch("app.agent.deepening.run_react_task", return_value={"status": "completed"}):
            _run_single_round(db, parent.run_id, "parent", ["follow up"], Settings())
        child = db.scalar(select(AgentRun).where(AgentRun.task == "follow up"))
        self.assertIsNotNone(child)
        self.assertIsInstance(child.run_id, str)
        self.assertIn(parent.run_id, child.plan_json)
        db.close()


if __name__ == "__main__":
    unittest.main()
