from app.eval.fake_react_llm import FakeReActLLMClient
from app.llm.base import LLMResponse, LLMUsage
from app.research.branch_planner import plan_research_branches


class PlannerResponseClient(FakeReActLLMClient):
    def __init__(self, response):
        super().__init__([])
        self.response = response
        self.max_tokens = "unset"

    def structured_complete(self, messages, temperature=0.0, max_tokens=2000):
        del messages, temperature
        self.max_tokens = max_tokens
        return self.response


def test_branch_planner_disables_data_analysis_and_sanitizes_priority():
    client = FakeReActLLMClient(
        [
            {
                "branches": [
                    {
                        "topic": "dataset",
                        "query": "analyze the verified dataset",
                        "research_goal": "derive the requested metric",
                        "node_type": "data_analysis",
                        "priority": "high",
                    }
                ],
                "is_comprehensive": False,
            }
        ]
    )

    result = plan_research_branches(
        client,
        task="compare a metric",
        observations=[],
        prior_queries=["compare a metric"],
        breadth=2,
        depth=1,
        contract={},
    )

    assert result["planner_failed"] is False
    assert result["branches"][0]["node_type"] == "web_research"
    assert result["branches"][0]["priority"] == 1


def test_branch_planner_accepts_only_r12_evidence_node_types():
    allowed = [
        "discovery",
        "web_research",
        "technical_research",
        "academic_research",
        "github_research",
        "verification",
    ]
    client = FakeReActLLMClient(
        [{"branches": [
            {"query": f"query {index}", "node_type": node_type}
            for index, node_type in enumerate(allowed)
        ]}]
    )

    result = plan_research_branches(
        client,
        task="research",
        observations=[],
        prior_queries=[],
        breadth=len(allowed),
        depth=1,
        contract={},
    )

    assert [branch["node_type"] for branch in result["branches"]] == allowed


def test_branch_planner_omits_output_cap_and_reports_truncation():
    client = PlannerResponseClient(
        LLMResponse(
            success=False,
            provider="fixture",
            model="reasoning-model",
            error_message="LLM structured response reached the provider output limit.",
            metadata={
                "finish_reason": "length",
                "error_type": "structured_output_truncated",
                "content_length": 693,
            },
            usage=LLMUsage(prompt_tokens=1102, completion_tokens=1200, total_tokens=2302),
        )
    )

    result = plan_research_branches(
        client,
        task="compare systems",
        observations=[],
        prior_queries=[],
        breadth=3,
        depth=1,
        contract={},
    )

    assert client.max_tokens is None
    assert result["planner_failed"] is True
    assert result["error_type"] == "structured_output_truncated"
    assert result["finish_reason"] == "length"
    assert result["prompt_tokens"] == 1102
    assert result["completion_tokens"] == 1200
    assert result["content_length"] == 693


def test_branch_planner_rejects_empty_non_comprehensive_plan():
    client = PlannerResponseClient(
        LLMResponse(
            success=True,
            content='{"branches":[],"is_comprehensive":false}',
            provider="fixture",
            model="fixture",
        )
    )

    result = plan_research_branches(
        client,
        task="research",
        observations=[],
        prior_queries=[],
        breadth=2,
        depth=1,
        contract={},
    )

    assert result["planner_failed"] is True
    assert result["error_type"] == "research_completeness_not_established"
