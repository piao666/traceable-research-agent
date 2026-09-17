import json

import pytest

from app.eval.deep_research_benchmark import (
    DEFAULT_CASES_PATH,
    RELEASE_GOLDEN_IDS,
    REQUIRED_CATEGORIES,
    load_deep_research_cases,
    validate_deep_research_cases,
)


def test_deep_research_manifest_has_cross_domain_cases_and_five_release_goldens():
    cases = load_deep_research_cases()
    assert len(cases) >= 20
    assert {case["category"] for case in cases} == REQUIRED_CATEGORIES
    assert {case["case_id"] for case in cases if case["release_golden"]} == RELEASE_GOLDEN_IDS
    assert all(case["expected_controller"] == "pear-v2" for case in cases)
    assert len({case["dataset_version"] for case in cases}) == 1


def test_deep_research_manifest_rejects_fixed_version_drift(tmp_path):
    cases = load_deep_research_cases()
    cases[1]["policy_version"] = "source-policy-drift"
    path = tmp_path / "drift.jsonl"
    path.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed-version drift"):
        from app.eval.deep_research_benchmark import _load_jsonl

        validate_deep_research_cases(_load_jsonl(path))


def test_deep_research_manifest_default_path_is_repo_local():
    assert DEFAULT_CASES_PATH.name == "golden_cases.jsonl"
    assert DEFAULT_CASES_PATH.parent.name == "deep_research"
