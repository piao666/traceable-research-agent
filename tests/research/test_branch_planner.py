from app.eval.fake_react_llm import FakeReActLLMClient
from app.research.branch_planner import plan_research_branches


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
