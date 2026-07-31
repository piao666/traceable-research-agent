"""Phase 5 tests: iterative deepening, inline citations, LLM distillation, conflict dashboard."""

import json
import unittest

from app.agent.context_compressor import compress_deepening_context


# ── Deepening context compression ──────────────────────────────────

class DeepeningContextCompressionTests(unittest.TestCase):
    """compress_deepening_context should truncate oldest rounds first."""

    def test_single_round_preserved(self):
        rounds = [
            {
                "round_num": 1,
                "learnings": ["Fact A", "Fact B"],
                "follow_up_queries": ["Query 1"],
                "compressed_observations": "Tool output here.",
            }
        ]
        result = compress_deepening_context(rounds, max_total_chars=2000)
        self.assertIn("Round 1", result)
        self.assertIn("Fact A", result)
        self.assertIn("Query 1", result)

    def test_empty_rounds_returns_empty(self):
        self.assertEqual(compress_deepening_context([], 1000), "")

    def test_budget_respected(self):
        rounds = [
            {"round_num": i, "learnings": [f"Learning {i}" * 50], "follow_up_queries": [], "compressed_observations": ""}
            for i in range(1, 6)
        ]
        result = compress_deepening_context(rounds, max_total_chars=500)
        self.assertLessEqual(len(result), 550)  # small tolerance

    def test_newest_round_keeps_detail(self):
        rounds = [
            {"round_num": 1, "learnings": ["Old learning"], "follow_up_queries": [], "compressed_observations": "old detail" * 100},
            {"round_num": 2, "learnings": ["New learning"], "follow_up_queries": [], "compressed_observations": "new detail" * 100},
        ]
        result = compress_deepening_context(rounds, max_total_chars=300)
        # Round 2 should appear before round 1 (newest first)
        self.assertIn("Round 2", result)
        # Round 1 should be truncated/abbreviated
        self.assertLessEqual(len(result), 350)

    def test_learnings_only_when_over_budget(self):
        rounds = [
            {"round_num": 1, "learnings": ["A", "B", "C", "D", "E", "F"], "follow_up_queries": [], "compressed_observations": "lots of text" * 200},
            {"round_num": 2, "learnings": ["X", "Y"], "follow_up_queries": [], "compressed_observations": "more text" * 200},
        ]
        result = compress_deepening_context(rounds, max_total_chars=500)
        self.assertLessEqual(len(result), 550)


# ── Deepening response parsing ─────────────────────────────────────

class DeepeningResponseParsingTests(unittest.TestCase):
    """_parse_deepening_response should handle various LLM output forms."""

    def _parse(self, content: str):
        from app.agent.deepening import _parse_deepening_response
        return _parse_deepening_response(content)

    def test_valid_json(self):
        result = self._parse(json.dumps({
            "learnings": ["L1", "L2"],
            "follow_up_queries": ["Q1"],
            "is_comprehensive": False,
        }))
        self.assertEqual(result["learnings"], ["L1", "L2"])
        self.assertEqual(result["follow_up_queries"], ["Q1"])
        self.assertFalse(result["is_comprehensive"])

    def test_markdown_wrapped_json(self):
        result = self._parse("```json\n{\"learnings\": [\"L1\"], \"follow_up_queries\": [], \"is_comprehensive\": true}\n```")
        self.assertEqual(result["learnings"], ["L1"])
        self.assertTrue(result["is_comprehensive"])

    def test_empty_string(self):
        result = self._parse("")
        self.assertEqual(result["learnings"], [])
        self.assertTrue(result["is_comprehensive"])

    def test_invalid_json(self):
        result = self._parse("not valid json at all")
        self.assertEqual(result["learnings"], [])
        self.assertTrue(result["is_comprehensive"])

    def test_missing_keys(self):
        result = self._parse("{}")
        self.assertEqual(result["learnings"], [])
        self.assertEqual(result["follow_up_queries"], [])
        # is_comprehensive defaults to False when key is absent
        self.assertFalse(result["is_comprehensive"])

    def test_comprehensive_stops_followups(self):
        result = self._parse(json.dumps({
            "learnings": ["Done"],
            "follow_up_queries": [],
            "is_comprehensive": True,
        }))
        self.assertTrue(result["is_comprehensive"])
        self.assertEqual(result["follow_up_queries"], [])


# ── Citation index rendering ───────────────────────────────────────

class CitationIndexTests(unittest.TestCase):
    """_render_citation_index should produce a markdown table."""

    def test_empty_bundle_returns_empty(self):
        from app.agent.reporter import _render_citation_index
        self.assertEqual(_render_citation_index(None), [])

    def test_no_citations_returns_empty(self):
        from app.agent.reporter import _render_citation_index
        bundle = {"citations": [], "passages": [], "edges": []}
        self.assertEqual(_render_citation_index(bundle), [])

    def test_renders_citation_table(self):
        from app.agent.reporter import _render_citation_index
        bundle = {
            "passages": [
                {
                    "passage_id": "pass_001",
                    "text": "This is a test passage about AI research.",
                    "content_basis": "full_text",
                    "locator": {"uri": "https://example.com/article"},
                    "trace_id": "trace_001",
                }
            ],
            "citations": [
                {
                    "citation_id": "cit_001",
                    "citation_label": "CIT-001-01",
                    "report_claim_id": "rc_001",
                    "passage_id": "pass_001",
                    "edge_id": "edge_001",
                }
            ],
            "edges": [
                {
                    "edge_id": "edge_001",
                    "relation": "supports",
                    "claim_id": "claim_001",
                }
            ],
        }
        lines = _render_citation_index(bundle)
        self.assertGreater(len(lines), 0)
        table_text = "\n".join(lines)
        self.assertIn("CIT-001-01", table_text)
        self.assertIn("passage", table_text.lower())

    def test_handles_missing_passage(self):
        from app.agent.reporter import _render_citation_index
        bundle = {
            "passages": [],
            "citations": [
                {
                    "citation_id": "cit_001",
                    "citation_label": "CIT-001-01",
                    "report_claim_id": "rc_001",
                    "passage_id": "missing_pass",
                    "edge_id": "edge_001",
                }
            ],
            "edges": [],
        }
        lines = _render_citation_index(bundle)
        self.assertGreater(len(lines), 0)


# ── Citation map building ──────────────────────────────────────────

class CitationMapTests(unittest.TestCase):
    """_build_citation_map should map citation labels to evidence data."""

    def test_empty_provenance(self):
        from frontend.streamlit_app import _build_citation_map
        self.assertEqual(_build_citation_map(None), {})

    def test_builds_citation_map(self):
        from frontend.streamlit_app import _build_citation_map
        provenance = {
            "passages": [
                {
                    "passage_id": "pass_001",
                    "snapshot_id": "snap_001",
                    "text": "Evidence text here.",
                    "content_basis": "full_text",
                    "trace_id": "t1",
                }
            ],
            "source_documents": [
                {
                    "document_id": "doc_001",
                    "canonical_uri": "https://example.com/doc",
                    "title": "Example Document",
                }
            ],
            "source_snapshots": [
                {
                    "snapshot_id": "snap_001",
                    "document_id": "doc_001",
                }
            ],
            "citations": [
                {
                    "citation_id": "cit_001",
                    "citation_label": "CIT-001-01",
                    "report_claim_id": "rc_001",
                    "passage_id": "pass_001",
                    "edge_id": "edge_001",
                }
            ],
            "edges": [
                {
                    "edge_id": "edge_001",
                    "relation": "supports",
                    "claim_id": "claim_001",
                }
            ],
        }
        result = _build_citation_map(provenance)
        self.assertIn("CIT-001-01", result)
        entry = result["CIT-001-01"]
        self.assertEqual(entry["relation"], "supports")
        self.assertEqual(entry["source_uri"], "https://example.com/doc")
        self.assertEqual(entry["source_title"], "Example Document")
        self.assertIn("passage", entry)

    def test_no_citations_returns_empty(self):
        from frontend.streamlit_app import _build_citation_map
        provenance = {"citations": [], "passages": [], "source_documents": [], "source_snapshots": [], "edges": []}
        self.assertEqual(_build_citation_map(provenance), {})


# ── Citation badge rendering ───────────────────────────────────────

class CitationBadgeTests(unittest.TestCase):
    """_render_citation_badges should replace [CIT-XXX-XX] with HTML badges."""

    def test_no_citation_map_preserves_text(self):
        from frontend.streamlit_app import _render_citation_badges
        text = "This is a claim [CIT-001-01] with evidence."
        result = _render_citation_badges(text, {})
        self.assertEqual(result, text)

    def test_replaces_citation_with_badge(self):
        from frontend.streamlit_app import _render_citation_badges
        citation_map = {
            "CIT-001-01": {
                "passage": {"text": "Supporting evidence text."},
                "relation": "supports",
                "source_uri": "https://example.com",
                "source_title": "Example",
            }
        }
        text = "A claim [CIT-001-01] backed by evidence."
        result = _render_citation_badges(text, citation_map)
        self.assertIn("span", result)
        self.assertIn("CIT-001-01", result)
        self.assertIn("title=", result)

    def test_unknown_citation_label_unchanged(self):
        from frontend.streamlit_app import _render_citation_badges
        text = "Claim [CIT-999-99] no match."
        result = _render_citation_badges(text, {"CIT-001-01": {}})
        self.assertIn("[CIT-999-99]", result)

    def test_refute_relation_colored(self):
        from frontend.streamlit_app import _render_citation_badges
        citation_map = {
            "CIT-001-01": {
                "passage": {"text": "Refuting evidence."},
                "relation": "refutes",
                "source_uri": "",
                "source_title": "",
            }
        }
        result = _render_citation_badges("Claim [CIT-001-01]", citation_map)
        self.assertIn("#B91C1C", result)


# ── Evidence card HTML ─────────────────────────────────────────────

class EvidenceCardTests(unittest.TestCase):
    """_evidence_card_html should produce valid HTML cards."""

    def test_basic_card(self):
        from frontend.streamlit_app import _evidence_card_html
        passage = {"text": "Test passage content.", "content_basis": "full_text"}
        html = _evidence_card_html("CIT-001-01", passage)
        self.assertIn("CIT-001-01", html)
        self.assertIn("Test passage content", html)
        self.assertIn("🌐 全文", html)

    def test_card_with_source(self):
        from frontend.streamlit_app import _evidence_card_html
        passage = {"text": "Content", "content_basis": "partial"}
        html = _evidence_card_html("CIT-002-01", passage, relation="refutes", source_uri="https://example.com", source_title="Example Source")
        self.assertIn("❌ 反驳", html)
        self.assertIn("Example Source", html)
        self.assertIn("https://example.com", html)


# ── LLM memory extraction parsing ──────────────────────────────────

class LLMMemoryExtractionTests(unittest.TestCase):
    """extract_preferences_with_llm should parse LLM output correctly."""

    def test_unavailable_client_returns_empty(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        run.task = "test task"
        run.run_id = "test_run"
        client = MagicMock()
        client.is_available.return_value = False
        result = extract_preferences_with_llm(run, [], client)
        self.assertEqual(result, [])

    def test_none_client_returns_empty(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        result = extract_preferences_with_llm(run, [], None)
        self.assertEqual(result, [])

    def test_parses_valid_llm_response(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        run.task = "Research AI agents"
        run.run_id = "test_run"
        client = MagicMock()
        client.is_available.return_value = True
        response = MagicMock()
        response.success = True
        response.content = json.dumps({
            "preferences": [
                {"kind": "interest", "content": "User researches AI agents", "confidence": 0.8},
                {"kind": "preference", "content": "User prefers Chinese reports", "confidence": 0.7},
            ]
        })
        client.complete.return_value = response
        result = extract_preferences_with_llm(run, [], client)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["kind"], "interest")
        self.assertEqual(result[0]["extraction_method"], "llm")
        self.assertEqual(result[0]["confidence"], 0.8)

    def test_handles_failed_response(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        run.task = "test"
        run.run_id = "test_run"
        client = MagicMock()
        client.is_available.return_value = True
        response = MagicMock()
        response.success = False
        response.content = None
        client.complete.return_value = response
        result = extract_preferences_with_llm(run, [], client)
        self.assertEqual(result, [])

    def test_handles_invalid_json(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        run.task = "test"
        run.run_id = "test_run"
        client = MagicMock()
        client.is_available.return_value = True
        response = MagicMock()
        response.success = True
        response.content = "not valid json {{{"
        client.complete.return_value = response
        result = extract_preferences_with_llm(run, [], client)
        self.assertEqual(result, [])

    def test_filters_invalid_kinds(self):
        from app.memory.extractor import extract_preferences_with_llm
        from unittest.mock import MagicMock
        run = MagicMock()
        run.task = "test"
        run.run_id = "test_run"
        client = MagicMock()
        client.is_available.return_value = True
        response = MagicMock()
        response.success = True
        response.content = json.dumps({
            "preferences": [
                {"kind": "invalid_kind", "content": "Test", "confidence": 0.5},
            ]
        })
        client.complete.return_value = response
        result = extract_preferences_with_llm(run, [], client)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "preference")  # default for invalid


# ── Config defaults ────────────────────────────────────────────────

class Phase5ConfigDefaultsTests(unittest.TestCase):
    """New Phase 5 config fields should have sensible defaults."""

    def test_deep_research_disabled_by_default(self):
        from app.config import settings
        self.assertFalse(settings.deep_research_enabled)

    def test_deep_research_max_depth_default(self):
        from app.config import settings
        self.assertEqual(settings.deep_research_max_depth, 2)

    def test_deep_research_breadth_default(self):
        from app.config import settings
        self.assertEqual(settings.deep_research_breadth, 3)

    def test_memory_llm_extraction_disabled_by_default(self):
        from app.config import settings
        self.assertFalse(settings.memory_llm_extraction_enabled)

    def test_safe_runtime_summary_includes_phase5_vars(self):
        from app.config import settings
        summary = settings.get_safe_runtime_config_summary()
        self.assertIn("deep_research_enabled", summary)
        self.assertIn("deep_research_max_depth", summary)
        self.assertIn("deep_research_breadth", summary)
        self.assertIn("memory_llm_extraction_enabled", summary)


# ── Deepening message building ─────────────────────────────────────

class DeepeningMessageTests(unittest.TestCase):
    """_build_deepening_messages should format observations for LLM."""

    def test_builds_messages_with_observations(self):
        from app.agent.deepening import _build_deepening_messages
        observations = [
            {"tool_name": "tavily_search", "success": True, "output_summary": "Found 5 results."},
            {"tool_name": "web_fetcher", "success": True, "output_summary": "Fetched 3 pages."},
        ]
        messages = _build_deepening_messages("Research task", observations, [], 3)
        self.assertEqual(len(messages), 2)
        self.assertIn("Research task", messages[1].content)
        self.assertIn("tavily_search", messages[1].content)
        self.assertIn("web_fetcher", messages[1].content)

    def test_includes_prior_learnings(self):
        from app.agent.deepening import _build_deepening_messages
        messages = _build_deepening_messages("Task", [], ["Prior learning 1", "Prior learning 2"], 3)
        self.assertIn("Prior learning 1", messages[1].content)

    def test_failed_observations_marked(self):
        from app.agent.deepening import _build_deepening_messages
        observations = [
            {"tool_name": "tavily_search", "success": False, "output_summary": "API error"},
        ]
        messages = _build_deepening_messages("Task", observations, [], 3)
        self.assertIn("❌", messages[1].content)


# ── Phase 5 Bug Fix Regression Tests ──────────────────────────────

class SkillParamDefaultBugTests(unittest.TestCase):
    """Bug 1: Skill numeric parameter default was lost because isinstance(pdef, dict)
    is always False for Pydantic SkillParameter objects."""

    def test_skill_param_default_preserved_for_pydantic(self):
        """pdef.model_dump() should be used when isinstance fails."""
        from app.agent.planner import _fill_skill_arguments
        arguments = {"max_results": "{{parameters.max_urls}}"}
        compiled = [{"step_no": 1}]
        params = {"query": "test", "max_urls": 5}

        result = _fill_skill_arguments(arguments, "fallback", compiled, params)
        self.assertIsInstance(result["max_results"], int)
        self.assertEqual(result["max_results"], 5)

    def test_skill_param_string_fallback_works(self):
        """When the value is a string, it should stay a string."""
        from app.agent.planner import _fill_skill_arguments
        arguments = {"query": "{{parameters.query}}"}
        compiled = []
        params = {"query": "my search query"}

        result = _fill_skill_arguments(arguments, "fallback", compiled, params)
        self.assertEqual(result["query"], "my search query")

    def test_multi_placeholder_still_string(self):
        """'Search for {{parameters.query}}' should stay a string after substitution."""
        from app.agent.planner import _fill_skill_arguments
        arguments = {"query": "Search for {{parameters.query}} in depth"}
        compiled = []
        params = {"query": "AI agents"}

        result = _fill_skill_arguments(arguments, "fallback", compiled, params)
        self.assertEqual(result["query"], "Search for AI agents in depth")

    def test_boolean_value_preserved(self):
        """Boolean default values should be preserved."""
        from app.agent.planner import _fill_skill_arguments
        arguments = {"include_answer": "{{parameters.include_answer}}"}
        compiled = []
        params = {"include_answer": True}

        result = _fill_skill_arguments(arguments, "fallback", compiled, params)
        self.assertIsInstance(result["include_answer"], bool)
        self.assertEqual(result["include_answer"], True)

    def test_skill_placeholder_type_preservation(self):
        """_resolve_skill_placeholder should return raw values, not strings."""
        from app.agent.planner import _resolve_skill_placeholder
        params = {"max_urls": 5, "enabled": True}
        compiled = []

        self.assertEqual(_resolve_skill_placeholder("parameters.max_urls", "t", compiled, params), 5)
        self.assertEqual(_resolve_skill_placeholder("parameters.enabled", "t", compiled, params), True)
        self.assertEqual(_resolve_skill_placeholder("parameters.missing", "t", compiled, params), "t")


class MemoryRecallTraceWiringTests(unittest.TestCase):
    """Bug 2: memory_recall trace events were defined but never recorded."""

    def test_cold_start_trace_event_structure(self):
        from app.memory.policy import build_cold_start_trace_event
        event = build_cold_start_trace_event()
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 0)
        self.assertEqual(event["reason"], "cold_start")

    def test_memory_recall_trace_event_structure(self):
        from app.memory.policy import build_memory_recall_trace_event
        event = build_memory_recall_trace_event(3, 450, ["m1", "m2", "m3"])
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 3)
        self.assertEqual(event["injected_chars"], 450)
        self.assertEqual(event["memory_ids"], ["m1", "m2", "m3"])
        self.assertIsNone(event["reason"])

    def test_budget_trimmed_trace_event_structure(self):
        from app.memory.policy import build_memory_injection_trimmed_trace_event
        event = build_memory_injection_trimmed_trace_event(10, 5, 600, 800)
        self.assertEqual(event["event_type"], "memory_recall")
        self.assertEqual(event["recalled"], 5)
        self.assertEqual(event["total_available"], 10)
        self.assertEqual(event["reason"], "budget_trimmed")

    def test_plan_task_accepts_tenant_user_params(self):
        """plan_task should accept tenant_id and user_id parameters without error."""
        from app.agent.planner import plan_task
        # Just verify the call doesn't raise for wrong arg names
        plan = plan_task("test task", tenant_id="t1", user_id="u1")
        self.assertIsInstance(plan, dict)
        self.assertIn("steps", plan)


if __name__ == "__main__":
    unittest.main()
