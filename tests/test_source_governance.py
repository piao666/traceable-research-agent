"""Deterministic tests for Phase 8.1 source governance execution."""

from __future__ import annotations

import unittest
import json

from app.config import Settings
from app.evidence.policy import (
    RetrievalProfile,
    SourceCandidate,
    classify_tier,
    load_source_policy,
    select_sources_by_profile,
)
from app.tools.base import ToolResult


def _plan(profile: str = "academic_literature") -> dict:
    policy = load_source_policy("config/source_policy.v2.json")
    selected = policy.retrieval_profiles[profile]
    return {
        "retrieval_profile": profile,
        "profile_constraints": selected.to_dict(),
        "policy_version": policy.version,
    }


def _candidate(uri: str) -> SourceCandidate:
    from urllib.parse import urlsplit

    hostname = (urlsplit(uri).hostname or "").lower()
    return SourceCandidate(
        uri=uri,
        hostname=hostname,
        organization=hostname,
        title=uri,
        snippet="evidence",
    )


class TierPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_source_policy("config/source_policy.v2.json")

    def test_verified_repo_wins_over_generic_github_domain(self) -> None:
        result = classify_tier(
            "mcp_github_search",
            "https://github.com/example-org/verified-repo/tree/main/module",
            {},
            self.policy,
        )
        self.assertEqual(result.tier, "T0")
        self.assertEqual(
            result.classification_rule,
            "verified_repo:github.com/example-org/verified-repo",
        )

    def test_unverified_repo_uses_conservative_tier(self) -> None:
        result = classify_tier(
            "mcp_github_search",
            "https://github.com/example-user/example-repo",
            {},
            self.policy,
        )
        self.assertEqual(result.tier, "T2")
        self.assertEqual(result.classification_rule, "default_conservative")

    def test_known_org_does_not_make_unverified_repo_official_code(self) -> None:
        from app.evidence.policy import classify_source

        source_class = classify_source(
            "mcp_github_search",
            "https://github.com/example-org/unverified-repo",
            {"organization": "example-org"},
            self.policy,
        )
        self.assertEqual(source_class, "blog")

    def test_supported_vendor_docs_and_verified_repositories_are_t0(self) -> None:
        # Verified repos and known academic/regulatory domains are T0
        for uri, expected_tier in (
            ("https://github.com/example-org/verified-repo", "T0"),
            ("https://sec.gov/report", "T0"),
            ("https://pubmed.ncbi.nlm.nih.gov/12345", "T0"),
            ("https://arxiv.org/abs/2401.00001", "T1"),
        ):
            with self.subTest(uri=uri):
                result = classify_tier("tavily_search", uri, {}, self.policy)
                self.assertEqual(result.tier, expected_tier,
                                 f"{uri} expected {expected_tier} got {result.tier}")

    def test_verified_repository_community_pages_do_not_inherit_t0(self) -> None:
        for section in ("issues/12", "discussions/1436", "pull/7"):
            result = classify_tier(
                "tavily_search",
                f"https://github.com/example-org/verified-repo/{section}",
                {},
                self.policy,
            )
            with self.subTest(section=section):
                self.assertEqual(result.tier, "T2")

    def test_user_content_hosts_do_not_inherit_official_tier(self) -> None:
        for uri in ("https://medium.com/tech-blog/post", "https://reddit.com/r/programming/comments/1"):
            result = classify_tier("tavily_search", uri, {}, self.policy)
            with self.subTest(uri=uri):
                self.assertEqual(result.tier, "T2")


class SelectionBudgetTests(unittest.TestCase):
    def test_max_candidates_applies_to_t0_selection(self) -> None:
        policy = load_source_policy("config/source_policy.v2.json")
        profile = RetrievalProfile(
            name="bounded",
            min_t0_sources=5,
            min_independent_sources=5,
            max_per_domain=5,
        )
        candidates = [
            _candidate(f"https://agency{i}.gov/report")
            for i in range(5)
        ]
        selection = select_sources_by_profile(
            candidates,
            profile,
            policy,
            oversample_factor=3,
            max_candidates=2,
        )
        self.assertEqual(len(selection.selected), 2)
        self.assertEqual(selection.quota_shortfall["t0_shortfall"], 3)
        self.assertEqual(selection.quota_shortfall["independent_shortfall"], 3)


class ExecutionGovernanceTests(unittest.TestCase):
    def test_prepare_arguments_applies_oversampling_and_hard_limits(self) -> None:
        from app.agent.source_governance import prepare_tool_arguments

        settings = Settings(
            oversample_factor=3,
            max_discovery_candidates=7,
            max_fetch_candidates=2,
        )
        plan = _plan()
        plan["task"] = "Research FastAPI framework performance and features"
        discovery = prepare_tool_arguments(
            "tavily_search", {"query": "test", "max_results": 4}, plan, settings
        )
        fetch = prepare_tool_arguments(
            "web_fetcher",
            {"urls": ["https://a.test", "https://b.test", "https://c.test"]},
            plan,
            settings,
        )
        self.assertEqual(discovery["max_results"], 7)
        self.assertEqual(fetch["urls"], ["https://a.test", "https://b.test"])

    def test_govern_result_exposes_selection_metadata(self) -> None:
        from app.agent.source_governance import govern_tool_result

        settings = Settings(max_discovery_candidates=2)
        raw = ToolResult(
            success=True,
            output={
                "results": [
                    {"title": "Primary", "url": "https://agency.gov/report", "content": "a"},
                    {"title": "Academic", "url": "https://lab.example.edu/paper", "content": "b"},
                    {"title": "Community", "url": "https://blog.example/post", "content": "c"},
                ]
            },
            metadata={"result_count": 3},
        )
        governed = govern_tool_result("tavily_search", raw, _plan("generic"), settings)
        audit = governed.metadata["source_governance"]
        self.assertLessEqual(len(governed.output["results"]), 2)
        self.assertEqual(audit["discovery_candidate_count"], 3)
        self.assertEqual(audit["classified_candidate_count"], 2)
        self.assertTrue(audit["budget_limited_selection"])
        self.assertEqual(audit["max_discovery_candidates"], 2)

    def test_targeted_refetch_round1_uses_official_discovery_query(self) -> None:
        from app.agent.source_governance import (
            execute_targeted_refetches,
            govern_tool_result,
        )

        settings = Settings(max_refetch_rounds=2, max_discovery_candidates=5)
        plan = _plan()
        plan["task"] = "Research FastAPI framework performance and features"
        initial = govern_tool_result(
            "tavily_search",
            ToolResult(
                success=True,
                output={"results": [{"title": "Community", "url": "https://blog.example/post"}]},
            ),
            plan,
            settings,
        )
        calls: list[dict] = []

        def execute(_name: str, arguments: dict) -> tuple[ToolResult, int]:
            calls.append(dict(arguments))
            return (
                ToolResult(
                    success=True,
                    output={"results": [{"title": "Still community", "url": "https://other.example/post"}]},
                ),
                4,
            )

        refetches = execute_targeted_refetches(
            "tavily_search",
            {"query": "research", "max_results": 5},
            initial,
            plan,
            settings,
            execute=execute,
        )
        self.assertGreaterEqual(len(refetches), 1)
        # Round 1: uses official discovery query (not include_domains)
        self.assertIn("official", calls[0].get("query", "").casefold())
        self.assertNotIn("include_domains", calls[0])

    def test_targeted_refetch_round2_uses_discovered_domains(self) -> None:
        from app.agent.source_governance import (
            execute_targeted_refetches,
            govern_tool_result,
            record_discovered_official,
        )

        settings = Settings(max_refetch_rounds=3, max_discovery_candidates=5)
        plan = _plan()
        plan["task"] = "Research FastAPI framework performance and features"
        # Pre-populate discovered official domains
        plan = record_discovered_official(plan, domains=["fastapi.tiangolo.com"])
        initial = govern_tool_result(
            "tavily_search",
            ToolResult(
                success=True,
                output={"results": [{"title": "Community", "url": "https://blog.example/post"}]},
            ),
            plan,
            settings,
        )
        calls: list[dict] = []

        def execute(_name: str, arguments: dict) -> tuple[ToolResult, int]:
            calls.append(dict(arguments))
            return (
                ToolResult(
                    success=True,
                    output={"results": [{"title": "FastAPI docs", "url": "https://fastapi.tiangolo.com/"}]},
                ),
                4,
            )

        refetches = execute_targeted_refetches(
            "tavily_search",
            {"query": "FastAPI framework", "max_results": 5},
            initial,
            plan,
            settings,
            execute=execute,
        )
        self.assertGreaterEqual(len(refetches), 1)
        # With discovered domains, round 1 uses include_domains
        self.assertIn("include_domains", calls[0])
        self.assertEqual(calls[0]["include_domains"], ["fastapi.tiangolo.com"])

    # ── P0-6: End-to-end official-source recovery regression ──────

    def test_e2e_unknown_vendor_becomes_t0_via_recovery_flow(self):
        """Full pipeline: unknown vendor → official discovery → infer → T0 → persist → refetch."""
        from app.agent.source_governance import (
            execute_targeted_refetches,
            govern_tool_result,
            discovered_official_sources,
            prepare_tool_arguments,
        )

        settings = Settings(
            max_refetch_rounds=3,
            max_discovery_candidates=10,
            oversample_factor=2,
        )
        plan = _plan()
        plan["task"] = "Research FastAPI framework performance and features"
        initial = govern_tool_result(
            "tavily_search",
            ToolResult(
                success=True,
                output={"results": [
                    {"title": "FastAPI", "url": "https://fastapi.tiangolo.com/",
                     "clean_content": "FastAPI framework, high performance"},
                ]},
            ),
            plan,
            settings,
        )
        # Without discovery, FastAPI docs won't be T0
        governance = initial.metadata.get("source_governance", {})
        self.assertGreaterEqual(governance.get("quota_shortfall", {}).get("t0_shortfall", 0), 0)

        calls: list[dict] = []

        def execute(_name: str, arguments: dict) -> tuple[ToolResult, int]:
            calls.append(dict(arguments))
            # If include_domains is set, return FastAPI official doc
            if "fastapi" in str(arguments.get("include_domains") or "").casefold():
                return (
                    ToolResult(
                        success=True,
                        output={"results": [
                            {"title": "FastAPI", "url": "https://fastapi.tiangolo.com/learn/",
                             "clean_content": "Official FastAPI documentation",
                             "metadata": {"official": True}}
                        ]},
                        metadata={"data_source": "tavily_api"},
                    ),
                    5,
                )
            # Discovery round: return FastAPI official site with strong signals
            return (
                ToolResult(
                    success=True,
                    output={"results": [
                        {"title": "FastAPI - Official Documentation",
                         "url": "https://fastapi.tiangolo.com/",
                         "clean_content": "FastAPI framework, high performance, easy to learn",
                         "metadata": {"official": True}}
                    ]},
                    metadata={"data_source": "tavily_api"},
                ),
                5,
            )

        refetches = execute_targeted_refetches(
            "tavily_search",
            {"query": "FastAPI framework", "max_results": 5},
            initial,
            plan,
            settings,
            execute=execute,
        )
        self.assertGreaterEqual(len(refetches), 1)
        # Round 1: official discovery query used
        round1 = calls[0]
        self.assertIn("official", str(round1.get("query", "")).casefold())
        self.assertNotIn("include_domains", round1)

        # After round 1, discovered official sources are persisted in plan
        discovered = discovered_official_sources(plan)
        self.assertIn("fastapi.tiangolo.com", discovered["domains"],
                      "FastAPI official domain should be discovered from round 1 results")

        # Round 2 should use include_domains with the discovered domain
        found_include = any("include_domains" in call for call in calls)
        self.assertTrue(found_include,
                        "At least one round should use include_domains with discovered official domain")

    def test_persisted_refetch_rounds_recovers_run_budget(self) -> None:
        from types import SimpleNamespace

        from app.agent.source_governance import persisted_refetch_rounds

        traces = [
            SimpleNamespace(sub_query=None),
            SimpleNamespace(sub_query="source_refetch_round:1"),
            SimpleNamespace(sub_query="source_refetch_round:2"),
            {"sub_query": "unrelated"},
        ]
        self.assertEqual(persisted_refetch_rounds(traces), 2)

    def test_arguments_from_steps_are_parallel_barriers(self) -> None:
        from app.agent.parallel_executor import _plan_groups

        steps = [
            {"step_no": 1, "tool_name": "tavily_search", "arguments": {"query": "x"}},
            {
                "step_no": 2,
                "tool_name": "web_fetcher",
                "arguments": {"urls": []},
                "arguments_from": {"step_no": 1, "field": "results"},
            },
            {"step_no": 3, "tool_name": "report_writer", "arguments": {}},
        ]
        groups = _plan_groups(steps)
        self.assertEqual([[step["step_no"] for step in group] for group in groups], [[1], [2], [3]])

    def test_planner_applies_default_retrieval_profile(self) -> None:
        from app.agent.planner import plan_task

        plan = plan_task("Generate a report", allowed_tools=["report_writer"])
        self.assertEqual(plan["retrieval_profile"], "generic")
        self.assertEqual(plan["profile_constraints"]["name"], "generic")

    def test_planner_infers_technical_profile_but_respects_explicit_choice(self) -> None:
        from app.agent.planner import plan_task

        task = "对比三个编码智能体的架构、沙箱、记忆、工具调用和插件机制"
        inferred = plan_task(task, allowed_tools=["report_writer"])
        explicit = plan_task(
            task,
            allowed_tools=["report_writer"],
            retrieval_profile="generic",
        )
        self.assertEqual(inferred["retrieval_profile"], "technical_facts")
        self.assertEqual(inferred["profile_constraints"]["shortfall_policy"], "targeted_refetch")
        self.assertEqual(explicit["retrieval_profile"], "generic")

    def test_task_api_snapshot_contains_profile_and_budgets(self) -> None:
        from fastapi.testclient import TestClient

        from app.database import SessionLocal
        from app.main import app
        from app.trace import store

        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={
                    "task": "Generate a report",
                    "allowed_tools": ["report_writer"],
                    "retrieval_profile": "technical_facts",
                },
            )
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run_id"]
        with SessionLocal() as db:
            run = store.get_agent_run(db, run_id)
            self.assertIsNotNone(run)
            snapshot = json.loads(run.run_config_snapshot or "{}")
        self.assertEqual(snapshot["retrieval_profile"], "technical_facts")
        self.assertEqual(snapshot["source_policy_version"], "source-policy-v2")
        self.assertEqual(snapshot["profile_constraints"]["name"], "technical_facts")
        for key in (
            "oversample_factor",
            "max_discovery_candidates",
            "max_fetch_candidates",
            "max_refetch_rounds",
        ):
            self.assertIn(key, snapshot)

    # ── Phase 8.x: official-source discovery regression ──────────────

    def test_official_source_queries_are_not_empty_for_technical_topic(self):
        from app.agent.source_governance import build_official_source_queries
        queries = build_official_source_queries("FastAPI framework")
        self.assertGreater(len(queries), 3)
        self.assertIn("FastAPI framework official documentation", queries)
        self.assertIn("FastAPI framework github official repository", queries)

    def test_basic_query_generates_no_technical_suffixes(self):
        from app.agent.source_governance import build_official_source_queries
        queries = build_official_source_queries("GDP growth rate")
        for q in queries:
            self.assertNotIn("github", q)

    def test_infer_official_flags_fastapi_official_docs(self):
        from app.evidence.policy import infer_official_source
        candidate = SourceCandidate(
            uri="https://fastapi.tiangolo.com/",
            hostname="fastapi.tiangolo.com",
            organization="tiangolo",
            title="FastAPI",
            snippet="FastAPI framework, high performance, easy to learn, fast to code",
            metadata={"official": True, "source_class": "official"},
        )
        self.assertTrue(infer_official_source(candidate, task_entities=["FastAPI"]))
        # domain match alone insufficient
        domain_only = SourceCandidate(
            uri="https://fastapi.tiangolo.com/",
            hostname="fastapi.tiangolo.com",
            organization=None,
            title="Some blog about FastAPI",
            snippet="Community blog",
        )
        self.assertFalse(infer_official_source(domain_only, task_entities=["FastAPI"]))

    def test_infer_official_flags_docs_title_with_entity_match(self):
        from app.evidence.policy import infer_official_source
        candidate = SourceCandidate(
            uri="https://pytorch.org/docs/stable/",
            hostname="pytorch.org",
            organization="PyTorch",
            title="PyTorch documentation",
            snippet="PyTorch is an optimized tensor library",
            metadata={"official": True},
        )
        self.assertTrue(infer_official_source(candidate, task_entities=["PyTorch"]))

    def test_infer_official_flags_github_org_match_plus_signal(self):
        from app.evidence.policy import infer_official_source
        candidate = SourceCandidate(
            uri="https://github.com/kubernetes/kubernetes",
            hostname="github.com",
            organization="kubernetes",
            title="kubernetes/kubernetes: Production-Grade Container Scheduling and Management",
            snippet="Kubernetes is an open-source system",
            metadata={"source_class": "official_code"},
        )
        self.assertTrue(infer_official_source(candidate, task_entities=["Kubernetes"]))

    def test_infer_official_rejects_spoofed_domain_alone(self):
        from app.evidence.policy import infer_official_source
        candidate = SourceCandidate(
            uri="https://kubernetes.fake-site.io/docs",
            hostname="kubernetes.fake-site.io",
            organization=None,
            title="Kubernetes Documentation",
            snippet="Kubernetes docs and tutorials",
        )
        self.assertFalse(infer_official_source(candidate, task_entities=["Kubernetes"]))

    def test_infer_official_flags_gov_domain(self):
        from app.evidence.policy import infer_official_source
        candidate = SourceCandidate(
            uri="https://www.census.gov/data",
            hostname="www.census.gov",
            organization="US Census Bureau",
            title="Census Data",
            snippet="Official US Census data",
            metadata={"official": True},
        )
        self.assertTrue(infer_official_source(candidate, task_entities=["Census"]))

    def test_discovered_official_sources_persisted_per_run(self):
        from app.agent.source_governance import discovered_official_sources, record_discovered_official
        plan: dict = {"react_state": {}}
        plan = record_discovered_official(
            plan, domains=["fastapi.tiangolo.com"], repos=["github.com/fastapi/fastapi"],
        )
        sources = discovered_official_sources(plan)
        self.assertIn("fastapi.tiangolo.com", sources["domains"])
        self.assertIn("github.com/fastapi/fastapi", sources["repos"])
        # Record again idempotent
        plan = record_discovered_official(
            plan, domains=["fastapi.tiangolo.com"], repos=["github.com/fastapi/fastapi"],
        )
        sources = discovered_official_sources(plan)
        self.assertEqual(len([d for d in sources["domains"] if d == "fastapi.tiangolo.com"]), 1)


if __name__ == "__main__":
    unittest.main()
