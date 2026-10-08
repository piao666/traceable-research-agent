"""Acquisition effects must reach the exact obligation before rejudgment."""
from copy import deepcopy
import json
from types import SimpleNamespace

from app.reporting.writing_evidence import build_writing_evidence
from app.research.recovery import actionable_gaps, recover_answer_evidence
from app.research.controller import run_work_loop
from app.research import findings as findings_module  # Import before patching its dependency.
from app.trace import store
from app.tools.base import ToolResult
from .conftest import create_root
from .test_manual_run_repairs import candidate_bundle, Understanding


def combine_sources(rows):
    result = {key: [] for key in ("source_documents", "source_snapshots", "passages", "citations")}
    for i, (text, primary) in enumerate(rows):
        source = candidate_bundle(text)
        for key in result:
            for row in source[key]:
                for field in ("document_id", "snapshot_id", "passage_id"):
                    if field in row:
                        row[field] += str(i)
                if "citation_label" in row:
                    row["citation_label"] = f"CIT-{i+1:03}-01"
                if "canonical_uri" in row:
                    row["canonical_uri"] = f"https://publisher{i}.example/source"
                if primary:
                    row.setdefault("metadata", {})["official"] = True
                result[key].append(row)
    return result


def test_each_object_dimension_gets_its_body_before_multi_object_directory():
    body = [
        ("AgentAlpha, AgentBeta and AgentGamma are popular frameworks. Memory, framework, mechanism and evaluation are topics in this comparison directory.", False),
        ("AgentAlpha memory persists project files and conversation history. When a project is deleted, its memory is removed.", True),
        ("AgentBeta memory persists each task in an append-only log. When replay fails, recovery stops rather than dropping the failed task.", True),
        ("AgentAlpha execution works by planning tool actions and using observed feedback to revise the next action.", True),
        ("AgentBeta execution works by dispatching a typed action to a worker and processing its returned feedback.", True),
    ]
    bundle = combine_sources(body)
    original = deepcopy(bundle)
    contract = {"obligation_version": "v2", "original_task": "Compare their mechanism and memory",
        "comparison_scope": {"entities": ["AgentAlpha", "AgentBeta"], "dimensions": {"mechanism": "mechanism", "memory": "memory"}},
        "requirements": [{"requirement_id": k, "predicate": k} for k in ("mechanism", "memory")],
        "requirement_focus": {k: {"facet": k} for k in ("mechanism", "memory")},
        "evidence_focus": [{"entity": e, "facet": k} for e in ("AgentAlpha", "AgentBeta") for k in ("mechanism", "memory")]}
    writing = build_writing_evidence(bundle, contract, 2200)
    texts = [u.text for u in writing.factual_units[:4]]
    assert all(any(text in projected for projected in texts) for text, _ in body[1:])
    assert not any("comparison directory" in text for text in texts)
    assert bundle == original
    assert all(u.text == original["passages"][int(u.passage_id[-1])]["text"][u.locator["writing_window_start"]:u.locator["writing_window_end"]]
               for u in writing.factual_units)
    assert all(d.get("grants_answer_coverage") is False for d in writing.projection_diagnostics if d.get("target"))


def test_unchanged_writer_view_skips_billable_judgment_and_keeps_gap(db, r12_settings, monkeypatch):
    root = create_root(db)
    gap = {"requirement_id": "r", "entity": "AgentAlpha", "facet": "memory", "cause": "answer_content_missing"}
    plan = {"task_contract": {"obligation_version": "v2", "original_task": "AgentAlpha memory",
        "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": "AgentAlpha memory"}],
        "evidence_focus": [{k: gap[k] for k in ("requirement_id", "entity", "facet")}]}}
    bundle = candidate_bundle("AgentAlpha provides an overview of its framework.")
    store.replace_agent_run_plan(db, root.run_id, plan)
    judgments, actions = [], []
    def projection(*_):
        return SimpleNamespace(factual_units=[SimpleNamespace(passage_id="p", text_sha256="new" if len(actions) >= 2 else "old", citation_id="CIT-001-01")])
    def findings(*_, previous=None):
        judgments.append(previous)
        complete = len(actions) >= 2
        return {"markdown": "new" if complete else "old", "validation": {}, "coverage": {"complete": complete,
            "answer_sha256": "new" if complete else "old", "gaps": [] if complete else [gap], "requirements": [
                {"requirement_id": "r", "comparison_cells": [{"entity": "AgentAlpha", "facet": "memory", "complete": complete, "marker_starts": [0]}]}]}}
    def recover(*_, **_kw):
        actions.append("acquired")
        return True
    monkeypatch.setattr("app.reporting.writing_evidence.build_writing_evidence", projection)
    monkeypatch.setattr("app.research.findings.assess_research_findings", findings)
    monkeypatch.setattr("app.research.controller.recover_answer_evidence", recover)
    run_work_loop(db, root.run_id, plan, r12_settings, bundle, Understanding({}), loader=lambda: bundle, traces=[])
    assert len(actions) == 2 and len(judgments) == 2
    assert judgments[1]["coverage"]["gaps"] == [gap]
    assert plan["work_controller"]["stop_reason"] == "candidate_answers_ready"
    work = next(w for w in plan["research_work"]["items"] if w["entity"] == "AgentAlpha")
    assert work["answer_status"] == "candidate"  # Requires current final report proof.
    assert any(t.phase == "research_work_projection_unchanged" for t in store.list_tool_traces(db, root.run_id))


def test_aggregate_and_local_identity_gaps_do_not_repeat_cell_acquisition():
    individual = {"requirement_id": "r", "entity": "AgentAlpha", "facet": "application", "cause": "answer_content_missing"}
    aggregate = {**individual, "requirement_id": "req-original"}
    identity = {"requirement_id": "list", "cause": "inventory_identity_missing"}
    assert actionable_gaps([aggregate, identity, individual]) == [individual]
    assert actionable_gaps([identity]) == []
    assert actionable_gaps([aggregate]) == [aggregate]


def test_application_reason_change_shares_allowance_and_body_trace_reaches_projection(db, r12_settings, monkeypatch):
    root = create_root(db)
    gap = {"requirement_id": "r", "entity": "AgentAlpha", "facet": "application", "cause": "answer_content_missing"}
    plan = {"allowed_tools": ["tavily_search", "web_fetcher"], "task_contract": {"obligation_version": "v2",
        "evidence_focus": [{k: gap[k] for k in ("requirement_id", "entity", "facet")}],
        "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": "typical applications"}]}}
    calls = []
    def execute(name, arguments, *_):
        calls.append(name)
        return ToolResult(success=True, output={"fetch_candidates": ["https://example.test/task"]} if name == "tavily_search" else
            {"pages": [{"url": "https://example.test/task", "content": "AgentAlpha solves math problems by coordinating a solver and a verifier."}]})
    monkeypatch.setattr("app.research.recovery.execute_governed_operation", execute)
    monkeypatch.setattr("app.research.recovery.materialize_execution_provenance", lambda *_: None)
    for cause in ("answer_content_missing", "application_workload_missing", "answer_content_missing"):
        recover_answer_evidence(db, root.run_id, plan, r12_settings, {"answer_gaps": [{**gap, "cause": cause}]})
    assert len(plan["answer_recovery"]["attempts"]) == 2
    assert plan["answer_recovery"]["stop_reason"] == "same_gap_repair_round_limit"
    body_trace = next(t for t in store.list_tool_traces(db, root.run_id) if t.tool_name == "web_fetcher")
    assert body_trace.trace_id in plan["task_contract"]["evidence_focus"][0]["trace_ids"]
    assert all(w["answer_status"] != "confirmed" for w in plan["research_work"]["items"])


def test_prior_findings_carry_diagnosis_without_old_citation_ids(tmp_path, monkeypatch):
    from app.research.findings import assess_research_findings
    from app.llm.base import LLMResponse
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    prompts = []
    class Client(Understanding):
        def complete(self, messages, **kwargs):
            prompts.append(json.loads(messages[-1].content))
            return LLMResponse(success=False, provider="fixture", error_message="Unavailable", metadata={"http_status": 402})
    previous = {"markdown": "AgentAlpha solves math tasks [CIT-099-01].", "coverage": {"gaps": [
        {"requirement_id": "r", "entity": "AgentBeta", "facet": "application", "cause": "answer_content_missing", "marker_starts": [99]}]}}
    result = assess_research_findings(candidate_bundle("AgentAlpha solves math tasks."), {"obligation_version": "v2"}, Client({}), previous=previous)
    feedback = prompts[0]["revision_feedback"]
    assert "CIT-099" not in json.dumps(feedback) and "marker_starts" not in json.dumps(feedback)
    assert feedback["previous_findings_are_evidence"] is False and result["provider_failure"]["http_status"] == 402


def test_valid_partial_cell_retains_exact_window_after_acquisition_and_relabels():
    from app.research.findings import retain_candidate_windows
    from app.research.state import semantic_contract
    body = "AgentAlpha solves math problems using a solver and a verifier. It provides the final result after the verifier checks each step."
    bundle = combine_sources([(body, True)])
    contract = {"obligation_version": "v2", "original_task": "Give their typical applications",
        "requirements": [{"requirement_id": "r", "predicate": "these frameworks' typical applications"}],
        "answer_scope": {"entities": ["AgentAlpha", "AgentBeta"]}}
    writing = build_writing_evidence(bundle, contract, 1600)
    before = semantic_contract(contract)
    unit = writing.factual_units[0]
    coverage = {"requirements": [{"requirement_id": "r", "comparison_cells": [
        {"entity": "AgentAlpha", "facet": "application", "complete": True, "marker_starts": [12]},
        {"entity": "AgentBeta", "facet": "application", "complete": False, "marker_starts": []}]}], "gaps": [
            {"requirement_id": "r", "entity": "AgentBeta", "facet": "application", "cause": "answer_content_missing"}]}
    validation = SimpleNamespace(details=[SimpleNamespace(marker_start=12, verdict="supported", citation_label=unit.citation_id)])
    retain_candidate_windows(contract, coverage, validation, writing)
    assert semantic_contract(contract) == before and len(contract["retained_cell_windows"]) == 1
    retain_candidate_windows(contract, coverage, validation, writing)
    assert len(contract["retained_cell_windows"]) == 1
    changed = deepcopy(bundle)
    changed["citations"][0]["citation_label"] = "CIT-008-01"
    refreshed = build_writing_evidence(changed, contract, 1600)
    assert any(u.citation_id == "CIT-008-01" and u.text == unit.text for u in refreshed.factual_units)
    assert any(d.get("retained_candidate") is True for d in refreshed.projection_diagnostics)
    contract["retained_cell_windows"][0]["text_sha256"] = "tampered"
    rejected = build_writing_evidence(changed, contract, 1600)
    assert not any(d.get("retained_candidate") is True for d in rejected.projection_diagnostics)


def test_rejected_cell_cannot_pin_supported_but_semantically_wrong_window():
    from app.research.findings import retain_candidate_windows
    writing = build_writing_evidence(candidate_bundle("AgentAlpha is easy to deploy as a multi-agent framework."))
    validation = SimpleNamespace(details=[SimpleNamespace(marker_start=0, verdict="supported", citation_label="CIT-001-01")])
    contract = {}
    coverage = {"requirements": [{"requirement_id": "r", "comparison_cells": [
        {"entity": "AgentAlpha", "facet": "application", "complete": True, "marker_starts": [0]}]}],
        "gaps": [{"requirement_id": "r", "entity": "AgentAlpha", "facet": "application", "cause": "application_workload_missing"}]}
    retain_candidate_windows(contract, coverage, validation, writing)
    assert not contract["retained_cell_windows"]
