"""Unit tests for Phase 3: report restructuring + Skills system."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure we can import from the app package
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SubQueryGroupingTests(unittest.TestCase):
    """Test sub-query grouping logic in reporter.py (TASK.md §5.1)."""

    def test_build_groups_from_traces_with_sub_query(self):
        from app.agent.reporter import _build_sub_query_groups

        # Create mock traces with sub_query
        trace1 = MagicMock()
        trace1.sub_query = "子问题A"
        trace1.step_no = 1
        trace1.trace_id = "t1"

        trace2 = MagicMock()
        trace2.sub_query = "子问题A"
        trace2.step_no = 2
        trace2.trace_id = "t2"

        trace3 = MagicMock()
        trace3.sub_query = "子问题B"
        trace3.step_no = 3
        trace3.trace_id = "t3"

        groups = _build_sub_query_groups({}, [trace1, trace2, trace3], None)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].sub_query, "子问题A")
        self.assertEqual(groups[0].step_nos, [1, 2])
        self.assertEqual(groups[1].sub_query, "子问题B")
        self.assertEqual(groups[1].step_nos, [3])

    def test_build_groups_no_sub_query_returns_empty(self):
        from app.agent.reporter import _build_sub_query_groups

        trace1 = MagicMock()
        trace1.sub_query = ""
        trace1.step_no = 1
        trace1.trace_id = "t1"

        groups = _build_sub_query_groups({}, [trace1], None)
        # Single group with empty sub_query — still one group
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sub_query, "")

    def test_build_groups_no_traces_returns_empty(self):
        from app.agent.reporter import _build_sub_query_groups

        groups = _build_sub_query_groups({}, [], None)
        self.assertEqual(len(groups), 0)

    def test_build_groups_with_provenance(self):
        from app.agent.reporter import _build_sub_query_groups

        trace1 = MagicMock()
        trace1.sub_query = "主题1"
        trace1.step_no = 1
        trace1.trace_id = "t1"

        provenance = {
            "passages": [
                {
                    "passage_id": "p1",
                    "trace_id": "t1",
                    "text": "evidence text",
                    "content_basis": "full_text",
                },
            ],
            "citations": [
                {
                    "citation_id": "cit1",
                    "report_claim_id": "rc1",
                    "passage_id": "p1",
                    "citation_label": "CIT-001-01",
                },
            ],
            "report_claims": [
                {
                    "report_claim_id": "rc1",
                    "claim_text": "Test claim",
                    "ordinal": 1,
                },
            ],
        }

        groups = _build_sub_query_groups({}, [trace1], provenance)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].passages), 1)
        self.assertEqual(len(groups[0].citations), 1)
        self.assertEqual(len(groups[0].claims), 1)

    def test_content_basis_label(self):
        from app.agent.reporter import _content_basis_label

        self.assertIn("全文", _content_basis_label({"content_basis": "full_text"}))
        self.assertIn("部分", _content_basis_label({"content_basis": "partial"}))
        self.assertIn("摘要", _content_basis_label({"content_basis": "snippet_only"}))
        self.assertIn("摘要", _content_basis_label({}))  # default

    def test_build_content_basis_map(self):
        from app.agent.reporter import _build_content_basis_map

        provenance = {
            "passages": [
                {"trace_id": "t1", "content_basis": "full_text"},
                {"trace_id": "t2", "content_basis": "snippet_only"},
            ]
        }
        m = _build_content_basis_map(provenance)
        self.assertIn("t1", m)
        self.assertIn("t2", m)
        self.assertIn("全文", m["t1"])
        self.assertIn("摘要", m["t2"])

    def test_build_content_basis_map_empty(self):
        from app.agent.reporter import _build_content_basis_map

        self.assertEqual(_build_content_basis_map(None), {})
        self.assertEqual(_build_content_basis_map({}), {})

    def test_grouped_final_answer_renders_claims(self):
        from app.agent.reporter import (
            SubQueryGroup,
            _render_grouped_final_answer,
        )

        trace = MagicMock()
        trace.sub_query = "子查询1"
        trace.step_no = 1
        trace.trace_id = "t1"
        trace.tool_name = "tavily_search"
        trace.status = "success"
        trace.output_json = json.dumps({})
        trace.output_summary = "test"
        trace.error_message = None
        trace.input_summary = ""
        trace.created_at = None
        trace.finished_at = None

        group = SubQueryGroup(
            sub_query="子查询1",
            step_nos=[1],
            traces=[trace],
            claims=[
                {
                    "report_claim_id": "rc1",
                    "claim_text": "测试发现：LangGraph 优于 CrewAI",
                    "ordinal": 1,
                }
            ],
            citations=[
                {
                    "citation_id": "cit1",
                    "report_claim_id": "rc1",
                    "passage_id": "p1",
                    "citation_label": "CIT-001-01",
                }
            ],
            passages=[
                {
                    "passage_id": "p1",
                    "trace_id": "t1",
                    "text": "LangGraph provides better orchestration than CrewAI",
                    "content_basis": "full_text",
                }
            ],
        )

        lines = _render_grouped_final_answer("test task", [], [], [group])
        text = "\n".join(lines)
        self.assertIn("子问题 1", text)
        self.assertIn("测试发现", text)
        self.assertIn("CIT-001-01", text)
        self.assertIn("全文", text)


class SkillLoaderTests(unittest.TestCase):
    """Test Skill JSON file loading (TASK.md §5.2)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_skill(self, filename: str, data: dict) -> Path:
        path = self.skills_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_load_valid_skill(self):
        from app.skills.loader import load_skill_from_file

        path = self._write_skill("test.json", {
            "name": "test_skill",
            "version": "1.0",
            "description": "A test skill",
            "required_tools": ["tavily_search"],
            "parameters": {
                "query": {"type": "string", "required": True},
            },
            "steps": [
                {
                    "tool_name": "tavily_search",
                    "goal": "Search the web",
                    "arguments": {"query": "{{parameters.query}}"},
                },
                {
                    "tool_name": "report_writer",
                    "goal": "Generate report",
                    "arguments": {},
                },
            ],
        })

        skill = load_skill_from_file(path)
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "test_skill")
        self.assertEqual(skill.version, "1.0")
        self.assertEqual(len(skill.steps), 2)
        self.assertEqual(skill.steps[0].tool_name, "tavily_search")
        self.assertEqual(skill.steps[0].arguments, {"query": "{{parameters.query}}"})
        self.assertEqual(len(skill.required_tools), 1)

    def test_load_skill_with_arguments_from(self):
        from app.skills.loader import load_skill_from_file

        path = self._write_skill("test2.json", {
            "name": "test_skill2",
            "steps": [
                {"tool_name": "tavily_search", "arguments": {"query": "test"}},
                {
                    "tool_name": "web_fetcher",
                    "arguments": {"urls": []},
                    "arguments_from": {"step_no": 1, "field": "results"},
                },
            ],
        })

        skill = load_skill_from_file(path)
        self.assertIsNotNone(skill)
        self.assertEqual(len(skill.steps), 2)
        self.assertIsNotNone(skill.steps[1].arguments_from)
        self.assertEqual(skill.steps[1].arguments_from["step_no"], 1)

    def test_load_invalid_json_returns_none(self):
        from app.skills.loader import load_skill_from_file

        path = self.skills_dir / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(load_skill_from_file(path))

    def test_load_missing_file_returns_none(self):
        from app.skills.loader import load_skill_from_file

        self.assertIsNone(load_skill_from_file(self.skills_dir / "nonexistent.json"))

    def test_validate_skill_with_registered_tools(self):
        from app.skills.loader import load_skill_from_file, validate_skill

        path = self._write_skill("valid.json", {
            "name": "valid_skill",
            "required_tools": ["tavily_search", "web_fetcher"],
            "steps": [
                {"tool_name": "tavily_search", "arguments": {"query": "test"}},
                {"tool_name": "report_writer", "arguments": {}},
            ],
        })
        skill = load_skill_from_file(path)
        # All these tools exist in the default registry
        available = {"tavily_search", "web_fetcher", "report_writer"}
        errors = validate_skill(skill, available)
        self.assertEqual(errors, [])

    def test_validate_skill_missing_tool(self):
        from app.skills.loader import load_skill_from_file, validate_skill

        path = self._write_skill("missing_tool.json", {
            "name": "missing_tool",
            "steps": [
                {"tool_name": "nonexistent_tool", "arguments": {}},
            ],
        })
        skill = load_skill_from_file(path)
        errors = validate_skill(skill, {"tavily_search"})
        self.assertTrue(len(errors) > 0)
        self.assertIn("nonexistent_tool", errors[0])

    def test_validate_empty_name(self):
        from app.skills.loader import load_skill_from_file, validate_skill

        path = self._write_skill("empty.json", {
            "name": "",
            "steps": [],
        })
        skill = load_skill_from_file(path)
        errors = validate_skill(skill, set())
        self.assertTrue(any("name" in e.lower() for e in errors))

    def test_load_all_skills_from_directory(self):
        from app.skills.loader import load_all_skills

        self._write_skill("a.json", {"name": "skill_a", "steps": [{"tool_name": "tavily_search", "arguments": {}}]})
        self._write_skill("b.json", {"name": "skill_b", "steps": [{"tool_name": "web_fetcher", "arguments": {}}]})

        skills = load_all_skills(self.skills_dir)
        self.assertEqual(len(skills), 2)
        self.assertIn("skill_a", skills)
        self.assertIn("skill_b", skills)

    def test_load_all_skills_empty_dir(self):
        from app.skills.loader import load_all_skills

        skills = load_all_skills(self.skills_dir)
        self.assertEqual(skills, {})

    def test_load_all_skills_nonexistent_dir(self):
        from app.skills.loader import load_all_skills

        skills = load_all_skills(Path("/nonexistent/path/xyz"))
        self.assertEqual(skills, {})


class SkillRegistryTests(unittest.TestCase):
    """Test skill registry initialization (TASK.md §5.3)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = Path(self.tmpdir)
        self._write_skill("test.json", {
            "name": "test_skill",
            "version": "1.0",
            "description": "A test",
            "required_tools": ["tavily_search"],
            "parameters": {"query": {"type": "string", "required": True}},
            "steps": [
                {"tool_name": "tavily_search", "goal": "Search", "arguments": {"query": "{{parameters.query}}"}},
                {"tool_name": "report_writer", "goal": "Report", "arguments": {}},
            ],
        })

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_skill(self, filename: str, data: dict) -> Path:
        path = self.skills_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_init_and_list_skills(self):
        from app.skills.registry import init_skill_registry, list_skills, get_skill

        init_skill_registry(self.skills_dir)
        skills = list_skills()
        self.assertGreaterEqual(len(skills), 1)
        test_skill = get_skill("test_skill")
        self.assertIsNotNone(test_skill)
        self.assertEqual(test_skill.name, "test_skill")

    def test_get_skill_not_found(self):
        from app.skills.registry import init_skill_registry, get_skill

        init_skill_registry(self.skills_dir)
        self.assertIsNone(get_skill("nonexistent"))

    def test_invalid_skill_marked_as_invalid(self):
        from app.skills.registry import init_skill_registry, list_skills

        self._write_skill("bad.json", {
            "name": "bad_skill",
            "steps": [{"tool_name": "nonexistent_tool_xyz", "arguments": {}}],
        })
        init_skill_registry(self.skills_dir)
        skills = list_skills()
        bad = next((s for s in skills if s.name == "bad_skill"), None)
        self.assertIsNotNone(bad)
        self.assertEqual(bad.status, "invalid")
        self.assertIsNotNone(bad.error)

    def test_reload_skills(self):
        from app.skills.registry import init_skill_registry, list_skills, reload_skills

        init_skill_registry(self.skills_dir)
        self.assertEqual(len(list_skills()), 1)

        self._write_skill("new.json", {
            "name": "new_skill",
            "steps": [{"tool_name": "tavily_search", "arguments": {}}],
        })
        reload_skills(self.skills_dir)
        self.assertEqual(len(list_skills()), 2)

    def test_empty_dir_init(self):
        from app.skills.registry import init_skill_registry, list_skills

        empty_dir = Path(self.tmpdir) / "empty"
        empty_dir.mkdir()
        init_skill_registry(empty_dir)
        self.assertEqual(list_skills(), [])


class SkillPlannerIntegrationTests(unittest.TestCase):
    """Test planner skill_name integration (TASK.md §5.4)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = Path(self.tmpdir)
        self._write_skill("deep_web_research.json", {
            "name": "deep_web_research",
            "version": "1.0",
            "description": "Deep web research",
            "required_tools": ["tavily_search", "web_fetcher"],
            "parameters": {
                "query": {"type": "string", "required": True},
                "max_urls": {"type": "integer", "default": 5},
            },
            "steps": [
                {
                    "tool_name": "tavily_search",
                    "goal": "Discover URLs",
                    "arguments": {
                        "query": "{{parameters.query}}",
                        "max_results": "{{parameters.max_urls}}",
                    },
                },
                {
                    "tool_name": "web_fetcher",
                    "goal": "Fetch full text",
                    "arguments": {"urls": [], "max_chars": 8000},
                    "arguments_from": {"step_no": "{{steps[0].step_no}}", "field": "results"},
                },
                {
                    "tool_name": "report_writer",
                    "goal": "Generate report",
                    "arguments": {},
                },
            ],
        })

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_skill(self, filename: str, data: dict) -> Path:
        path = self.skills_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_plan_task_with_skill_name(self):
        from app.skills.registry import init_skill_registry
        from app.agent.planner import plan_task

        init_skill_registry(self.skills_dir)

        plan = plan_task(
            "调研 AI Agent 框架对比",
            skill_name="deep_web_research",
        )

        self.assertEqual(plan.get("planner_source"), "skill")
        self.assertEqual(plan.get("skill_name"), "deep_web_research")
        steps = plan.get("steps", [])
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["tool_name"], "tavily_search")
        self.assertEqual(steps[1]["tool_name"], "web_fetcher")
        self.assertEqual(steps[2]["tool_name"], "report_writer")

    def test_plan_task_skill_not_found_falls_back(self):
        from app.skills.registry import init_skill_registry
        from app.agent.planner import plan_task

        init_skill_registry(self.skills_dir)

        plan = plan_task(
            "调研 AI Agent 框架对比",
            skill_name="nonexistent_skill",
        )

        self.assertNotEqual(plan.get("planner_source"), "skill")

    def test_skill_placeholder_resolution(self):
        from app.agent.planner import _resolve_skill_placeholder

        compiled = [{"step_no": 1, "tool_name": "tavily_search"}]
        params = {"query": "test query", "max_urls": 5}

        self.assertEqual(
            _resolve_skill_placeholder("parameters.query", "fallback", compiled, params),
            "test query",
        )
        self.assertEqual(
            _resolve_skill_placeholder("parameters.max_urls", "fallback", compiled, params),
            5,  # int preserved after Phase 5 Bug 1 fix
        )
        self.assertEqual(
            _resolve_skill_placeholder("steps[0].step_no", "fallback", compiled, params),
            1,  # int preserved after Phase 5 Bug 1 fix
        )

    def test_fill_skill_arguments(self):
        from app.agent.planner import _fill_skill_arguments

        arguments = {
            "query": "{{parameters.query}}",
            "max_results": "{{parameters.max_urls}}",
            "limit": 10,  # non-string, should pass through
        }
        compiled = [{"step_no": 1, "tool_name": "t"}]
        params = {"query": "my query", "max_urls": 5}

        result = _fill_skill_arguments(arguments, "fallback", compiled, params)
        self.assertEqual(result["query"], "my query")
        self.assertEqual(result["max_results"], 5)  # int preserved after Phase 5 Bug 1 fix
        self.assertEqual(result["limit"], 10)

    def test_fill_skill_arguments_from(self):
        from app.agent.planner import _fill_skill_arguments_from

        args_from = {"step_no": "{{steps[0].step_no}}", "field": "results"}
        compiled = [{"step_no": 1, "tool_name": "t"}]
        params = {}

        result = _fill_skill_arguments_from(args_from, "task", compiled, params)
        self.assertEqual(result["step_no"], 1)
        self.assertEqual(result["field"], "results")

    def test_skill_to_plan_skips_blocked_tools(self):
        from app.agent.planner import _skill_to_plan
        from app.skills.models import SkillDefinition, SkillStep

        skill = SkillDefinition(
            name="test",
            version="1.0",
            steps=[
                SkillStep(tool_name="tavily_search", arguments={"query": "test"}),
                SkillStep(tool_name="web_fetcher", arguments={"urls": []}),
                SkillStep(tool_name="report_writer", arguments={}),
            ],
        )
        plan = _skill_to_plan(skill, "test task", ["tavily_search", "report_writer"])
        steps = plan.get("steps", [])
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["tool_name"], "tavily_search")
        self.assertEqual(steps[1]["tool_name"], "report_writer")

    def test_plan_task_without_skill_name_unchanged(self):
        from app.skills.registry import init_skill_registry
        from app.agent.planner import plan_task

        init_skill_registry(self.skills_dir)

        plan = plan_task("分析本地文件", allowed_tools=["file_reader", "report_writer"])
        self.assertNotEqual(plan.get("planner_source"), "skill")
        self.assertGreaterEqual(len(plan.get("steps", [])), 1)


class PresetSkillFileTests(unittest.TestCase):
    """Verify the 4 preset Skill JSON files are well-formed (TASK.md §5.5)."""

    def setUp(self):
        self.skills_dir = Path(__file__).resolve().parents[1] / "workspace" / "skills"

    def test_all_preset_skills_load(self):
        from app.skills.loader import load_all_skills

        skills = load_all_skills(self.skills_dir)
        self.assertEqual(len(skills), 4, f"Expected 4 skills, got {len(skills)}: {list(skills.keys())}")

        for name, skill in skills.items():
            with self.subTest(skill=name):
                self.assertTrue(skill.name, f"{name}: empty name")
                self.assertTrue(skill.steps, f"{name}: no steps")
                for i, s in enumerate(skill.steps):
                    self.assertTrue(s.tool_name, f"{name} step {i+1}: empty tool_name")

    def test_each_skill_has_required_fields(self):
        from app.skills.loader import load_all_skills

        skills = load_all_skills(self.skills_dir)
        for name, skill in skills.items():
            with self.subTest(skill=name):
                self.assertTrue(skill.name)
                self.assertTrue(skill.version)
                self.assertTrue(skill.description)
                self.assertTrue(len(skill.steps) >= 1, f"{name} has no steps")
                self.assertTrue(
                    any(s.tool_name == "report_writer" for s in skill.steps),
                    f"{name} missing report_writer step",
                )


class EvidenceContentBasisTests(unittest.TestCase):
    """Test content_basis is exposed in provenance passage dicts (TASK.md §5.1)."""

    def test_passage_dict_includes_content_basis(self):
        from app.evidence.service import _passage_dict
        from unittest.mock import MagicMock

        passage = MagicMock()
        passage.passage_id = "p1"
        passage.snapshot_id = "s1"
        passage.trace_id = "t1"
        passage.ordinal = 1
        passage.content_hash = "abc"
        passage.text = "test"
        passage.locator_json = "{}"
        passage.metadata_json = "{}"
        passage.content_basis = "full_text"

        d = _passage_dict(passage)
        self.assertEqual(d["content_basis"], "full_text")


if __name__ == "__main__":
    unittest.main()
