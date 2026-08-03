"""Phase 6 tests: cost tracking, multi-report types, academic search, claim verification, demo script."""

import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from app.llm.base import LLMMessage, LLMResponse, LLMUsage
from app.llm.cost import estimate_cost, estimate_cost_from_tokens
from app.tools.base import ToolResult


# ── LLM Cost Tracking ─────────────────────────────────────────────────────

class LLMUsageModelTests(unittest.TestCase):
    """Test LLMUsage model and LLMResponse integration."""

    def test_llm_usage_defaults(self):
        usage = LLMUsage()
        self.assertEqual(usage.prompt_tokens, 0)
        self.assertEqual(usage.completion_tokens, 0)
        self.assertEqual(usage.total_tokens, 0)

    def test_llm_usage_with_values(self):
        usage = LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        self.assertEqual(usage.prompt_tokens, 100)
        self.assertEqual(usage.completion_tokens, 50)
        self.assertEqual(usage.total_tokens, 150)

    def test_llm_response_without_usage(self):
        resp = LLMResponse(success=True, content="test", provider="qwen", model="qwen-plus")
        self.assertIsNone(resp.usage)

    def test_llm_response_with_usage(self):
        usage = LLMUsage(prompt_tokens=200, completion_tokens=80, total_tokens=280)
        resp = LLMResponse(
            success=True, content="test", provider="deepseek", model="deepseek-chat",
            usage=usage,
        )
        self.assertIsNotNone(resp.usage)
        self.assertEqual(resp.usage.total_tokens, 280)

    def test_llm_response_failure_no_usage(self):
        resp = LLMResponse(
            success=False, content=None, provider="qwen",
            error_message="API error",
        )
        self.assertIsNone(resp.usage)


class CostEstimationTests(unittest.TestCase):
    """Test cost estimation from token usage."""

    def test_estimate_cost_none_usage(self):
        self.assertEqual(estimate_cost("deepseek", "deepseek-chat", None), 0.0)

    def test_estimate_cost_zero_tokens(self):
        usage = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        self.assertEqual(estimate_cost("qwen", "qwen-plus", usage), 0.0)

    def test_estimate_cost_deepseek_chat(self):
        usage = LLMUsage(prompt_tokens=500_000, completion_tokens=250_000, total_tokens=750_000)
        cost = estimate_cost("deepseek", "deepseek-chat", usage)
        # prompt: 0.5M * 1.0 = 0.5, completion: 0.25M * 2.0 = 0.5 → total 1.0
        self.assertAlmostEqual(cost, 1.0, places=4)

    def test_estimate_cost_qwen_plus(self):
        usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000)
        cost = estimate_cost("qwen", "qwen-plus", usage)
        # prompt: 1M * 2.0 = 2.0, completion: 0.5M * 6.0 = 3.0 → total 5.0
        self.assertAlmostEqual(cost, 5.0, places=4)

    def test_estimate_cost_from_tokens(self):
        cost = estimate_cost_from_tokens("deepseek", "deepseek-chat", 500_000, 250_000)
        self.assertAlmostEqual(cost, 1.0, places=4)

    def test_estimate_cost_unknown_provider(self):
        usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000)
        cost = estimate_cost("unknown", None, usage)
        # Uses default pricing: prompt 1.0/1M
        self.assertAlmostEqual(cost, 1.0, places=4)

    def test_estimate_cost_from_tokens_zero(self):
        self.assertEqual(estimate_cost_from_tokens("qwen", "qwen-plus", 0, 0), 0.0)


# ── Multi-Report Types ────────────────────────────────────────────────────

class ReportTypeTests(unittest.TestCase):
    """Test report type handling functions."""

    def setUp(self):
        from app.agent.reporter import _build_toc, _to_outline, _find_section_start

        self._build_toc = _build_toc
        self._to_outline = _to_outline
        self._find_section_start = _find_section_start

    def test_build_toc_from_headings(self):
        lines = [
            "# Title",
            "## 1. 任务说明",
            "Some text",
            "### 步骤 1",
            "More text",
            "## 2. 运行摘要",
        ]
        toc = self._build_toc(lines)
        self.assertIn("## 目录", toc)
        self.assertIn("* 1. 任务说明", toc)
        self.assertIn("* 2. 运行摘要", toc)
        self.assertIn("    * 步骤 1", toc)

    def test_build_toc_empty(self):
        toc = self._build_toc([])
        self.assertIn("## 目录", toc)

    def test_find_section_start(self):
        lines = ["text", "## 3. Something", "content"]
        pos = self._find_section_start(lines, "## 3.")
        self.assertEqual(pos, 1)

    def test_find_section_start_not_found(self):
        pos = self._find_section_start(["no headings"], "## 9.")
        self.assertEqual(pos, -1)

    def test_to_outline_strips_content(self):
        lines = [
            "# Title",
            "",
            "## 1. 任务说明",
            "Some paragraph with many words",
            "Another line of text",
            "",
            "## 2. 运行摘要",
            "* item",
            "",
            "### Sub section",
            "detail",
        ]
        outline = self._to_outline(lines)
        self.assertIn("# Title", outline)
        self.assertIn("## 1.", outline)
        self.assertIn("## 2.", outline)
        self.assertIn("### Sub section", outline)
        self.assertNotIn("Some paragraph", outline)
        self.assertIn("大纲模式", outline)
        self.assertIn("大纲模式", outline)

    def test_to_outline_minimal(self):
        lines = ["# Title", "", "## 1. Intro", "some text"]
        outline = self._to_outline(lines)
        self.assertIn("# Title", outline)
        self.assertIn("## 1. Intro", outline)


# ── Claim Verification Pass ───────────────────────────────────────────────

class ClaimVerificationTests(unittest.TestCase):
    """Test limitations section rendering for unresolved claims."""

    def setUp(self):
        from app.agent.reporter import _render_limitations_section
        self._render = _render_limitations_section

    def test_no_resolutions_returns_empty(self):
        bundle = {"claims": [], "resolutions": []}
        result = self._render(bundle)
        self.assertEqual(result, [])

    def test_all_resolved_returns_empty(self):
        bundle = {
            "claims": [{"claim_id": "c1", "claim_text": "Claim 1"}],
            "resolutions": [{"claim_id": "c1", "status": "confirmed", "confidence": 0.95}],
        }
        result = self._render(bundle)
        self.assertEqual(result, [])

    def test_unresolved_claim_rendered(self):
        bundle = {
            "claims": [
                {"claim_id": "c1", "claim_text": "A disputed claim"},
            ],
            "resolutions": [
                {
                    "claim_id": "c1",
                    "status": "unresolved",
                    "confidence": 0.45,
                    "independent_support_count": 2,
                    "independent_refute_count": 3,
                    "rationale": {"summary": "Conflicting evidence from multiple sources"},
                },
            ],
        }
        result = self._render(bundle)
        self.assertTrue(len(result) > 0)
        section_text = "\n".join(result)
        self.assertIn("10. 限制与待核实结论", section_text)
        self.assertIn("A disputed claim", section_text)
        self.assertIn("unresolved", section_text)
        self.assertIn("未解决", section_text)

    def test_requires_human_claim_rendered(self):
        bundle = {
            "claims": [
                {"claim_id": "c2", "claim_text": "Needs human judgment"},
            ],
            "resolutions": [
                {
                    "claim_id": "c2",
                    "status": "requires_human",
                    "confidence": 0.5,
                    "independent_support_count": 1,
                    "independent_refute_count": 1,
                    "rationale": {},
                },
            ],
        }
        result = self._render(bundle)
        section_text = "\n".join(result)
        self.assertIn("Needs human judgment", section_text)
        self.assertIn("requires_human", section_text)
        self.assertIn("需人工判断", section_text)

    def test_quality_gate_failure_shown(self):
        bundle = {
            "claims": [
                {"claim_id": "c3", "claim_text": "Weak claim"},
            ],
            "resolutions": [
                {
                    "claim_id": "c3",
                    "status": "unresolved",
                    "confidence": 0.3,
                    "independent_support_count": 0,
                    "independent_refute_count": 0,
                    "rationale": {
                        "quality_gate": {
                            "passed": False,
                            "independent_source_count": 0,
                            "minimum_independent_sources": 2,
                        },
                    },
                },
            ],
        }
        result = self._render(bundle)
        section_text = "\n".join(result)
        self.assertIn("质量门禁: 未通过", section_text)


# ── Academic Search Tools ──────────────────────────────────────────────────

class ArxivSearchTests(unittest.TestCase):
    """Test arXiv search handler argument validation."""

    def setUp(self):
        from app.tools.arxiv_search import arxiv_search_handler
        self.handler = arxiv_search_handler

    def test_empty_query_fails(self):
        result = self.handler({"query": ""})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message.lower())

    def test_missing_query_fails(self):
        result = self.handler({})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message.lower())

    def test_max_results_bounded(self):
        from app.tools.arxiv_search import _bounded_max
        self.assertEqual(_bounded_max(50, 5, 1, 30), 30)
        self.assertEqual(_bounded_max(0, 5, 1, 30), 1)  # 0 valid int, clamped to minimum
        self.assertEqual(_bounded_max(5, 5, 1, 30), 5)
        self.assertEqual(_bounded_max("invalid", 10, 1, 30), 10)  # non-int → default


class SemanticScholarTests(unittest.TestCase):
    """Test Semantic Scholar search handler argument validation."""

    def setUp(self):
        from app.tools.semantic_scholar import semantic_scholar_handler
        self.handler = semantic_scholar_handler

    def test_empty_query_fails(self):
        result = self.handler({"query": ""})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message.lower())

    def test_missing_query_fails(self):
        result = self.handler({})
        self.assertFalse(result.success)
        self.assertIn("query", result.error_message.lower())

    def test_limit_bounded(self):
        from app.tools.semantic_scholar import _bounded_limit
        self.assertEqual(_bounded_limit(100, 5, 1, 20), 20)
        self.assertEqual(_bounded_limit(0, 5, 1, 20), 1)  # 0 valid int, clamped to minimum
        self.assertEqual(_bounded_limit(10, 5, 1, 20), 10)
        self.assertEqual(_bounded_limit("invalid", 10, 1, 20), 10)  # non-int → default


# ── Tool Registration ─────────────────────────────────────────────────────

class Phase6ToolRegistrationTests(unittest.TestCase):
    """Test that Phase 6 tools are registered correctly."""

    @classmethod
    def setUpClass(cls):
        """Register default tools once for all tests."""
        from app.tools.defaults import register_default_tools
        register_default_tools()

    def test_arxiv_search_registered(self):
        from app.tools.registry import get_tool
        spec = get_tool("arxiv_search")
        self.assertIsNotNone(spec)
        self.assertIn("academic", spec.tags)
        self.assertIn("arxiv", spec.tags)

    def test_semantic_scholar_registered(self):
        from app.tools.registry import get_tool
        spec = get_tool("semantic_scholar_search")
        self.assertIsNotNone(spec)
        self.assertIn("academic", spec.tags)

    def test_total_tool_count_includes_new(self):
        from app.tools.registry import list_tools
        tools = list_tools()
        self.assertGreaterEqual(len(tools), 10)  # 8 from Phase 5 + 2 new


# ── Cost tracking in trace logger ─────────────────────────────────────────

class TraceLoggerCostTests(unittest.TestCase):
    """Test that trace logger passes through token/cost parameters."""

    def test_record_tool_result_accepts_token_params(self):
        from app.trace.logger import record_tool_result
        import inspect

        sig = inspect.signature(record_tool_result)
        self.assertIn("token_in", sig.parameters)
        self.assertIn("token_out", sig.parameters)
        self.assertIn("estimated_cost", sig.parameters)

    def test_record_trace_event_accepts_token_params(self):
        from app.trace.logger import record_trace_event
        import inspect

        sig = inspect.signature(record_trace_event)
        self.assertIn("token_in", sig.parameters)
        self.assertIn("token_out", sig.parameters)
        self.assertIn("estimated_cost", sig.parameters)


# ── Config ─────────────────────────────────────────────────────────────────

class Phase6ConfigTests(unittest.TestCase):
    """Test Phase 6 configuration additions."""

    def test_semantic_scholar_api_key_default_none(self):
        from app.config import Settings
        s = Settings()
        self.assertIsNone(s.semantic_scholar_api_key)

    def test_safe_runtime_summary_includes_semantic_scholar(self):
        from app.config import settings
        summary = settings.get_safe_runtime_config_summary()
        self.assertIn("semantic_scholar_configured", summary)
        self.assertFalse(summary["semantic_scholar_configured"])

    def test_from_env_sets_semantic_scholar(self):
        import os
        os.environ["SEMANTIC_SCHOLAR_API_KEY"] = "test-key-123"
        try:
            from app.config import Settings
            s = Settings.from_env()
            self.assertEqual(s.semantic_scholar_api_key, "test-key-123")
        finally:
            del os.environ["SEMANTIC_SCHOLAR_API_KEY"]


# ── Demo script smoke ──────────────────────────────────────────────────────

class DemoScriptTests(unittest.TestCase):
    """Verify demo script structure."""

    def test_demo_script_exists(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "demo_deep_research.py"
        self.assertTrue(path.exists(), f"Demo script not found at {path}")

    def test_demo_script_imports(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "scripts" / "demo_deep_research.py"
        spec = importlib.util.spec_from_file_location("demo", path)
        self.assertIsNotNone(spec)

    def test_preset_questions(self):
        from scripts.demo_deep_research import PRESET_QUESTIONS
        self.assertEqual(len(PRESET_QUESTIONS), 3)
        for q in PRESET_QUESTIONS:
            self.assertIsInstance(q, str)
            self.assertTrue(len(q) > 5)


if __name__ == "__main__":
    unittest.main()
