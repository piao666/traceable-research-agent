from app.eval.fake_react_llm import FakeReActLLMClient
from app.research.branch_planner import plan_research_branches


def test_branch_planner_accepts_data_analysis_and_sanitizes_priority():
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
    assert result["branches"][0]["node_type"] == "data_analysis"
    assert result["branches"][0]["priority"] == 1
