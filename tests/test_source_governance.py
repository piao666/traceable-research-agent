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

    def test_unverified_repo_uses_generic_github_tier(self) -> None:
        result = classify_tier(
            "mcp_github_search",
            "https://github.com/example-user/example-repo",
            {},
            self.policy,
        )
        self.assertEqual(result.tier, "T1")
        self.assertEqual(result.classification_rule, "domain_table:github.com")

    def test_known_org_does_not_make_unverified_repo_official_code(self) -> None:
        from app.evidence.policy import classify_source

        source_class = classify_source(
            "mcp_github_search",
            "https://github.com/example-org/unverified-repo",
            {"organization": "example-org"},
            self.policy,
        )
        self.assertEqual(source_class, "blog")


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

    def test_targeted_refetch_is_bounded_and_uses_preferred_domains(self) -> None:
        from app.agent.source_governance import (
            execute_targeted_refetches,
            govern_tool_result,
        )

        settings = Settings(max_refetch_rounds=2, max_discovery_candidates=5)
        plan = _plan()
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
            calls.append(arguments)
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
        self.assertEqual(len(refetches), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["include_domains"], list(plan["profile_constraints"]["prefer_domains"]))
        self.assertEqual([item.round_no for item in refetches], [1, 2])

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


if __name__ == "__main__":
    unittest.main()
