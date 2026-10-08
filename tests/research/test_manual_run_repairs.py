"""Regression cases for lost obligations, incorrect vetoes and repair routing."""
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.research_goal import build_task_contract
from app.evidence.citation_validator import _has_explicit_contradiction, _is_substantive_window_quote
from app.llm.base import LLMResponse
from app.reporting.claim_occurrence import segment_final_answer_claims, is_evidence_limitation_statement
from app.research.comparison_scope import comparison_spec
from app.research.task_understanding import understand_new_task, _split_explicit_compound_questions
from app.research.answer_coverage import _coverage_constraints, _missing_comparison_cells
from .conftest import create_root


class Understanding:
    def __init__(self, proposal):
        self.proposal = proposal
    def structured_complete(self, *args, **kwargs):
        return LLMResponse(success=True, provider="fixture", content=json.dumps(self.proposal))
    def is_available(self):
        return True


def test_shared_question_keeps_both_product_obligations(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    task = "拆分对比Agent Alpha和Agent Beta核心原理。"
    proposal = {"questions": [{"question_id": "q", "text": task, "requirement_ids": ["a", "b"]}],
        "requirements": [{"requirement_id": rid, "question_id": "q", "predicate": "Explain core principles", "entity": f"Agent {name} core principles"}
                         for rid, name in [("a", "Alpha"), ("b", "Beta")]]}
    contract = understand_new_task(build_task_contract(task), Understanding(proposal))
    assert contract["obligation_version"] == "research-obligations-v2"
    assert contract["evidence_requirement"] == "substantive"
    assert contract["questions"][0]["requirement_ids"] == ["a", "b"]
    assert {r["requirement_id"] for r in contract["requirements"]} == {"a", "b", "req-original"}
    assert contract["research_terms"]["a"] != contract["research_terms"]["b"]
    assert contract["requirement_focus"]["a"]["entity"] == "Agent Alpha"
    assert contract["requirement_focus"]["b"]["entity"] == "Agent Beta"


def test_shared_question_keeps_four_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    task = "比较最近很火的几款personal Agent，拆分对比他们的底层原理、框架、记忆以及评测等。"
    question = "拆分对比他们的底层原理、框架、记忆以及评测等"
    proposal = {"questions": [{"question_id": "q", "text": question, "requirement_ids": ["p", "f", "m", "e"]}],
        "requirements": [{"requirement_id": rid, "question_id": "q", "predicate": f"Compare {topic}", "entity": f"personal Agent {topic}"}
                         for rid, topic in [("p", "underlying principles"), ("f", "frameworks"), ("m", "memory"), ("e", "evaluation")]]}
    contract = understand_new_task(build_task_contract(task), Understanding(proposal))
    assert contract["questions"][0]["requirement_ids"] == ["p", "f", "m", "e"]
    assert {v["facet"] for v in contract["requirement_focus"].values()} == {"mechanism", "framework", "memory", "evaluation"}
    assert contract["comparison_scope"]["selection_required"]


def test_compound_split_preserves_all_existing_requirements():
    result = _split_explicit_compound_questions({"questions": [{"question_id": "q", "text": "是否支持以及何时生效", "requirement_ids": ["a", "b"]}],
        "requirements": [{"requirement_id": rid, "question_id": "q"} for rid in ["a", "b"]]})
    assert len(result["requirements"]) == 4
    assert len({r["requirement_id"] for r in result["requirements"]}) == 4
    assert all(len(q["requirement_ids"]) == 2 for q in result["questions"])


@pytest.mark.parametrize("before,after", [("章节：", "下一句"), (" ", "下一句"), ("", "")])
def test_complete_chinese_clause_has_natural_boundaries(before, after):
    quote = "Agent Alpha 通过工具循环执行任务，并保留调用结果。"
    assert _is_substantive_window_quote(quote, before + quote + after)
    assert not _is_substantive_window_quote("支持跨会话知识传递", "不支持跨会话知识传递")


@pytest.mark.parametrize("abbreviation", ["et al.", "e.g.", "i.e.", "Dr.", "Fig."])
def test_abbreviations_do_not_detach_sentence_citation(abbreviation):
    text = f"来源说明 {abbreviation} 2025 表 2 的基线为 63.8%，实验为 71.2%[CIT-001-01]。"
    spans = [s for s in segment_final_answer_claims(text) if s.is_claim_candidate]
    assert len(spans) == 1
    assert spans[0].citation_labels == ("CIT-001-01",)
    assert "63.8" in spans[0].claim_text


def test_actual_sentence_end_still_keeps_uncited_claim_separate():
    spans = segment_final_answer_claims("Uncited result. Supported result [CIT-001-01].")
    assert len(spans) == 2
    assert not spans[0].citation_labels


def test_source_extra_baseline_number_is_not_a_contradiction():
    assert not _has_explicit_contradiction("AgentAlpha reports 71.2% on BenchSmall, using model-4.",
        "AgentAlpha reports 71.2% on BenchSmall, using model-4; the old 63.8% was a baseline, not AgentAlpha.")
    assert _has_explicit_contradiction("AgentAlpha supports 3 workers.", "AgentAlpha supports 2 workers.")
    assert _has_explicit_contradiction("AgentAlpha scores 71.3% on BenchSmall; AgentBeta scores 63.8%.",
        "AgentAlpha scores 71.2% on BenchSmall; AgentBeta scores 63.8%.")


def test_product_dimension_cell_cannot_use_generic_benchmark_or_single_blog():
    expected = [{"entity": "AgentAlpha", "facet": "memory"}]
    row = {"marker_starts": [10], "comparison_cells": [{**expected[0], "complete": True, "marker_starts": [10]}]}
    generic = [{"text": "Memory benchmarks measure recall.", "marker_starts": [10]}]
    assert _missing_comparison_cells(row, expected, generic)
    own = [{"text": "AgentAlpha stores memory in a persistent file.", "marker_starts": [10]}]
    single_blog = {"10": [{"independence_group": "blog", "primary": False}]}
    assert _missing_comparison_cells(row, expected, own, single_blog)
    primary = {"10": [{"independence_group": "manual", "primary": True}]}
    assert not _missing_comparison_cells(row, expected, own, primary)
    assert _missing_comparison_cells({**row, "comparison_cells": []}, expected, own, primary)


def test_same_products_required_across_all_dimensions():
    spec = comparison_spec("对比AgentAlpha和AgentBeta的原理、框架、记忆和评测。")
    constraints = _coverage_constraints({"comparison_scope": spec}, [{"requirement_id": "r", "predicate": "原理、框架、记忆和评测"}])
    assert len(constraints["cells"]["r"]) == 8


def test_limitation_is_not_a_world_fact_or_answer():
    assert is_evidence_limitation_statement("本次证据未提供 AgentAlpha 的框架细节。")
    assert is_evidence_limitation_statement("证据未提供 AgentAlpha 的记忆与评测数据。")
    assert not is_evidence_limitation_statement("证据未提供资料，但 AgentAlpha 实际支持所有操作。")


def test_repair_feedback_contains_actual_local_veto():
    from app.agent.reporter import _synthesis_revision_feedback
    from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport
    detail = CitationValidationDetail("CIT-001-01", "unsupported", "claim", "window", 0,
        application_reason="non_substantive_quote", provider_verdict="supported", evidence_quote="short", evidence_window="complete window")
    report = CitationValidationReport(occurrence_total=1, unsupported_occurrences=1, details=[detail])
    failure = _synthesis_revision_feedback(report)["must_remove_or_rewrite_unsupported"][0]
    assert failure["application_reason"] == "non_substantive_quote"
    assert failure["provider_verdict"] == "supported"
    assert failure["evidence_window"] == "complete window"


def test_local_quote_repair_does_not_spend_acquisition_round(db, r12_settings):
    from app.research.recovery import recover_answer_evidence
    root = create_root(db)
    plan = {"task_contract": {"obligation_version": "v1"}, "allowed_tools": ["web_fetcher", "tavily_search"]}
    feedback = {"must_remove_or_rewrite_unsupported": [{"application_reason": "non_substantive_quote"}]}
    with patch("app.research.recovery.execute_governed_operation") as execute:
        assert not recover_answer_evidence(db, root.run_id, plan, r12_settings, feedback)
    assert not execute.called
    assert plan["answer_recovery"]["attempts"] == []


def test_new_answer_gap_searches_before_unrelated_pending_pages(db, r12_settings):
    from app.research.recovery import recover_answer_evidence
    from app.tools.base import ToolResult
    root = create_root(db)
    plan = {"task_contract": {"obligation_version": "v1", "research_terms": {"r": "AgentAlpha architecture"}},
            "allowed_tools": ["tavily_search", "web_fetcher"], "answer_recovery": {"attempts": [
                {"round": 1, "repair_key": "optional", "actions": []}, {"round": 2, "repair_key": "optional", "actions": []}]}}
    called = []
    def execute(name, args, *_args):
        called.append((name, args))
        return ToolResult(success=True, output={"fetch_candidates": []})
    with patch("app.research.recovery.execute_governed_operation", side_effect=execute), \
         patch("app.research.recovery.materialize_execution_provenance"), \
         patch("app.research.recovery.build_source_context", return_value={"sources": [{"url": "https://irrelevant.test/", "fetch_status": "pending"}]}):
        assert not recover_answer_evidence(db, root.run_id, plan, r12_settings,
            {"answer_gaps": [{"requirement_id": "r", "predicate": "Compare frameworks", "detail": "Missing AgentAlpha framework"}]})
    assert called[0][0] == "tavily_search"
    assert "AgentAlpha" in called[0][1]["query"]
    assert len(plan["answer_recovery"]["attempts"]) == 3


def candidate_bundle(text):
    import hashlib
    return {"citations": [{"citation_label": "CIT-001-01", "passage_id": "p"}],
        "passages": [{"passage_id": "p", "snapshot_id": "s", "trace_id": "t", "content_basis": "full_text",
                      "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(), "locator": {}}],
        "source_snapshots": [{"snapshot_id": "s", "document_id": "d", "metadata": {"evidence_role": "primary_content", "official": True}}],
        "source_documents": [{"document_id": "d", "canonical_uri": "https://example.test/manual"}]}


@pytest.mark.parametrize("bad", [None, "old", "category", "quote", "missing_date", "old_relative", "unrelated_activity"])
def test_candidate_selection_freezes_only_grounded_recent_products(tmp_path, monkeypatch, bad):
    from app.research.comparison_scope import select_comparison_candidates
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    category = "AgentAlpha is a personal assistant that executes tasks."
    if bad == "category":
        category = "AgentAlpha is not a personal agent but a benchmark dataset."
    activity = "In 2026, AgentAlpha and AgentBeta have 2000 active users."
    if bad == "old":
        activity = activity.replace("2026", "2020")
    if bad == "old_relative":
        activity = "Updated recently in 2020, AgentAlpha and AgentBeta have 2000 active users."
    if bad == "unrelated_activity":
        activity = "In 2026, AgentGamma and AgentDelta have 2000 active users."
    beta = "AgentBeta is a personal assistant that executes tasks."
    payload = {"candidates": [{"name": name, "citation": "CIT-001-01", "evidence_quote": quote, "selection_quote": activity}
                for name, quote in [("AgentAlpha", category), ("AgentBeta", beta)]],
               "criteria": "Observed active users", "time_scope": "2026"}
    if bad == "quote":
        payload["candidates"][0]["evidence_quote"] = "AgentAlpha is an invented personal agent with hidden implementation."
    client = Understanding(payload)
    contract = {"original_task": "比较最近很火的几款personal Agent", "as_of": "2026-10-07",
                "comparison_scope": comparison_spec("比较最近很火的几款personal Agent")}
    if bad == "missing_date":
        contract.pop("as_of")
        activity = activity.replace("2026", "2020")
        for item in payload["candidates"]:
            item["selection_quote"] = activity
    bundle = candidate_bundle("\n".join([category, beta, activity]))
    result = select_comparison_candidates(contract, bundle, client)
    assert contract["comparison_scope"]["entities"] == []
    if bad is None:
        assert result["comparison_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
        assert result["comparison_scope"]["selection"]["decision_audit"]
    else:
        assert result["comparison_scope"]["entities"] == []
    with patch.object(client, "structured_complete") as complete:
        assert select_comparison_candidates(result, bundle, client) == result
        complete.assert_not_called()


def test_source_numbers_for_different_metrics_do_not_conflict():
    assert not _has_explicit_contradiction("AgentAlpha supports 3 workers.", "AgentAlpha uses 2 GB of memory.")
    assert not _has_explicit_contradiction("AgentAlpha supports 4 workers.",
        "AgentAlpha supports 2 workers; AgentAlpha supports 3 workers; AgentAlpha supports 2 workers.")


def test_comparison_cells_union_preserves_marker_checks_and_exact_entities():
    from app.research.answer_coverage import _coverage_rows
    expected = [{"entity": "AgentAlpha", "facet": "memory"}]
    raw = {"marker_starts": [], "comparison_cells": [{**expected[0], "complete": True, "marker_starts": [10]}]}
    row = _coverage_rows({"requirements": [raw]})[0]
    assert row["marker_starts"] == [10]
    assert _missing_comparison_cells(row, expected, [{"text": "AgentAlphaPlus stores persistent memory.", "marker_starts": [10]}])
    assert not _missing_comparison_cells(row, expected, [{"text": "AgentAlpha通过文件存储记忆。", "marker_starts": [10]}])


@pytest.mark.parametrize("mapped_cells", [False, True])
def test_coverage_replays_product_matrix_and_rejects_changed_scope(tmp_path, monkeypatch, mapped_cells):
    from app.research.answer_coverage import assess_answer_coverage, _verified_audit
    from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    answer = "AgentAlpha stores memory in files [CIT-001-01]. AgentBeta stores memory in files [CIT-001-01]."
    markers = [m.start() for m in re.finditer(r"\[CIT-", answer)]
    contract = {"original_task": "对比AgentAlpha和AgentBeta的记忆。", "comparison_scope": comparison_spec("对比AgentAlpha和AgentBeta的记忆。"),
                "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": "对比AgentAlpha和AgentBeta的记忆。"}]}
    row = {"requirement_id": "r", "complete": True, "marker_starts": markers,
           "facets": [{"kind": "memory", "complete": True, "marker_starts": markers}]}
    if mapped_cells:
        row["comparison_cells"] = [{"entity": name, "facet": "memory", "complete": True, "marker_starts": [marker]}
                                   for name, marker in zip(["AgentAlpha", "AgentBeta"], markers)]
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", "", "", 1,
        marker_start=m) for m in markers])
    result = assess_answer_coverage(answer, contract, validation, Understanding({"requirements": [row]}),
                                   provenance=candidate_bundle("AgentAlpha and AgentBeta store memory in files."))
    assert result["complete"] is mapped_cells
    if mapped_cells:
        assert _verified_audit(result, contract)
        changed = {**contract, "comparison_scope": {**contract["comparison_scope"], "entities": ["AgentAlpha", "AgentGamma"]}}
        assert not _verified_audit(result, changed)


@pytest.mark.parametrize("complete", [False, True])
def test_new_child_completion_requires_audited_assigned_answers(db, r12_settings, complete):
    from app.agent.react_executor import _complete_report
    from app.research.scope import create_research_scope
    from app.trace import store
    from .conftest import add_web_trace
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    child = store.create_agent_run(db, "AgentAlpha memory", "summary", "real", parent_run_id=root.run_id,
        root_run_id=root.run_id, run_role="research_branch", research_scope_id=scope.scope_id, engine_version="v2")
    plan = {"defer_to_research_scope": True, "task_contract": {"obligation_version": "research-obligations-v2"}}
    add_web_trace(db, child.run_id, "AgentAlpha memory source has body content.", "assigned")
    with patch("app.research.findings.assess_research_findings", return_value={"coverage": {"complete": True}}), \
         patch("app.research.answer_coverage._verified_audit", return_value=complete), \
         patch("app.agent.react_executor.generate_markdown_report") as writer:
        result = _complete_report(db, child.run_id, plan, {}, "node_complete", r12_settings, Understanding({}))
    assert result["status"] == ("completed" if complete else "incomplete")
    assert store.get_agent_run(db, child.run_id).report_path is None
    writer.assert_not_called()


def test_child_focus_does_not_inherit_root_aggregate_obligation():
    from app.research.node_executor import _node_task_contract
    parent = {"obligation_version": "research-obligations-v2", "requirements": [
        {"requirement_id": "r", "question_id": "q", "predicate": "Compare memory"},
        {"requirement_id": "req-original", "question_id": "original", "predicate": "Compare everything"}],
        "requirement_focus": {"r": {"facet": "memory"}}}
    node = SimpleNamespace(query="memory", research_goal="memory", metadata_json=json.dumps({"assigned_requirement_ids": ["r", "req-original"]}))
    child = _node_task_contract(parent, node)
    assert [r["requirement_id"] for r in child["requirements"]] == ["r"]
    assert child["requirement_focus"]["r"]["facet"] == "memory"


def test_scope_closes_bounded_answer_gaps_only_with_verified_final_answer(db, r12_settings):
    from app.research.scope import create_research_scope, create_research_node
    from app.evidence.scope_service import get_scope_provenance_bundle
    from app.research.outcome import assess_scope_outcome
    from .conftest import add_web_trace, materialize_run
    root = create_root(db)
    scope = create_research_scope(db, root.run_id, {})
    node = create_research_node(db, scope.scope_id, parent_node_id=None, run_id=root.run_id,
        node_type="research", topic="memory", query="memory", research_goal="memory", depth=0, priority=0, status="incomplete")
    add_web_trace(db, root.run_id, "AgentAlpha memory is persisted in files.", "scope")
    materialize_run(db, root, r12_settings)
    bundle = get_scope_provenance_bundle(db, scope)
    contract = {"obligation_version": "research-obligations-v2"}
    with patch("app.research.answer_coverage._verified_audit", return_value=False):
        assert "required_answer_coverage_incomplete" in assess_scope_outcome(db, scope, bundle, contract)["errors"]
        assert "required_answer_coverage_incomplete" in assess_scope_outcome(db, scope, {**bundle, "final_answer_coverage": {"complete": True}}, contract)["errors"]
    with patch("app.research.answer_coverage._verified_audit", return_value=True):
        assert not assess_scope_outcome(db, scope, {**bundle, "final_answer_coverage": {"complete": True}}, contract)["errors"]
        node.status = "failed"
        db.flush()
        assert "required_research_branch_failed" in assess_scope_outcome(db, scope, {**bundle, "final_answer_coverage": {"complete": True}}, contract)["errors"]


def test_repair_reads_relevant_retained_tail_before_search(db, r12_settings, tmp_path, monkeypatch):
    from app.research.recovery import recover_answer_evidence
    from app.retrieval.source_view import retain_source
    from app.trace.logger import record_trace_event
    from app.tools.base import ToolResult
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    root = create_root(db)
    body = "Introductory background. " * 1000 + "AgentAlpha persists memory via its journal and a replay protocol."
    trace = record_trace_event(db, root.run_id, 1, "web_fetcher", "success", {}, "retained", {"pages": [{
        "url": "https://example.test/manual", "content": body[:1000], "source_artifact": retain_source(body)}]})
    plan = {"task_contract": {"obligation_version": "v2", "research_terms": {"r": "AgentAlpha memory"}},
            "allowed_tools": ["tavily_search", "web_fetcher"]}
    called = []
    def execute(name, args, *_args, **_kwargs):
        called.append((name, args))
        return ToolResult(success=True, output={})
    with patch("app.research.recovery.execute_governed_operation", side_effect=execute), \
         patch("app.research.recovery.materialize_execution_provenance"):
        assert not recover_answer_evidence(db, root.run_id, plan, r12_settings,
            {"answer_gaps": [{"requirement_id": "r", "predicate": "Memory", "detail": "Missing AgentAlpha memory protocol"}]}, traces=[trace])
    assert called[0][0] == "web_fetcher"
    assert called[0][1]["offset"] > 1000
    assert called[0][1]["origin_trace_id"] == trace.trace_id
    assert "urls" not in called[0][1]
    assert not any(name == "tavily_search" for name, _ in called)


def test_retained_repair_uses_real_registry_policy_budget_and_lineage(db, r12_settings, tmp_path, monkeypatch):
    from app.research.recovery import recover_answer_evidence
    from app.retrieval.source_view import retain_source
    from app.trace.logger import record_trace_event
    from app.trace import store
    from app.agent.budget import planning_budget, budget_snapshot
    from app.tools.defaults import register_default_tools
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    register_default_tools()
    root = create_root(db)
    body = "The guide describes execution features. " * 1000 + "AgentAlpha memory is persisted through a journal."
    origin = record_trace_event(db, root.run_id, 1, "web_fetcher", "success", {}, "retained", {"pages": [{
        "url": "https://example.test/manual", "content": body[:1000], "content_basis": "partial",
        "source_artifact": retain_source(body)}]})
    plan = {"research_mode": "quick", "task_contract": {"obligation_version": "v2", "research_terms": {"r": "AgentAlpha memory"}},
            "allowed_tools": ["tavily_search", "web_fetcher"]}
    with planning_budget(db, root.run_id, r12_settings):
        assert recover_answer_evidence(db, root.run_id, plan, r12_settings,
            {"answer_gaps": [{"requirement_id": "r", "predicate": "Memory", "detail": "Missing AgentAlpha memory protocol"}]})
    traces = store.list_tool_traces(db, root.run_id)
    read = next(t for t in traces if t.trace_id != origin.trace_id and t.tool_name == "web_fetcher")
    assert read.status == "success"
    output = json.loads(read.output_json)
    assert output["source_content"]["origin_trace_id"] == origin.trace_id
    assert "AgentAlpha memory" in output["source_content"]["text"]
    assert budget_snapshot(db, root.run_id)["tool_calls"] == 1
