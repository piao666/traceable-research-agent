"""Regressions from the source-chain audit; no network/provider calls."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.budget import estimate_text_tokens, limits
from app.agent.research_goal import build_task_contract
from app.config import Settings
from app.reporting.writing_evidence import build_writing_evidence
from app.research.node_executor import _node_task_contract
from scripts.validate_real_runtime import AcceptanceError, _load_dispatched_child_observations


@pytest.mark.parametrize("prefix", ["使用官方文档", "只使用官方文档", "仅使用官方文档", "以官方资料为准"])
def test_chinese_explicit_official_source_contract(prefix):
    assert build_task_contract(prefix + "解释 Alpha API")["source_constraints"]["official_only"]


def test_child_contract_contains_only_its_work_without_mutating_parent():
    parent = build_task_contract("比较 Alpha 和 Beta 的核心差异，从部署和成本方面分析")
    before = json.dumps(parent)
    node = SimpleNamespace(query="Alpha 部署", research_goal="Alpha 部署", metadata_json="{}")
    child = _node_task_contract(parent, node)
    assert [(item["entity"], item["dimension"]) for item in child["requirements"]] == [("Alpha", "部署")]
    assert child["entities"] == ["Alpha"]
    assert child["dimensions"] == ["部署"]
    assert json.dumps(parent) == before


def test_child_explicit_assignment_does_not_need_lexical_guessing():
    parent = build_task_contract("比较 Alpha 和 Beta 的核心差异，从部署和成本方面分析")
    node = SimpleNamespace(query="Deployment guide", research_goal="Read deployment details", metadata_json=json.dumps({"assigned_requirement_ids": ["cmp-2-1"]}))
    assert _node_task_contract(parent, node)["requirements"][0]["entity"] == "Beta"
    node.metadata_json = json.dumps({"assigned_requirement_ids": ["invented"]})
    with pytest.raises(ValueError, match="Unknown assigned"):
        _node_task_contract(parent, node)


@pytest.mark.parametrize("assigned", [None, "r1", {}, [1], ["unknown"]])
def test_child_rejects_malformed_explicit_assignment(assigned):
    node = SimpleNamespace(query="Alpha", research_goal="Alpha",
                           metadata_json=json.dumps({"assigned_requirement_ids": assigned}))
    with pytest.raises(ValueError):
        _node_task_contract({"requirements": [{"requirement_id": "r1", "entity": "Alpha"}]}, node)


def test_requirement_index_preserves_legacy_scope_and_rejects_conflicts():
    from app.research.contracts import requirement_index
    item = {"requirement_id": "r1", "entity": "Alpha", "source_scope": "external_web"}
    contract = {"requirements": [item], "evidence_scope_requirements": [dict(item)]}
    before = json.dumps(contract)
    assert requirement_index(contract) == {"r1": item}
    assert json.dumps(contract) == before
    contract["evidence_scope_requirements"][0]["entity"] = "Beta"
    with pytest.raises(ValueError, match="Ambiguous"):
        requirement_index(contract)


def test_branch_planner_rejects_conflicting_root_requirement_ids():
    from app.research.branch_planner import plan_research_branches
    from tests.support.fake_react_llm import FakeReActLLMClient
    contract = {"requirements": [
        {"requirement_id": "r1", "entity": "Alpha"},
        {"requirement_id": "r1", "entity": "Beta"},
    ]}
    result = plan_research_branches(FakeReActLLMClient([
        {"branches": [{"query": "Alpha", "assigned_requirement_ids": ["r1"]}]}
    ]), task="Compare", observations=[], prior_queries=[], breadth=2, depth=1, contract=contract)
    assert result["planner_failed"]
    assert result["error_type"] == "branch_requirement_invalid"


def bundle_for(basis, text="Alpha supports structured output with verified field values."):
    return {
        "citations": [{"citation_label": "CIT-001-01", "passage_id": "p"}],
        "passages": [{"passage_id": "p", "snapshot_id": "s", "content_basis": basis, "text": text,
                      "content_hash": hashlib.sha256(text.encode()).hexdigest(), "locator": {"row": 1, "column": "value"}}],
        "source_snapshots": [{"snapshot_id": "s", "document_id": "d", "metadata": {"evidence_role": "primary_content"}}],
        "source_documents": [{"document_id": "d", "canonical_uri": "https://example.test/source"}],
    }


@pytest.mark.parametrize("basis", ["full_text", "partial", "table", "structured"])
def test_writer_accepts_all_shared_content_bases_with_exact_identity(basis):
    bundle = bundle_for(basis)
    result = build_writing_evidence(bundle, budget=500)
    assert len(result.factual_units) == 1
    unit = result.factual_units[0]
    assert unit.locator["row"] == 1
    assert unit.text_sha256 == bundle["passages"][0]["content_hash"]
    bundle["passages"][0]["content_hash"] = "tampered"
    assert not build_writing_evidence(bundle).factual_units


def test_discovery_stays_ineligible_and_chinese_budget_is_not_chars_div_four():
    assert not build_writing_evidence(bundle_for("snippet_only")).factual_units
    text = "真实正文内容" * 100
    assert estimate_text_tokens(text) > 200
    assert not build_writing_evidence(bundle_for("full_text", text), budget=200).factual_units
    assert not build_writing_evidence(bundle_for("full_text"), budget=0).factual_units


def test_report_reserve_covers_batched_validation_without_raising_total():
    configured = limits(Settings(research_max_llm_calls=64, research_max_tokens=200000))
    assert configured["max_llm_calls"] == 64
    assert configured["max_tokens"] == 200000
    assert configured["final_report_llm_calls"] == 16
    assert configured["final_report_tokens"] == 60000


@pytest.mark.parametrize("basis", ["full_text", "partial", "table", "structured"])
def test_deep_contract_and_comparison_use_shared_body_vocabulary(basis):
    from app.evidence.qualification import DEFAULT_CONTENT_BASES
    from app.research.contracts import EvidenceRequirement, normalize_requirements
    from app.research.coverage import comparison_requirements

    requirement = EvidenceRequirement(requirement_id="r1")
    assert requirement.acceptable_content_basis == DEFAULT_CONTENT_BASES
    explicit = normalize_requirements({"requirements": [
        {"requirement_id": "r1", "predicate": "verified fact", "acceptable_content_basis": [basis]}
    ]})[0]
    assert explicit.predicate == "verified fact"
    assert explicit.acceptable_content_basis == (basis,)
    assert comparison_requirements(["Alpha"], ["deployment"])[0]["acceptable_content_basis"] == list(DEFAULT_CONTENT_BASES)


@pytest.mark.parametrize("basis,restriction,role,expected", [
    ("partial", None, "primary_content", True),
    ("partial", ["full_text"], "primary_content", False),
    ("snippet_only", None, "primary_content", False),
    ("metadata", None, "primary_content", False),
    ("partial", None, "discovery_index", False),
])
def test_deep_shared_basis_preserves_explicit_restrictions_and_roles(basis, restriction, role, expected):
    from app.research.assessor import assess_requirements

    requirement = {"requirement_id": "r1", "predicate": "reduced latency"}
    if restriction is not None:
        requirement["acceptable_content_basis"] = restriction
    contract = {"requirements": [requirement], "requirement_claim_links": [
        {"requirement_id": "r1", "source_id": "s1", "claim_occurrence_id": "c1"}
    ]}
    context = {"sources": [{"source_id": "s1", "fetch_status": "fetched",
                            "content_basis": basis, "evidence_role": role,
                            "independence_group": "publisher", "url": "https://example.test/source"}]}
    assert assess_requirements(contract, context)["complete"] is expected
    contract["requirement_claim_links"] = []
    assert not assess_requirements(contract, context)["complete"]


def test_partial_report_requires_eligible_evidence_and_only_quality_gaps():
    from app.research.outcome import can_write_partial_report
    outcome = {"errors": ["required_evidence_missing"],
               "evidence_assessment": {"eligible_passage_ids": ["p"]}}
    assert can_write_partial_report(outcome)
    for code in ("provider_failure", "evidence_trace_incomplete", "research_orchestration_incomplete"):
        assert not can_write_partial_report({**outcome, "errors": [code]})
    assert not can_write_partial_report({**outcome, "evidence_assessment": {}})


@pytest.mark.parametrize("raw", [
    [None], ["unparsed obligation"], "not a list", {},
    [{"requirement_id": "", "question_id": "", "unexpected": True}],
    [{"requirement_id": "r" * 161, "question_id": "q" * 161}],
    [{"requirement_id": "r1", "acceptable_content_basis": ["invented"]}],
])
@pytest.mark.parametrize("goal_kind", ["fact", "comparison"])
def test_malformed_obligation_remains_blocked_even_with_source_mapping(raw, goal_kind):
    from app.research.assessor import assess_requirements
    from app.research.contracts import normalize_requirements

    contract = {"requirements": raw, "goal_kind": goal_kind}
    normalized = normalize_requirements(contract)
    assert len(normalized) == 1
    assert normalized[0].predicate == "invalid_requirement"
    contract["requirement_claim_links"] = [{
        "requirement_id": normalized[0].requirement_id,
        "source_id": "s1", "claim_occurrence_id": "c1",
    }]
    context = {"sources": [{"source_id": "s1", "fetch_status": "fetched",
                            "content_basis": "full_text", "evidence_role": "primary_content",
                            "independence_group": "publisher", "url": "https://example.test/source"}]}
    result = assess_requirements(contract, context)
    assert result["applicable"]
    assert not result["complete"]
    assert result["requirements"][0]["status"] == "blocked"
    assert result["gaps"][0]["type"] == "invalid_requirement"


@pytest.mark.parametrize("contract", [
    {"requirements": [{"requirement_id": "alpha-deploy", "entity": "Alpha", "dimension": "部署"}]},
    {"original_task": "解释 Alpha 的部署机制", "evidence_requirement": "substantive"},
])
def test_generic_scope_coverage_does_not_require_topic_specific_contract(contract):
    from app.research.orchestrator import _explicit_scope_covered
    node = SimpleNamespace(run_id="branch", status="completed", metadata_json='{"required":true}')
    bundle = {"passages": [{"passage_id": "p", "origin_run_id": "branch"}]}
    assessment = SimpleNamespace(passed=True, eligible_passage_ids=["p"])
    with (
        patch("app.research.orchestrator.list_scope_nodes", return_value=[node]),
        patch("app.research.orchestrator.get_scope_provenance_bundle", return_value=bundle),
        patch("app.agent.evidence_requirements.assess_required_evidence", return_value=assessment),
    ):
        assert _explicit_scope_covered(None, SimpleNamespace(scope_id="scope"), {"task_contract": contract})
        assessment.passed = False
        assert not _explicit_scope_covered(None, SimpleNamespace(scope_id="scope"), {"task_contract": contract})


def test_branch_planner_preserves_valid_assignment_and_rejects_invented_ids():
    from app.research.branch_planner import plan_research_branches
    from tests.support.fake_react_llm import FakeReActLLMClient
    contract = {"requirements": [{"requirement_id": "alpha", "entity": "Alpha"}]}
    def plan(ids):
        client = FakeReActLLMClient([{"branches": [{"query": "Alpha docs", "assigned_requirement_ids": ids}], "is_comprehensive": False}])
        return plan_research_branches(client, task="Alpha", observations=[], prior_queries=[], breadth=2, depth=1, contract=contract)
    valid = plan(["alpha"])
    assert valid["branches"][0]["assigned_requirement_ids"] == ["alpha"]
    assert valid["branches"][0]["required"]
    assert not plan([])["branches"][0]["required"]
    assert plan(["invented"])["planner_failed"]


class LineageClient:
    def __init__(self, wrong_root=False):
        self.wrong_root = wrong_root

    def json(self, method, path):
        if path.endswith(("/trace", "/evidence")):
            return []
        child = path.rsplit("/", 1)[-1]
        return {"run_id": child, "parent_run_id": "root" if child == "child" else "child",
                "root_run_id": "wrong" if self.wrong_root else "root", "research_scope_id": "scope"}


def test_acceptance_follows_actual_multilevel_parent_links(tmp_path):
    traces = [{"run_id": "root", "tool_name": "research_node_dispatch", "status": "success",
               "output": {"child_run_id": child, "parent_run_id": parent}}
              for child, parent in [("child", "root"), ("grandchild", "child")]]
    assert len(_load_dispatched_child_observations(LineageClient(), "root", traces, tmp_path)) == 2
    with pytest.raises(AcceptanceError, match="different root"):
        _load_dispatched_child_observations(LineageClient(True), "root", traces, tmp_path)
    with pytest.raises(AcceptanceError, match="ancestor"):
        _load_dispatched_child_observations(LineageClient(), "root", traces[1:], tmp_path)
