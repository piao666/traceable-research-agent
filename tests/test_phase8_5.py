"""Phase 8.5 unit tests: openalex_search, crossref_search, systematic_review Skill.

Covers: argument validation, response parsing, error handling, tool registration,
Skill loading, and pipeline integration.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch, Mock

from app.tools.base import ToolResult


# ── Helpers ─────────────────────────────────────────────────────────────────

def _mock_urlopen_with_json(data: dict):
    """Create a mock urlopen that returns the given JSON data."""
    mock_resp = Mock()
    mock_resp.read.return_value = json.dumps(data).encode("utf-8")
    mock_resp.__enter__ = Mock(return_value=mock_resp)
    mock_resp.__exit__ = Mock(return_value=False)
    return lambda *a, **kw: mock_resp


def _mock_urlopen_http_error(code: int):
    from urllib.error import HTTPError
    def raiser(*a, **kw):
        raise HTTPError("http://test", code, "Error", {}, None)
    return raiser


def _mock_urlopen_timeout():
    from urllib.error import URLError
    def raiser(*a, **kw):
        raise URLError("timeout")
    return raiser


# ── OpenAlex argument validation ────────────────────────────────────────────

class OpenAlexArgValidationTests(unittest.TestCase):
    def test_empty_query_fails(self):
        from app.tools.openalex_search import openalex_search_handler
        result = openalex_search_handler({"query": ""})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message)

    def test_default_max_results(self):
        from app.tools.openalex_search import openalex_search_handler
        with patch("app.tools.openalex_search.urlopen",
                   _mock_urlopen_with_json({"results": [], "meta": {"count": 0, "per_page": 5}})):
            result = openalex_search_handler({"query": "test"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["returned"], 0)

    def test_max_results_capped_at_20(self):
        from app.tools.openalex_search import _bounded_limit
        self.assertEqual(_bounded_limit(100, 5, 1, 20), 20)
        self.assertEqual(_bounded_limit(15, 5, 1, 20), 15)
        self.assertEqual(_bounded_limit(0, 5, 1, 20), 1)  # below min → min
        self.assertEqual(_bounded_limit("invalid", 5, 1, 20), 5)

    def test_missing_query_key(self):
        from app.tools.openalex_search import openalex_search_handler
        result = openalex_search_handler({})
        self.assertFalse(result.success)


# ── OpenAlex handling ───────────────────────────────────────────────────────

class OpenAlexHandlingTests(unittest.TestCase):
    def test_parses_valid_response(self):
        from app.tools.openalex_search import openalex_search_handler
        data = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Test Paper",
                    "authorships": [
                        {"author": {"display_name": "Alice Smith"}},
                        {"author": {"display_name": "Bob Jones"}},
                    ],
                    "publication_year": 2024,
                    "doi": "https://doi.org/10.1234/test",
                    "cited_by_count": 42,
                    "primary_location": {"source": {"display_name": "Nature"}},
                    "open_access": {"is_oa": True},
                    "type": "journal-article",
                }
            ],
            "meta": {"count": 1, "per_page": 1},
        }
        with patch("app.tools.openalex_search.urlopen", _mock_urlopen_with_json(data)):
            result = openalex_search_handler({"query": "test", "max_results": 5})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 1)
            self.assertEqual(len(result.output["papers"]), 1)
            paper = result.output["papers"][0]
            self.assertEqual(paper["title"], "Test Paper")
            self.assertEqual(paper["authors"], ["Alice Smith", "Bob Jones"])
            self.assertEqual(paper["year"], 2024)
            self.assertEqual(paper["doi"], "https://doi.org/10.1234/test")
            self.assertEqual(paper["cited_by_count"], 42)
            self.assertTrue(paper["is_open_access"])

    def test_empty_results(self):
        from app.tools.openalex_search import openalex_search_handler
        data = {"results": [], "meta": {"count": 0, "per_page": 5}}
        with patch("app.tools.openalex_search.urlopen", _mock_urlopen_with_json(data)):
            result = openalex_search_handler({"query": "nonexistent_xyz"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 0)
            self.assertEqual(len(result.output["papers"]), 0)

    def test_http_error(self):
        from app.tools.openalex_search import openalex_search_handler
        with patch("app.tools.openalex_search.urlopen", _mock_urlopen_http_error(500)):
            result = openalex_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("HTTP error", result.error_message)

    def test_timeout(self):
        from app.tools.openalex_search import openalex_search_handler
        with patch("app.tools.openalex_search.urlopen", _mock_urlopen_timeout()):
            result = openalex_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("network error", result.error_message)

    def test_invalid_json(self):
        from app.tools.openalex_search import openalex_search_handler
        mock_resp = Mock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        with patch("app.tools.openalex_search.urlopen", lambda *a, **kw: mock_resp):
            result = openalex_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("invalid JSON", result.error_message)

    def test_missing_meta_field(self):
        from app.tools.openalex_search import openalex_search_handler
        data = {"results": [{"id": "W1", "title": "Only Paper", "publication_year": 2023}]}
        with patch("app.tools.openalex_search.urlopen", _mock_urlopen_with_json(data)):
            result = openalex_search_handler({"query": "test"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 0)  # no meta → 0


# ── Crossref argument validation ────────────────────────────────────────────

class CrossrefArgValidationTests(unittest.TestCase):
    def test_empty_query_fails(self):
        from app.tools.crossref_search import crossref_search_handler
        result = crossref_search_handler({"query": ""})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message)

    def test_default_max_results(self):
        from app.tools.crossref_search import crossref_search_handler
        data = {"message": {"items": [], "total-results": 0}}
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_with_json(data)):
            result = crossref_search_handler({"query": "test"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["returned"], 0)

    def test_max_results_capped_at_20(self):
        from app.tools.crossref_search import _bounded_rows
        self.assertEqual(_bounded_rows(100, 5, 1, 20), 20)
        self.assertEqual(_bounded_rows(10, 5, 1, 20), 10)
        self.assertEqual(_bounded_rows(0, 5, 1, 20), 1)  # below min → min

    def test_missing_query_key(self):
        from app.tools.crossref_search import crossref_search_handler
        result = crossref_search_handler({})
        self.assertFalse(result.success)


# ── Crossref handling ───────────────────────────────────────────────────────

class CrossrefHandlingTests(unittest.TestCase):
    def test_parses_valid_response(self):
        from app.tools.crossref_search import crossref_search_handler
        data = {
            "message": {
                "items": [
                    {
                        "title": ["Attention Is All You Need"],
                        "author": [
                            {"given": "Ashish", "family": "Vaswani"},
                            {"given": "Noam", "family": "Shazeer"},
                        ],
                        "publisher": "NeurIPS",
                        "DOI": "10.5555/3295222.3295349",
                        "published-print": {"date-parts": [[2017, 12]]},
                        "container-title": ["Advances in Neural Information Processing Systems"],
                        "type": "proceedings-article",
                    }
                ],
                "total-results": 1,
            }
        }
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_with_json(data)):
            result = crossref_search_handler({"query": "transformer"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 1)
            self.assertEqual(len(result.output["papers"]), 1)
            paper = result.output["papers"][0]
            self.assertEqual(paper["title"], "Attention Is All You Need")
            self.assertEqual(paper["authors"], ["Ashish Vaswani", "Noam Shazeer"])
            self.assertEqual(paper["year"], 2017)
            self.assertEqual(paper["doi"], "10.5555/3295222.3295349")
            self.assertEqual(paper["publisher"], "NeurIPS")
            self.assertIn("Advances in Neural Information Processing Systems", paper["venue"])

    def test_empty_results(self):
        from app.tools.crossref_search import crossref_search_handler
        data = {"message": {"items": [], "total-results": 0}}
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_with_json(data)):
            result = crossref_search_handler({"query": "no_such_paper_xyz"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 0)
            self.assertEqual(len(result.output["papers"]), 0)

    def test_http_error(self):
        from app.tools.crossref_search import crossref_search_handler
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_http_error(503)):
            result = crossref_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("HTTP error", result.error_message)

    def test_timeout(self):
        from app.tools.crossref_search import crossref_search_handler
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_timeout()):
            result = crossref_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("network error", result.error_message)

    def test_invalid_json(self):
        from app.tools.crossref_search import crossref_search_handler
        mock_resp = Mock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        with patch("app.tools.crossref_search.urlopen", lambda *a, **kw: mock_resp):
            result = crossref_search_handler({"query": "test"})
            self.assertFalse(result.success)
            self.assertIn("invalid JSON", result.error_message)

    def test_missing_message_field(self):
        from app.tools.crossref_search import crossref_search_handler
        data = {"not_message": {}}
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_with_json(data)):
            result = crossref_search_handler({"query": "test"})
            self.assertTrue(result.success)
            self.assertEqual(result.output["total"], 0)

    def test_paper_without_year(self):
        from app.tools.crossref_search import crossref_search_handler
        data = {
            "message": {
                "items": [
                    {"title": ["Untitled"], "author": [], "DOI": "10.0/x",
                     "container-title": [], "type": "journal-article"}
                ],
                "total-results": 1,
            }
        }
        with patch("app.tools.crossref_search.urlopen", _mock_urlopen_with_json(data)):
            result = crossref_search_handler({"query": "test"})
            self.assertTrue(result.success)
            paper = result.output["papers"][0]
            self.assertIsNone(paper["year"])
            self.assertEqual(paper["authors"], [])


# ── Tool registration ───────────────────────────────────────────────────────

class ToolRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.tools.defaults import register_default_tools
        from app.tools.registry import _tool_specs, _tool_handlers
        register_default_tools()

    def test_openalex_registered(self):
        from app.tools.registry import get_tool
        spec = get_tool("openalex_search")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "openalex_search")
        self.assertIn("academic", spec.tags)
        self.assertTrue(spec.enabled)

    def test_crossref_registered(self):
        from app.tools.registry import get_tool
        spec = get_tool("crossref_search")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "crossref_search")
        self.assertIn("academic", spec.tags)
        self.assertTrue(spec.enabled)

    def test_both_in_executable_tools(self):
        from app.agent.executor import EXECUTABLE_TOOLS
        self.assertIn("openalex_search", EXECUTABLE_TOOLS)
        self.assertIn("crossref_search", EXECUTABLE_TOOLS)

    def test_both_in_parallel_safe_tools(self):
        from app.agent.parallel_executor import PARALLEL_SAFE_TOOLS
        self.assertIn("openalex_search", PARALLEL_SAFE_TOOLS)
        self.assertIn("crossref_search", PARALLEL_SAFE_TOOLS)

    def test_handlers_are_callable(self):
        from app.tools.registry import _tool_handlers
        self.assertTrue(callable(_tool_handlers.get("openalex_search")))
        self.assertTrue(callable(_tool_handlers.get("crossref_search")))


# ── Skill loading ───────────────────────────────────────────────────────────

class SkillLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.tools.defaults import register_default_tools
        from app.skills.registry import init_skill_registry
        from pathlib import Path
        register_default_tools()
        skills_dir = Path(__file__).resolve().parents[1] / "workspace" / "skills"
        init_skill_registry(skills_dir)

    def test_systematic_review_loaded(self):
        from app.skills.registry import get_skill
        skill = get_skill("systematic_review")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "systematic_review")
        self.assertEqual(skill.version, "1.0")

    def test_all_required_tools_registered(self):
        from app.skills.registry import get_skill
        from app.tools.registry import list_tools
        skill = get_skill("systematic_review")
        registered = {t.name for t in list_tools()}
        for tool_name in skill.required_tools:
            self.assertIn(tool_name, registered,
                          f"Required tool '{tool_name}' not in Tool Registry")

    def test_has_5_steps(self):
        from app.skills.registry import get_skill
        skill = get_skill("systematic_review")
        self.assertEqual(len(skill.steps), 5)

    def test_steps_have_valid_tool_names(self):
        from app.skills.registry import get_skill
        from app.tools.registry import list_tools
        skill = get_skill("systematic_review")
        registered = {t.name for t in list_tools()}
        for step in skill.steps:
            self.assertIn(step.tool_name, registered,
                          f"Step tool '{step.tool_name}' not in Tool Registry")

    def test_parameters_parsed_correctly(self):
        from app.skills.registry import get_skill
        skill = get_skill("systematic_review")
        params = skill.parameters
        self.assertIn("query", params)
        self.assertTrue(params["query"].required)
        self.assertIn("year_from", params)
        self.assertEqual(params["year_from"].default, 2018)
        self.assertIn("year_to", params)
        self.assertEqual(params["year_to"].default, 2026)
        self.assertIn("max_papers", params)
        self.assertEqual(params["max_papers"].default, 30)
        self.assertIn("exclude_keywords", params)

    def test_skill_meta_valid(self):
        from app.skills.registry import list_skills
        metas = {m.name: m for m in list_skills()}
        self.assertIn("systematic_review", metas)
        self.assertEqual(metas["systematic_review"].status, "valid")

    def test_skill_smoke_valid(self):
        from app.skills.registry import get_skill
        from app.skills.loader import validate_skill
        from app.tools.registry import list_tools
        skill = get_skill("systematic_review")
        registered = {t.name for t in list_tools()}
        errors = validate_skill(skill, registered)
        self.assertEqual(len(errors), 0, f"Validation errors: {errors}")


# ── Pipeline integration ────────────────────────────────────────────────────

class PipelineIntegrationTests(unittest.TestCase):
    """Integration tests that verify the pipeline doesn't crash with new tools."""

    def test_all_four_search_tools_trivially(self):
        """Verify all 4 academic search tools are in the same executable set."""
        from app.agent.executor import EXECUTABLE_TOOLS
        academic_tools = {"arxiv_search", "semantic_scholar_search",
                          "openalex_search", "crossref_search"}
        self.assertTrue(academic_tools.issubset(EXECUTABLE_TOOLS))

    def test_new_tools_have_compatible_risk_level(self):
        from app.tools.registry import get_tool
        from app.tools.base import RiskLevel
        for name in ("openalex_search", "crossref_search"):
            spec = get_tool(name)
            self.assertEqual(spec.risk_level, RiskLevel.LOW,
                             f"{name} should be LOW risk")

    def test_new_tools_have_read_only_tag(self):
        from app.tools.registry import get_tool
        for name in ("openalex_search", "crossref_search"):
            spec = get_tool(name)
            self.assertIn("read-only", spec.tags,
                          f"{name} should have 'read-only' tag")

    def test_total_registered_tools(self):
        from app.tools.registry import list_tools
        tools = list_tools()
        tool_names = {t.name for t in tools}
        self.assertIn("openalex_search", tool_names)
        self.assertIn("crossref_search", tool_names)
        # 10 original + 2 new = 12 total (not counting report_writer)
        self.assertGreaterEqual(len(tools), 11)
