"""Exercise obligation -> entity/facet -> gap -> operation -> verification."""
from copy import deepcopy
import json

from app.research.assessor import persist_plan_contract
from app.research.referential_scope import enumerated_items
from app.llm.base import LLMResponse
import re
import hashlib
import pytest


def contract():
    return {"version": "task-contract-v1", "obligation_version": "research-obligations-v2",
        "original_task": "列出常用框架，给出这些的典型应用场景",
        "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": "这些的典型应用场景"}],
        "questions": [{"question_id": "q", "text": "典型应用场景", "requirement_ids": ["r"]}]}


def test_execution_attempt_does_not_revise_obligations(db):
    from .conftest import create_root
    root = create_root(db)
    original = {**contract(), "comparison_scope": {"entities": [], "selection_required": True}}
    first = persist_plan_contract(db, root_run_id=root.run_id, contract=original)
    changed = deepcopy(original)
    changed["comparison_scope"].update(selection_attempt={"accepted_count": 0, "decision_audit": "a"}, selection_search_rounds=2)
    assert persist_plan_contract(db, root_run_id=root.run_id, contract=changed).revision_id == first.revision_id


def test_reverse_inventory_is_equivalent_to_forward_inventory():
    c = contract()
    assert enumerated_items([{"text": "证据将 AgentAlpha、AgentBeta 列为常用框架。"}], c) == ["AgentAlpha", "AgentBeta"]


def test_body_admission_does_not_duplicate_semantic_obligation_coverage():
    from app.agent.evidence_requirements import assess_required_evidence
    from .test_manual_run_repairs import candidate_bundle
    c = {**contract(), "evidence_requirement": "substantive"}
    # No literal predicate keyword occurs in this valid body. Admission is
    # not proof of an answer; the separate controller must fill that cell.
    bundle = candidate_bundle("AgentAlpha routes specialist workers according to incoming requests.")
    result = assess_required_evidence(c, bundle)
    assert result.passed and result.eligible_passage_ids == ("p",)
    c["evidence_scope_requirements"] = [{"requirement_id": "local", "source_scope": "local_project"}]
    result = assess_required_evidence(c, bundle)
    assert not result.passed and any(g.requirement_id == "local" for g in result.gaps)


@pytest.mark.parametrize("invalid", ["snippet", "provenance", "mock", "quality", "official", "current"])
def test_body_admission_keeps_provenance_and_source_constraints(invalid):
    from app.agent.evidence_requirements import assess_required_evidence
    from .test_manual_run_repairs import candidate_bundle
    c = {**contract(), "evidence_requirement": "substantive"}
    bundle = candidate_bundle("AgentAlpha is a task routing system.")
    meta = bundle["source_snapshots"][0]["metadata"]
    if invalid == "snippet":
        bundle["passages"][0]["content_basis"] = "search_snippet"
    elif invalid == "provenance":
        bundle["passages"][0]["snapshot_id"] = "unknown"
    elif invalid == "mock":
        meta["is_mock"] = True
    elif invalid == "quality":
        meta["quality"] = {"usable": False}
    elif invalid == "official":
        c["source_constraints"] = {"official_only": True}
    elif invalid == "current":
        c["source_constraints"] = {"current_official_documentation": True}
    assert not assess_required_evidence(c, bundle).passed


def test_recovered_research_is_not_vetoed_by_earlier_step_or_finish_state(monkeypatch, r12_settings):
    from types import SimpleNamespace
    from app.agent.outcome import assess_research_outcome
    # A later recovery action has usable evidence at a different step. The
    # original mandatory fetch failed and the earlier agent said not_met.
    item = SimpleNamespace(is_mock=False, is_fallback=False, step_no=7, tool_name="web_fetcher")
    monkeypatch.setattr("app.agent.outcome.build_evidence_bundle",
        lambda *_: SimpleNamespace(evidence_items=[item], warnings=[]))
    plan = {"task_contract": contract(), "steps": [{"step_no": 2, "tool_name": "web_fetcher", "required": True}],
        "react_state": {"goal_status": "not_met", "finish_reason": "incomplete"}}
    result = assess_research_outcome(SimpleNamespace(source_mode="real"), plan, [], [], r12_settings)
    assert result["status"] == "passed"  # Operational readiness, never final answer confirmation.
    monkeypatch.setattr("app.agent.outcome.build_evidence_bundle",
        lambda *_: SimpleNamespace(evidence_items=[], warnings=[]))
    assert assess_research_outcome(SimpleNamespace(source_mode="real"), plan, [], [], r12_settings)["status"] == "failed"


@pytest.mark.parametrize("execution", ["planned", "react"])
@pytest.mark.parametrize("has_body", [True, False])
def test_root_enters_work_loop_before_terminal_evidence_gate(db, r12_settings, monkeypatch, execution, has_body):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.agent import executor, react_executor
    from app.trace import store
    root = create_root(db)
    plan = {"execution_mode": execution, "research_mode": "quick", "steps": [], "allowed_tools": [],
            "task_contract": {**contract(), "evidence_requirement": "substantive"}}
    store.replace_agent_run_plan(db, root.run_id, plan)
    bundle = candidate_bundle("AgentAlpha routes specialist workers.") if has_body else {}
    events = []
    def loop(*args, **kwargs):
        events.append("work")
        return bundle
    def stop_at_gate(*args):
        events.append("gate")
        store.update_agent_run_status(db, root.run_id, "incomplete", "fixture stops before writing")
        return False
    monkeypatch.setattr("app.research.controller.run_work_loop", loop)
    module = executor if execution == "planned" else react_executor
    monkeypatch.setattr(module, "materialize_execution_provenance", lambda *_: bundle)
    monkeypatch.setattr(module, "enforce_research_outcome", stop_at_gate)
    if execution == "planned":
        monkeypatch.setattr(executor, "enforce_execution_readiness", lambda *_args, **_kwargs: True)
        result = executor.run_plan(db, root.run_id, settings_obj=r12_settings, report_llm_client=WorkJudge())
    else:
        result = react_executor._complete_report(db, root.run_id, plan, {}, "finish", r12_settings, WorkJudge())
    assert events == ["work", "gate"], result


def test_work_survives_mapping_failure_and_reopen(db):
    from .conftest import create_root
    from app.research.control_store import sync_work, apply_coverage, open_work, begin_action, finish_action
    root = create_root(db)
    plan = {"task_contract": {**contract(), "answer_scope": {"entities": ["AgentAlpha", "AgentBeta"]}}}
    sync_work(db, root.run_id, plan)
    gap = {"requirement_id": "r", "entity": "AgentBeta", "facet": "application", "cause": "answer_content_missing"}
    apply_coverage(db, root.run_id, {"complete": False, "gaps": [gap], "requirements": []})
    apply_coverage(db, root.run_id, {"complete": False, "gaps": [{"requirement_id": "r", "cause": "coverage_mapping_missing"}], "requirements": []})
    work = next(w for w in open_work(db, root.run_id) if w["entity"] == "AgentBeta")
    assert work["facet"] == "application" and work["reason_code"] == "answer_content_missing"
    op = begin_action(db, root.run_id, work, "read_retained", {"source_id": "S1", "offset": 0}, "evidence-v1")
    assert begin_action(db, root.run_id, work, "read_retained", {"source_id": "S1", "offset": 0}, "evidence-v1") is None
    finish_action(db, op, "succeeded", trace_id="trace-a", effect={"new_body_units": 1})
    assert any(w["entity"] == "AgentBeta" for w in open_work(db, root.run_id))
    db.expire_all()
    assert any(w["work_item_id"] == work["work_item_id"] for w in open_work(db, root.run_id))


def test_visible_table_row_is_a_target_window():
    from app.reporting.writing_evidence import build_writing_evidence
    from .test_manual_run_repairs import candidate_bundle
    text = "# 框架典型应用场景\n\n| 框架 | 典型应用场景 |\n| --- | --- |\n| AgentAlpha | 内部文档问答系统与审批流程自动化 |\n| AgentBeta | 多角色协作研发与决策支持 |"
    c = {**contract(), "answer_scope": {"entities": ["AgentAlpha", "AgentBeta"]},
         "evidence_focus": [{"entity": "AgentBeta", "facet": "application"}]}
    writing = build_writing_evidence(candidate_bundle(text), c, 5000)
    assert any("多角色协作研发" in u.text and "AgentBeta" in u.text and "典型应用场景" in u.text for u in writing.factual_units)


class WorkJudge:
    """Offline provider: first omits a cell, then uses the focused evidence."""
    def __init__(self):
        self.prompts = []
    def is_available(self):
        return True
    def complete(self, messages, **kwargs):
        value = json.loads(messages[-1].content)
        self.prompts.append(value)
        focused = bool(value["contract"].get("evidence_focus"))
        answer = "框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 用于内部知识检索和文档问答 [CIT-001-01]。"
        if focused:
            answer += "AgentBeta 用于多角色协作研发与决策支持 [CIT-001-01]。"
        return LLMResponse(success=True, provider="fixture", content=answer)
    def structured_complete(self, messages, **kwargs):
        value = json.loads(messages[-1].content)
        if "application_candidates" in value:
            rows = []
            for candidate in value["application_candidates"]:
                quote = next((c["text"] for c in candidate["claims"] if "用于" in c["text"] and (
                    candidate["entity"] in c["text"] or c.get("section_entity") == candidate["entity"])), "")
                rows.append({"entity": candidate["entity"], "concrete_task": bool(quote),
                    "workload_quote": quote, "reason": "A supported usage task is required"})
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"applications": rows}))
        if "cases" in value:
            rows = [{**case["identity"], "verdict": "supported", "evidence_quote": case["evidence_window"]}
                    for case in value["cases"]]
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"verdicts": rows}))
        claims = value["strictly_supported_claims"]
        if "proposed_bindings" in value:
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"complete": True,
                "entities": [{"canonical_name": n, "listed_as_item": True, "category_ok": True}
                             for n in ["AgentAlpha", "AgentBeta"]]}))
        if "required_comparison_cells" not in value:
            unit = value["evidence"]["factual_units"][0]
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"complete": True,
                "entities": [{"canonical_name": n, "display_name": n, "marker_starts": [m for c in claims
                    if n in c["text"] for m in c["marker_starts"]][:1], "citation": unit["citation_id"], "evidence_quote": unit["text"],
                    "membership_quote": next(c["text"] for c in claims if n in c["text"])}
                    for n in ["AgentAlpha", "AgentBeta"]]}))
        cells = []
        for cell in value["required_comparison_cells"]["r"]:
            markers = [m for c in claims if cell["entity"] in c["text"] and "用于" in c["text"] for m in c["marker_starts"]]
            cells.append({**cell, "complete": bool(markers), "marker_starts": markers, "reason": "specific application",
                "application_kind": "concrete_task" if markers else "missing",
                "application_workload_quote": next((c["text"] for c in claims if cell["entity"] in c["text"] and "用于" in c["text"]), "")})
        row = {"requirement_id": "r", "complete": all(c["complete"] for c in cells),
            "marker_starts": [m for c in claims for m in c["marker_starts"]], "comparison_cells": cells,
            "reason": "each item needs a use case"}
        return LLMResponse(success=True, provider="fixture", content=json.dumps({"requirements": [row],
            "identity_bindings": [{"canonical_name": n, "same_entity": True, "category_ok": True,
                "marker_starts": [m for c in claims if n in c["text"] for m in c["marker_starts"]]}
                for n in value["referential_items"]],
            "answer_inventory": {"complete": True, "entities": value["referential_items"]}}))


def test_controller_reprojects_rejudges_then_confirms_actual_report(db, r12_settings, tmp_path, monkeypatch):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.research.controller import run_work_loop
    from app.research.control_store import project_work
    from app.research.answer_coverage import persist_final_answer_coverage
    from app.evidence.models import ReportRevision, ReportClaimOccurrence
    from app.research.models import ResearchOperation
    from sqlalchemy import select
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    root = create_root(db, contract()["original_task"])
    plan = {"task_contract": contract(), "allowed_tools": []}
    bundle = candidate_bundle("框架包括 AgentAlpha、AgentBeta。AgentAlpha 用于内部知识检索和文档问答。AgentBeta 用于多角色协作研发与决策支持。")
    judge = WorkJudge()
    returned = run_work_loop(db, root.run_id, plan, r12_settings, bundle, judge, loader=lambda: bundle, traces=[])
    assert returned is bundle
    assert plan["work_controller"]["stop_reason"] == "candidate_answers_ready", plan["research_findings"]
    assert any("AgentBeta" in str(p["evidence"]) and p["contract"].get("evidence_focus") for p in judge.prompts)
    assert all(w["answer_status"] != "confirmed" for w in project_work(db, root.run_id))
    operations = db.scalars(select(ResearchOperation).where(ResearchOperation.root_run_id == root.run_id)).all()
    assert any(o.operation_kind == "reproject" for o in operations)
    finding = plan["research_findings"][-1]
    coverage, text = finding["coverage"], finding["markdown"]
    digest = hashlib.sha256(text.strip().encode()).hexdigest()
    db.add(ReportRevision(report_revision_id="report", root_run_id=root.run_id, content_hash=digest,
        final_answer_hash=digest, report_path="fixture.md", status="complete"))
    db.flush()
    db.add(ReportClaimOccurrence(claim_occurrence_id="claim", report_revision_id="report", section="answer",
        claim_text=text, normalized_claim_text=text, sentence_start=0, sentence_end=len(text)))
    db.flush()
    occurrences = {"report_revision": {"report_revision_id": "report", "final_answer_hash": digest},
        "citation_occurrences": [{"marker_start": m, "verdict": "supported", "claim_occurrence_id": "claim"}
                                  for r in coverage["requirements"] for m in r["marker_starts"]]}
    final = persist_final_answer_coverage(db, root, plan["task_contract"], coverage, occurrences)
    assert final["complete"]
    assert all(w["answer_status"] in {"confirmed", "superseded"} for w in project_work(db, root.run_id))
    # Deleting the mandatory application changes the byte identity and
    # invalidates confirmation even if the previous semantic audit is reused.
    occurrences["report_revision"]["final_answer_hash"] = "changed"
    assert not persist_final_answer_coverage(db, root, plan["task_contract"], coverage, occurrences)["complete"]
    assert all(w["answer_status"] != "confirmed" for w in project_work(db, root.run_id))


def test_versioned_display_name_binds_attested_base_without_erasing_dates(tmp_path, monkeypatch):
    from .test_manual_run_repairs import Understanding, candidate_bundle
    from app.research.comparison_scope import comparison_spec, select_comparison_candidates
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    task = "比较最近几款个人智能体"
    c = {"original_task": task, "as_of": "2026-10-08", "comparison_scope": comparison_spec(task)}
    categories = [f"{name} is a personal assistant that executes autonomous tasks." for name in ["AgentAlpha", "AgentBeta"]]
    activity = "In 2026, AgentAlpha and AgentBeta have 2000 active users."
    candidates = [{"name": name + " (Workspace / Edition)", "citation": "CIT-001-01", "evidence_quote": quote,
        "selection_quote": activity} for name, quote in zip(["AgentAlpha", "AgentBeta"], categories)]
    payload = {"candidates": candidates, "criteria": "Observed activity", "time_scope": "2026"}
    result = select_comparison_candidates(c, candidate_bundle("\n".join(categories + [activity])), Understanding(payload))
    assert result["comparison_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
    assert result["comparison_scope"]["selection"]["candidates"][0]["display_name"].endswith("(Workspace / Edition)")
    stale = activity.replace("2026", "2020")
    for candidate in candidates:
        candidate["selection_quote"] = stale
    result = select_comparison_candidates(c, candidate_bundle("\n".join(categories + [stale])), Understanding(payload))
    assert not result["comparison_scope"]["entities"]
    assert result["comparison_scope"]["selection_attempt"]["rejections"][0]["cause"] == "dated_activity_missing"


def test_partial_final_proof_retains_answered_obligation(db, tmp_path, monkeypatch):
    from .conftest import create_root
    from .test_obligation_repairs import _contract, CoverageJudge
    from .test_manual_run_repairs import candidate_bundle
    from app.evidence.citation_validator import CitationValidationReport, CitationValidationDetail
    from app.evidence.models import ReportRevision, ReportClaimOccurrence
    from app.research.answer_coverage import assess_answer_coverage, persist_final_answer_coverage
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    root = create_root(db)
    text = "Readers run concurrently [CIT-001-01]."
    marker = text.index("CIT-")
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", text, text, 1, marker_start=marker)])
    c = _contract()
    coverage = assess_answer_coverage(text, c, validation, CoverageJudge(), provenance=candidate_bundle(text))
    assert not coverage["complete"]
    digest = hashlib.sha256(text.strip().encode()).hexdigest()
    db.add(ReportRevision(report_revision_id="partial", root_run_id=root.run_id, content_hash=digest,
        final_answer_hash=digest, report_path="fixture.md", status="complete"))
    db.flush()
    db.add(ReportClaimOccurrence(claim_occurrence_id="partial-claim", report_revision_id="partial", section="answer",
        claim_text=text, normalized_claim_text=text, sentence_start=0, sentence_end=len(text)))
    db.flush()
    occurrences = {"report_revision": {"report_revision_id": "partial", "final_answer_hash": digest},
        "citation_occurrences": [{"marker_start": marker, "verdict": "supported", "claim_occurrence_id": "partial-claim"}]}
    final = persist_final_answer_coverage(db, root, c, coverage, occurrences)
    assert not final["complete"]
    assert {r["requirement_id"]: r["status"] for r in final["requirements"]} == {"r1": "satisfied", "r2": "uncovered"}
    assert final["requirements"][0]["claim_occurrence_ids"] == ["partial-claim"]


def test_budget_approval_resumes_same_work_and_deferred_intent(db, r12_settings):
    from .conftest import create_root
    from app.research.control_store import sync_work, open_work, begin_action, finish_action
    from app.agent.budget import BudgetRuntime, pause_for_token_approval, approve_token_budget
    from app.trace import store
    root = create_root(db)
    plan = {"task_contract": contract(), "allowed_tools": ["web_fetcher"], "steps": []}
    store.replace_agent_run_plan(db, root.run_id, plan)
    sync_work(db, root.run_id, plan)
    item = open_work(db, root.run_id)[0]
    args = {"source_id": "S1", "offset": 0}
    operation = begin_action(db, root.run_id, item, "read_retained", args, "source-v1")
    finish_action(db, operation, "deferred", None, {"issued": False})
    settings = r12_settings.model_copy(update={"research_max_tokens": 1000})
    BudgetRuntime(db, root.run_id, settings).reserve(tokens=100)
    pause_for_token_approval(db, root.run_id)
    from app.api.tasks import confirm_task
    from app.schemas import TaskConfirmRequest
    confirm_task(root.run_id, TaskConfirmRequest(approved=True, resume=False, unlimited_tokens=True), db=db, start_async=False)
    assert open_work(db, root.run_id)[0]["work_item_id"] == item["work_item_id"]
    assert BudgetRuntime(db, root.run_id, settings).snapshot()["accounted_tokens"] == 100
    resumed = begin_action(db, root.run_id, item, "read_retained", args, "source-v1")
    assert resumed.attempt == 2
    finish_action(db, resumed, "interrupted", None, {"issued": "unknown"})
    assert begin_action(db, root.run_id, item, "read_retained", args, "source-v1") is None


def test_report_feedback_selects_then_dispatches_same_cohort_cells(db, r12_settings, tmp_path, monkeypatch):
    from .conftest import create_root, add_web_trace, materialize_run
    from .test_manual_run_repairs import Understanding
    from app.research.scope import create_research_scope, create_research_node, list_scope_nodes
    from app.research.node_executor import ResearchNodeExecutor
    from app.research.orchestrator import recover_scope_evidence
    from app.research.comparison_scope import comparison_spec
    from app.trace import store
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    task = "Compare several personal assistants in memory."
    root = create_root(db, task)
    c = {"original_task": task, "obligation_version": "v2", "comparison_scope": comparison_spec(task),
        "requirements": [{"requirement_id": "m", "question_id": "q", "predicate": "memory"}],
        "requirement_focus": {"m": {"facet": "memory"}}}
    plan = {"task_contract": c, "allowed_tools": ["web_fetcher"], "steps": []}
    store.replace_agent_run_plan(db, root.run_id, plan)
    scope = create_research_scope(db, root.run_id, c)
    create_research_node(db, scope.scope_id, parent_node_id=None, run_id=root.run_id, node_type="root",
        topic=task, query=task, research_goal=task, depth=0, priority=0)
    quote = "AgentAlpha and AgentBeta are personal assistant products that execute autonomous tasks."
    criterion = "AgentAlpha and AgentBeta are compared in this hands-on product review."
    add_web_trace(db, root.run_id, quote + " " + criterion, "selection")
    materialize_run(db, root, r12_settings)
    client = Understanding({"candidates": [{"name": n, "citation": "CIT-001-01", "evidence_quote": quote,
        "selection_quote": criterion} for n in ["AgentAlpha", "AgentBeta"]], "criteria": "Comparative review", "time_scope": "undated"})
    observed = []
    def runner(session, rid, settings, client):
        child = store.get_fresh_agent_run(session, rid)
        child_plan = json.loads(child.plan_json)
        members = child_plan["task_contract"]["comparison_scope"]["entities"]
        assert len(members) == 1
        observed.extend(members)
        add_web_trace(session, rid, members[0] + " stores persistent memory in a journal between sessions.", members[0])
        materialize_run(session, child, settings)
        store.update_agent_run_status(session, rid, "completed")
        return {"run_id": rid, "status": "completed"}
    def findings(bundle, contract, client, *, previous=None):
        gaps = [{"requirement_id": "m", "entity": n, "facet": "memory", "cause": "source_quality_missing"}
                for n in ["AgentAlpha", "AgentBeta"] if n not in observed]
        return {"coverage": {"complete": not gaps, "requirements": [], "gaps": gaps}, "validation": {}, "decision_audit": None}
    monkeypatch.setattr("app.research.findings.assess_research_findings", findings)
    executor = ResearchNodeExecutor(runner)
    monkeypatch.setattr("app.research.orchestrator.ResearchNodeExecutor", lambda: executor)
    feedback = {"answer_gaps": [{"requirement_id": "m", "cause": "comparison_selection_missing"}]}
    returned = recover_scope_evidence(db, root.run_id, plan, r12_settings, feedback, client)
    assert returned and observed == ["AgentAlpha", "AgentBeta"]
    assert plan["task_contract"]["comparison_scope"]["entities"] == observed
    children = [n for n in list_scope_nodes(db, scope.scope_id) if n.parent_node_id]
    assert len(children) == 2
    assert all(json.loads(n.metadata_json).get("recovery_work_item_id") for n in children)


def test_full_retry_clears_execution_inventory_but_keeps_named_obligations():
    from app.api.tasks import _clear_retry_derived_state
    c = contract()
    c.update(answer_scope={"entities": ["AgentAlpha"]}, evidence_focus=[{"entity": "AgentAlpha"}],
        comparison_scope={"selection_required": True, "entities": ["AgentAlpha"], "selection": {"decision_audit": "old"},
                          "selection_attempt": {"accepted_count": 1}, "entity_bindings": [{"canonical_name": "AgentAlpha"}]})
    plan = {"task_contract": c, "research_work": {"old": True}, "work_controller": {"old": True}}
    _clear_retry_derived_state(plan)
    assert c["requirements"] == contract()["requirements"]
    assert c["comparison_scope"]["entities"] == []
    assert not any(k in c for k in ["answer_scope", "evidence_focus"])
    assert "research_work" not in plan and "work_controller" not in plan


def test_no_body_effect_on_one_candidate_does_not_abandon_other_cells(db, r12_settings, monkeypatch):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.research.controller import run_work_loop
    from app.research.control_store import project_work
    from app.tools.base import ToolResult
    root = create_root(db)
    c = {**contract(), "evidence_requirement": "substantive", "as_of": "2026-10-08",
         "requirement_focus": {"r": {"facet": "selection"}},
         "comparison_scope": {"selection_required": True, "entities": [], "selection_attempt": {"rejections": [
             {"name": n, "cause": "selection_criterion_missing"} for n in ["AgentAlpha", "AgentBeta"]]}}}
    plan = {"task_contract": c, "allowed_tools": ["tavily_search"]}
    bundle = candidate_bundle("Candidate assistant descriptions remain available for research.")
    monkeypatch.setattr("app.research.controller.refresh_comparison_contract", lambda *_: False)
    monkeypatch.setattr("app.research.recovery.materialize_execution_provenance", lambda *_: bundle)
    calls = []
    def search(name, args, *_):
        calls.append(args["query"])
        return ToolResult(success=False, output={}, error_message="fixture has no new material")
    monkeypatch.setattr("app.research.recovery.execute_governed_operation", search)
    run_work_loop(db, root.run_id, plan, r12_settings, bundle, WorkJudge(), loader=lambda: bundle, traces=[])
    assert len(calls) == 4
    assert [q.split()[0] for q in calls] == ["AgentAlpha", "AgentBeta", "AgentAlpha", "AgentBeta"]
    assert plan["work_controller"]["stop_reason"] == "same_gap_repair_round_limit"
    named = [w for w in project_work(db, root.run_id) if w["entity"]]
    assert len(named) == 2
    assert all(w["answer_status"] == "unanswered" and w["reason_code"] == "selection_criterion_missing" for w in named)


@pytest.mark.parametrize("provider_fails", [False, True])
def test_entity_binding_uses_json_protocol_and_keeps_provider_failure_distinct(tmp_path, monkeypatch, provider_fails):
    from .test_manual_run_repairs import candidate_bundle
    from app.research.comparison_scope import select_comparison_candidates
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    quote = "It autonomously executes personal tasks for its owner."
    criterion = "This review compares AgentAlpha and AgentBeta for personal task execution."
    text = "AgentAlpha: " + quote + " AgentBeta: " + quote + " " + criterion
    class ProtocolJudge:
        def is_available(self):
            return True
        def structured_complete(self, messages, **kwargs):
            assert any("json" in m.content.casefold() for m in messages)
            inputs = json.loads(messages[-1].content)
            if "canonical_name" in inputs:
                if provider_fails:
                    return LLMResponse(success=False, provider="fixture", error_message="Invalid JSON request",
                                       metadata={"error_type": "invalid_request", "http_status": 400})
                payload = {"canonical_name": inputs["canonical_name"], "category_ok": True, "criterion_ok": True, "date_ok": False}
            else:
                payload = {"candidates": [{"name": n, "citation": "CIT-001-01", "evidence_quote": quote,
                    "selection_quote": criterion} for n in ["AgentAlpha", "AgentBeta"]],
                    "criteria": "Comparative review", "time_scope": "undated"}
            return LLMResponse(success=True, provider="fixture", content=json.dumps(payload))
    result = select_comparison_candidates({**contract(), "comparison_scope": {"selection_required": True, "entities": []}},
                                         candidate_bundle(text), ProtocolJudge())
    scope = result["comparison_scope"]
    if provider_fails:
        assert not scope["entities"]
        refusal = scope["selection_attempt"]["rejections"][0]
        assert refusal["cause"] == "selection_provider_failure" and refusal["error_type"] == "invalid_request"
        assert refusal["decision_audit"]["decision_sha256"]
    else:
        assert scope["entities"] == ["AgentAlpha", "AgentBeta"]


def test_descriptive_clause_cannot_become_an_automatic_inventory_identity(tmp_path, monkeypatch):
    from .test_manual_run_repairs import candidate_bundle, Understanding
    from app.research.referential_scope import freeze_item_inventory
    from app.reporting.writing_evidence import build_writing_evidence
    from app.research.answer_coverage import assess_answer_coverage
    from app.evidence.citation_validator import CitationValidationReport
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    text = "这些框架有明确的应用场景描述。"
    writing = build_writing_evidence(candidate_bundle(text))
    freeze_item_inventory(c, [{"text": text, "marker_starts": [0]}], writing,
                          Understanding({"complete": False, "entities": [], "reason": "No named framework list"}))
    assert "answer_scope" not in c
    result = assess_answer_coverage(text, c, CitationValidationReport(), WorkJudge())
    assert not result["complete"]
    assert all(g["cause"] == "coverage_mapping_missing" for g in result["gaps"])


@pytest.mark.parametrize("failure", ["benchmark", "dependency", "omission", "provider", "membership_quote"])
def test_named_inventory_needs_independent_membership_category_and_completeness(tmp_path, monkeypatch, failure):
    from .test_manual_run_repairs import candidate_bundle
    from app.research.referential_scope import freeze_item_inventory
    from app.reporting.writing_evidence import build_writing_evidence
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    body = "AgentAlpha is a framework for autonomous tasks using AgentBeta and measured on BenchAlpha."
    writing = build_writing_evidence(candidate_bundle(body))
    citation = writing.factual_units[0].citation_id
    text = body + f" [{citation}]"
    name = {"benchmark": "BenchAlpha", "dependency": "AgentBeta"}.get(failure, "AgentAlpha")
    calls = []
    class InventoryJudge:
        def structured_complete(self, messages, **kwargs):
            value = json.loads(messages[-1].content)
            calls.append(value)
            if "proposed_bindings" not in value:
                result = {"complete": True, "entities": [{"canonical_name": name, "marker_starts": [0],
                    "citation": citation, "evidence_quote": body,
                    "membership_quote": "unsupported invented membership" if failure == "membership_quote" else text}]}
            elif failure == "provider":
                return LLMResponse(success=False, provider="fixture", error_message="review unavailable",
                                   metadata={"error_type": "rate_limit"})
            else:
                names = [name, "AgentGamma"] if failure == "omission" else [name]
                result = {"complete": failure == "omission", "entities": [{"canonical_name": n,
                    "listed_as_item": failure != "dependency", "category_ok": failure != "benchmark"}
                    for n in names], "reason": failure}
            return LLMResponse(success=True, provider="fixture", content=json.dumps(result))
    c = contract()
    freeze_item_inventory(c, [{"text": text, "marker_starts": [0], "marker_citations": {"0": citation}}], writing, InventoryJudge())
    assert "answer_scope" not in c  # No fake executable objects are created.
    if failure == "membership_quote":
        assert len(calls) == 2  # One bounded same-evidence mapping repair.
    else:
        assert c["answer_scope_attempt"]["binding_audit"]["decision_sha256"]
    if failure == "provider":
        assert c["answer_scope_attempt"]["provider_failure"]["error_type"] == "rate_limit"


def test_verified_inventory_preserves_both_audits_in_entity_provenance(db, tmp_path, monkeypatch):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.research.referential_scope import freeze_item_inventory
    from app.reporting.writing_evidence import build_writing_evidence
    from app.research.control_store import sync_work
    from app.research.models import ResearchEntity
    from sqlalchemy import select
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    body = "框架包括 AgentAlpha、AgentBeta，分别用于文档检索和协作研发。"
    text = body + " [CIT-001-01]"
    c = contract()
    freeze_item_inventory(c, [{"text": text, "marker_starts": [0], "marker_citations": {"0": "CIT-001-01"}}],
                          build_writing_evidence(candidate_bundle(body)), WorkJudge())
    assert c["answer_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
    root = create_root(db)
    sync_work(db, root.run_id, {"task_contract": c})
    entities = db.scalars(select(ResearchEntity).where(ResearchEntity.root_run_id == root.run_id)).all()
    assert len(entities) == 2
    for entity in entities:
        provenance = json.loads(entity.provenance_json)
        assert provenance["decision_audit"]["decision_sha256"]
        assert provenance["binding_audit"]["decision_sha256"]


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_missing_json_cells_repair_mapping_before_evidence_acquisition(tmp_path, monkeypatch, repair_succeeds):
    from .test_manual_run_repairs import candidate_bundle
    from app.evidence.citation_validator import CitationValidationReport, CitationValidationDetail
    from app.research.answer_coverage import assess_answer_coverage
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    c["answer_scope"] = {"entities": ["AgentAlpha", "AgentBeta"]}
    text = "AgentAlpha 用于文档检索 [CIT-001-01]。AgentBeta 用于研发协作 [CIT-001-01]。"
    markers = [m.start() for m in re.finditer("CIT-", text)]
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", text, text,
        1, marker_start=m) for m in markers])
    class MappingJudge:
        def __init__(self):
            self.calls = 0
        def is_available(self):
            return True
        def structured_complete(self, messages, **kwargs):
            self.calls += 1
            value = json.loads(messages[-1].content)
            row = {"requirement_id": "r", "complete": True, "marker_starts": markers,
                   "reason": "Both uses are answered", "facets": []}
            if "mapping_feedback" in value and repair_succeeds:
                row["comparison_cells"] = [{"entity": n, "facet": "application", "complete": True,
                    "marker_starts": [markers[i]], "reason": "specific application",
                    "application_kind": "concrete_task",
                    "application_workload_quote": next(c["text"] for c in value["strictly_supported_claims"] if n in c["text"])}
                    for i, n in enumerate(["AgentAlpha", "AgentBeta"])]
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"requirements": [row]}))
    judge = MappingJudge()
    result = assess_answer_coverage(text, c, validation, judge, provenance=candidate_bundle(text))
    assert judge.calls == 2
    assert result["complete"] is repair_succeeds
    if not repair_succeeds:
        assert {g["cause"] for g in result["gaps"]} == {"coverage_mapping_missing"}


@pytest.mark.parametrize("actual", [["AgentAlpha"], ["AgentAlpha", "AgentBeta", "AgentGamma"], ["AgentAlpha", "AgentAlpha"]])
def test_final_answer_inventory_cannot_remove_add_or_duplicate_frozen_items(tmp_path, monkeypatch, actual):
    from .test_manual_run_repairs import candidate_bundle
    from app.research.findings import assess_research_findings
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    class DriftingJudge(WorkJudge):
        def structured_complete(self, messages, **kwargs):
            response = super().structured_complete(messages, **kwargs)
            payload = json.loads(response.content)
            if "requirements" in payload:
                payload["answer_inventory"] = {"complete": True, "entities": actual}
            return LLMResponse(success=True, provider="fixture", content=json.dumps(payload))
    c["evidence_focus"] = [{"entity": "AgentBeta", "facet": "application"}]
    result = assess_research_findings(candidate_bundle("框架包括 AgentAlpha、AgentBeta。AgentAlpha 用于内部知识检索和文档问答。AgentBeta 用于多角色协作研发与决策支持。"), c, DriftingJudge())
    assert not result["coverage"]["complete"]
    assert any("actual answer inventory" in gap["detail"] for gap in result["coverage"]["gaps"])
    from app.research.answer_coverage import _verified_audit
    assert not _verified_audit(result["coverage"], c)
    assert _verified_audit(result["coverage"], c, only_requirement="r",
                           only_cell={"entity": "AgentAlpha", "facet": "application"})


def test_synthesis_does_not_expose_previous_answer_offsets_as_current_markers():
    from app.research.state import synthesis_contract
    c = contract()
    c.update(answer_scope={"version": "answer-inventory-v2", "entities": ["AgentAlpha"],
        "bindings": [{"canonical_name": "AgentAlpha", "marker_starts": [115]}], "binding_audit": {"old": True}},
        controller_findings={"marker_starts": [345]}, evidence_focus=[{"entity": "AgentAlpha"}],
        answer_scope_attempt={"binding_audit": "old"})
    prompt = synthesis_contract(c)
    assert prompt["answer_scope"] == {"version": "answer-inventory-v2", "entities": ["AgentAlpha"]}
    assert prompt["evidence_focus"] == c["evidence_focus"]
    assert "marker_starts" not in json.dumps(prompt)
    assert c["answer_scope"]["bindings"][0]["marker_starts"] == [115]


@pytest.mark.parametrize("repaired", [True, False])
def test_stale_answer_marker_is_mapping_failure_and_uses_current_occurrences(tmp_path, monkeypatch, repaired):
    from .test_manual_run_repairs import candidate_bundle
    from app.evidence.citation_validator import CitationValidationReport, CitationValidationDetail
    from app.research.answer_coverage import assess_answer_coverage
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = {**contract(), "original_task": "List frameworks", "requirements": [{"requirement_id": "r", "predicate": "List frameworks", "question_id": "q"}]}
    text = "AgentAlpha is a framework [CIT-001-01]."
    marker = text.index("CIT-")
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", text, text, 1, marker_start=marker)])
    class MarkerJudge:
        def __init__(self): self.calls = 0
        def is_available(self): return True
        def structured_complete(self, messages, **kwargs):
            self.calls += 1
            value = json.loads(messages[-1].content)
            mapped = marker if "mapping_feedback" in value and repaired else 999
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"requirements": [
                {"requirement_id": "r", "complete": True, "marker_starts": [mapped], "reason": "list answered"}]}))
    judge = MarkerJudge()
    result = assess_answer_coverage(text, c, validation, judge, provenance=candidate_bundle(text))
    assert judge.calls == 2
    assert result["complete"] is repaired
    if not repaired:
        assert {gap["cause"] for gap in result["gaps"]} == {"coverage_mapping_missing"}


@pytest.mark.parametrize("version", ["answer-inventory-v1", "answer-inventory-v2"])
@pytest.mark.parametrize("rejection", ["same_entity", "category_ok", "missing", "stale_marker"])
def test_namesake_or_unverified_identity_never_confirms_application(tmp_path, monkeypatch, version, rejection):
    from .test_manual_run_repairs import candidate_bundle
    from app.research.findings import assess_research_findings
    from app.research.answer_coverage import _verified_audit
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    c["evidence_focus"] = [{"entity": "AgentBeta", "facet": "application"}]
    c["answer_scope"] = {"version": version, "entities": ["AgentAlpha", "AgentBeta"], "bindings": [
        {"canonical_name": "AgentAlpha", "evidence_quote": "AgentAlpha is a benchmark for evaluating task completion.", "marker_starts": [999]},
        {"canonical_name": "AgentBeta", "evidence_quote": "AgentBeta is a framework for multi-role collaboration."}]}
    class IdentityJudge(WorkJudge):
        def structured_complete(self, messages, **kwargs):
            value = json.loads(messages[-1].content)
            result = super().structured_complete(messages, **kwargs)
            data = json.loads(result.content)
            if "requirements" in data:
                assert "benchmark" in value["frozen_entity_bindings"][0]["evidence_quote"]
                assert "marker_starts" not in json.dumps(value["frozen_entity_bindings"])
                assert any("数据库" in " ".join(e["quotes"]) for e in value["current_entity_evidence"].values())
                row = data["identity_bindings"][0]
                if rejection == "missing": data.pop("identity_bindings")
                elif rejection == "stale_marker": row["marker_starts"] = [999]
                else: row[rejection] = False
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    result = assess_research_findings(candidate_bundle("框架包括 AgentAlpha、AgentBeta。AgentAlpha 是数据库平台，用于内部知识检索和文档问答。AgentBeta 用于多角色协作研发与决策支持。"), c, IdentityJudge())
    coverage = result["coverage"]
    assert not coverage["complete"]
    assert any(g["cause"] == "inventory_identity_missing" for g in coverage["gaps"])
    assert not _verified_audit(coverage, c, only_requirement="r", only_cell={"entity": "AgentAlpha", "facet": "application"})
    if rejection != "missing":
        assert _verified_audit(coverage, c, only_requirement="r", only_cell={"entity": "AgentBeta", "facet": "application"})


@pytest.mark.parametrize("repair", [True, False])
def test_inventory_repairs_exact_quote_and_member_markers_before_acquisition(tmp_path, monkeypatch, repair):
    from .test_manual_run_repairs import candidate_bundle
    from app.reporting.writing_evidence import build_writing_evidence
    from app.research.referential_scope import freeze_item_inventory
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    text = "AgentAlpha is a framework for autonomous task execution."
    writing = build_writing_evidence(candidate_bundle(text))
    citation = writing.factual_units[0].citation_id
    claims = [{"text": text, "marker_starts": [0], "marker_citations": {"0": citation}},
              {"text": "AgentAlpha is used for document workflows.", "marker_starts": [88], "marker_citations": {"88": citation}}]
    calls = []
    class MappingJudge:
        def structured_complete(self, messages, **kwargs):
            inputs = json.loads(messages[-1].content);calls.append(inputs)
            if "proposed_bindings" in inputs:
                data = {"complete": True, "entities": [{"canonical_name": "AgentAlpha", "listed_as_item": True, "category_ok": True}]}
            else:
                fixed = "binding_feedback" in inputs and repair
                if "binding_feedback" in inputs:
                    error = inputs["binding_feedback"]["errors"][0]
                    assert error["allowed_membership_markers"] == [0]
                    assert set(error["reasons"]) == {"quote_not_verbatim_in_window", "markers_not_in_membership_claim"}
                    assert inputs["binding_feedback"]["prior_decision_audit"]["decision_sha256"]
                data = {"complete": True, "entities": [{"canonical_name": "AgentAlpha", "citation": citation,
                    "membership_quote": text, "marker_starts": [0] if fixed else [0, 88],
                    "evidence_quote": text if fixed else text.replace("framework", "agent framework")}]}
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    c = contract()
    freeze_item_inventory(c, claims, writing, MappingJudge())
    assert bool(c.get("answer_scope")) is repair
    assert len(calls) == (3 if repair else 2)
    if repair:
        assert c["answer_scope"]["bindings"][0]["marker_starts"] == [0]
    else:
        assert c["answer_scope_attempt"]["binding_errors"]


def test_final_answer_can_bind_inventory_from_current_validated_writer_windows(tmp_path, monkeypatch):
    from .test_manual_run_repairs import candidate_bundle
    from app.reporting.writing_evidence import build_writing_evidence
    from app.evidence.citation_validator import validate_citations
    from app.research.answer_coverage import assess_answer_coverage, _verified_audit
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract();c["evidence_focus"] = [{"entity": "AgentBeta", "facet": "application"}]
    c["answer_scope_attempt"] = {"version": "semantic-item-inventory-v1", "reason": "Earlier draft mapping was invalid"}
    body = "框架包括 AgentAlpha、AgentBeta。AgentAlpha 用于内部知识检索和文档问答。AgentBeta 用于多角色协作研发与决策支持。"
    bundle = candidate_bundle(body);writing = build_writing_evidence(bundle, c)
    answer = "框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 用于内部知识检索和文档问答 [CIT-001-01]。AgentBeta 用于多角色协作研发与决策支持 [CIT-001-01]。"
    judge = WorkJudge()
    validation = validate_citations(answer, bundle, writing_evidence=writing, task_contract=c,
        multilingual_llm_client=judge, use_multilingual_adjudication=True)
    result = assess_answer_coverage(answer, c, validation, judge, provenance=bundle, writing_evidence=writing)
    assert c["answer_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
    assert result["complete"] and _verified_audit(result, c)


def test_provider_failure_stops_control_before_search_or_semantic_judgment(db, r12_settings, tmp_path, monkeypatch):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.research.controller import run_work_loop
    from app.research.state import semantic_contract
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    class Unavailable(WorkJudge):
        def complete(self, *_args, **_kwargs):
            return LLMResponse(success=False, provider="fixture", error_message="service request failed",
                metadata={"error_type": "provider_unavailable", "http_status": 402})
        def structured_complete(self, *_args, **_kwargs):
            raise AssertionError("No coverage or inventory judgment may follow synthesis failure")
    monkeypatch.setattr("app.research.controller.recover_answer_evidence", lambda *_args, **_kwargs: pytest.fail("No acquisition after provider failure"))
    root = create_root(db);plan = {"task_contract": contract()}
    store = __import__("app.trace.store", fromlist=["replace_agent_run_plan"])
    store.replace_agent_run_plan(db, root.run_id, plan)
    bundle = candidate_bundle("AgentAlpha is a framework for document workflows.")
    run_work_loop(db, root.run_id, plan, r12_settings, bundle, Unavailable(), loader=lambda: bundle, traces=[])
    assert plan["work_controller"]["stop_reason"] == "findings_provider_failure"
    assert plan["task_contract"]["research_provider_failure"]["http_status"] == 402
    assert "research_provider_failure" not in semantic_contract(plan["task_contract"])
    from app.agent.outcome import assess_research_outcome
    fresh = store.get_fresh_agent_run(db, root.run_id)
    assert assess_research_outcome(fresh, plan, [], [], r12_settings)["error_code"] == "research_provider_failure"
    from app.api.tasks import _clear_retry_derived_state
    _clear_retry_derived_state(plan)
    assert "research_provider_failure" not in plan["task_contract"]


def test_final_inventory_provider_failure_does_not_trigger_coverage_calls(tmp_path, monkeypatch):
    from .test_manual_run_repairs import candidate_bundle
    from app.reporting.writing_evidence import build_writing_evidence
    from app.evidence.citation_validator import CitationValidationReport, CitationValidationDetail
    from app.research.answer_coverage import assess_answer_coverage
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    answer = "AgentAlpha is a framework for document workflows [CIT-001-01]."
    writing = build_writing_evidence(candidate_bundle(answer))
    validation = CitationValidationReport(details=[CitationValidationDetail(
        "CIT-001-01", "supported", answer, answer, 1, marker_start=answer.index("CIT-"))])
    class Unavailable:
        calls = 0
        def structured_complete(self, *_args, **_kwargs):
            self.calls += 1
            assert self.calls == 1
            return LLMResponse(success=False, provider="fixture", error_message="service request failed",
                metadata={"error_type": "provider_unavailable", "http_status": 402})
    judge = Unavailable()
    result = assess_answer_coverage(answer, contract(), validation, judge, writing_evidence=writing)
    assert not result["complete"] and judge.calls == 1
    assert result["provider_failure"]["http_status"] == 402
    assert result["provider_failure"]["decision_audit"]["decision_sha256"]


@pytest.mark.parametrize("repair", [True, False])
def test_identity_marker_mapping_repairs_same_evidence_before_search(tmp_path, monkeypatch, repair):
    from .test_manual_run_repairs import candidate_bundle
    from app.evidence.citation_validator import validate_citations
    from app.reporting.writing_evidence import build_writing_evidence
    from app.research.answer_coverage import assess_answer_coverage, _verified_audit
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    c["answer_scope"] = {"version": "answer-inventory-v2", "entities": ["AgentAlpha", "AgentBeta"],
        "bindings": [{"canonical_name": n, "evidence_quote": n + " is a framework."}
                     for n in ["AgentAlpha", "AgentBeta"]]}
    answer = "框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 用于内部知识检索和文档问答 [CIT-001-01]。AgentBeta 用于多角色协作研发与决策支持 [CIT-001-01]。企业工作流程得到支持 [CIT-001-01]。"
    bundle = candidate_bundle(re.sub(r"\s*\[CIT-\d{3}-\d{2}\]", "", answer))
    writing = build_writing_evidence(bundle, c)
    class MappingJudge(WorkJudge):
        coverage_calls = 0
        def structured_complete(self, messages, **kwargs):
            value = json.loads(messages[-1].content)
            result = super().structured_complete(messages, **kwargs)
            data = json.loads(result.content)
            if "requirements" in data:
                self.coverage_calls += 1
                row = data["identity_bindings"][0]
                foreign = value["strictly_supported_claims"][-1]["marker_starts"][0]
                if "mapping_feedback" in value:
                    error = value["mapping_feedback"]["identity_errors"][0]
                    assert error["canonical_name"] == "AgentAlpha"
                    assert error["mapping_reason"] == "invalid_identity_markers"
                    assert foreign not in error["allowed_marker_starts"]
                    assert value["mapping_feedback"]["prior_decision_audit"]["decision_sha256"]
                if "mapping_feedback" not in value or not repair:
                    row["marker_starts"].append(foreign)
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    judge = MappingJudge()
    validation = validate_citations(answer, bundle, writing_evidence=writing, task_contract=c,
        multilingual_llm_client=judge, use_multilingual_adjudication=True)
    result = assess_answer_coverage(answer, c, validation, judge, provenance=bundle)
    assert judge.coverage_calls == 2
    assert result["complete"] is repair
    assert _verified_audit(result, c) is repair


def test_source_candidate_pool_does_not_expand_supported_answer_inventory(tmp_path, monkeypatch):
    from .test_manual_run_repairs import candidate_bundle
    from app.reporting.writing_evidence import build_writing_evidence
    from app.research.referential_scope import freeze_item_inventory
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    member = "AgentAlpha is a framework for autonomous document workflows."
    body = member + " AgentBeta and AgentGamma are other frameworks with distinct uses."
    writing = build_writing_evidence(candidate_bundle(body))
    citation = writing.factual_units[0].citation_id
    claims = [{"text": member, "marker_starts": [10], "marker_citations": {"10": citation}}]
    class InventoryJudge:
        def structured_complete(self, messages, **_kwargs):
            inputs = json.loads(messages[-1].content)
            assert inputs["inventory_policy"]["boundary"] == "supported_answer_list"
            assert inputs["inventory_policy"]["additional_source_names_expand_inventory"] is False
            assert "AgentBeta" in json.dumps(inputs["evidence"])
            if "proposed_bindings" in inputs:
                assert "Do not return source-only names" in messages[0].content
                data = {"complete": True, "entities": [{"canonical_name": "AgentAlpha",
                    "listed_as_item": True, "category_ok": True}]}
            else:
                data = {"complete": True, "entities": [{"canonical_name": "AgentAlpha",
                    "marker_starts": [10], "citation": citation, "evidence_quote": member,
                    "membership_quote": member}]}
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    c = contract()
    freeze_item_inventory(c, claims, writing, InventoryJudge())
    assert c["answer_scope"]["entities"] == ["AgentAlpha"]
    assert c["answer_scope"]["binding_audit"]["decision_sha256"]


@pytest.mark.parametrize("kind,quote,valid", [
    ("deployment_context", "AgentAlpha is suitable for enterprise deployment", False),
    ("framework_characteristic", "AgentAlpha uses role-based teams", False),
    ("concrete_task", "AgentAlpha automates customer support", True),
    ("concrete_task", "AgentBeta automates customer support", False),
])
def test_application_requires_a_concrete_task_from_its_current_claim(kind, quote, valid):
    from app.research.answer_coverage import _comparison_cell_gaps
    cell = {"entity": "AgentAlpha", "facet": "application"}
    claim_text = "AgentAlpha automates customer support" if "AgentBeta" in quote else quote
    row = {"marker_starts": [10], "comparison_cells": [{**cell, "complete": True,
        "marker_starts": [10], "application_kind": kind, "application_workload_quote": quote}]}
    gaps = _comparison_cell_gaps(row, [cell], [{"text": claim_text, "marker_starts": [10]}])
    assert (not gaps) is valid


@pytest.mark.parametrize("primary_kind", ["deployment_context", "concrete_task"])
def test_deployment_only_answer_never_confirms_final_application_work(db, tmp_path, monkeypatch, primary_kind):
    from .conftest import create_root
    from .test_manual_run_repairs import candidate_bundle
    from app.research.findings import assess_research_findings
    from app.research.answer_coverage import persist_final_answer_coverage
    from app.research.control_store import project_work
    from app.evidence.models import ReportRevision, ReportClaimOccurrence
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract()
    answer = "框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 适合企业快速部署 [CIT-001-01]。AgentBeta 用于多角色协作研发与决策支持 [CIT-001-01]。"
    body = re.sub(r"\s*\[CIT-\d{3}-\d{2}\]", "", answer)
    class DeploymentJudge(WorkJudge):
        def complete(self, *_args, **_kwargs):
            return LLMResponse(success=True, provider="fixture", content=answer)
        def structured_complete(self, messages, **kwargs):
            value = json.loads(messages[-1].content)
            response = super().structured_complete(messages, **kwargs)
            data = json.loads(response.content)
            if "requirements" in data:
                row = data["requirements"][0];row["complete"] = True
                cell = row["comparison_cells"][0];cell["complete"] = True
                cell["marker_starts"] = [m for claim in value["strictly_supported_claims"]
                    if "企业快速部署" in claim["text"] for m in claim["marker_starts"]]
                cell["application_kind"] = primary_kind
                cell["application_workload_quote"] = "AgentAlpha 适合企业快速部署"
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    finding = assess_research_findings(candidate_bundle(body), c, DeploymentJudge())
    coverage = finding["coverage"]
    assert not coverage["complete"]
    assert any(g.get("entity") == "AgentAlpha" and g.get("facet") == "application" for g in coverage["gaps"])
    root = create_root(db, c["original_task"])
    digest = hashlib.sha256(answer.strip().encode()).hexdigest()
    db.add(ReportRevision(report_revision_id="deployment-report", root_run_id=root.run_id,
        content_hash=digest, final_answer_hash=digest, report_path="fixture.md", status="incomplete"))
    db.flush()
    db.add(ReportClaimOccurrence(claim_occurrence_id="deployment-claim", report_revision_id="deployment-report",
        section="answer", claim_text=answer, normalized_claim_text=answer, sentence_start=0, sentence_end=len(answer)))
    db.flush()
    final = persist_final_answer_coverage(db, root, c, coverage, {
        "report_revision": {"report_revision_id": "deployment-report", "final_answer_hash": digest},
        "citation_occurrences": [{"marker_start": m, "verdict": "supported", "claim_occurrence_id": "deployment-claim"}
            for claim in json.loads(__import__("gzip").decompress((tmp_path / "artifacts" /
                coverage["decision_audit"]["artifact_path"]).read_bytes()))["inputs"]["strictly_supported_claims"]
            for m in claim["marker_starts"]]})
    assert not final["complete"]
    work = project_work(db, root.run_id)
    assert any(w["entity"] == "AgentBeta" and w["answer_status"] == "confirmed" for w in work)
    assert all(w["answer_status"] != "confirmed" for w in work if w["entity"] == "AgentAlpha")
    from app.research.answer_coverage import _verified_audit
    assert coverage["application_review"]["decision_audit"]["decision_sha256"]
    assert _verified_audit(coverage, c, only_requirement="r", only_cell={"entity": "AgentBeta", "facet": "application"})
    coverage["application_review"]["applications"][0]["concrete_task"] = True
    assert not _verified_audit(coverage, c, only_requirement="r", only_cell={"entity": "AgentBeta", "facet": "application"})


def test_independent_application_provider_failure_stops_without_rewriting(tmp_path, monkeypatch):
    from .test_manual_run_repairs import candidate_bundle
    from app.research.findings import assess_research_findings
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract();c["evidence_focus"] = [{"entity": "AgentBeta", "facet": "application"}]
    class UnavailableReview(WorkJudge):
        def structured_complete(self, messages, **kwargs):
            if "application_candidates" in json.loads(messages[-1].content):
                return LLMResponse(success=False, provider="fixture", error_message="service request failed",
                    metadata={"error_type": "provider_unavailable", "http_status": 402})
            return super().structured_complete(messages, **kwargs)
    judge = UnavailableReview()
    body = "框架包括 AgentAlpha、AgentBeta。AgentAlpha 用于内部知识检索和文档问答。AgentBeta 用于多角色协作研发与决策支持。"
    result = assess_research_findings(candidate_bundle(body), c, judge)
    assert not result["coverage"]["complete"]
    assert result["coverage"]["provider_failure"]["http_status"] == 402
    assert len(judge.prompts) == 1


@pytest.mark.parametrize("separator,valid", [("", True), ("\n\n", False), (" **AgentBeta**：", False), (" **UnboundAgent**：", False)])
def test_explicit_paragraph_subject_binds_only_its_local_workload(tmp_path, monkeypatch, separator, valid):
    from .test_manual_run_repairs import candidate_bundle
    from app.reporting.writing_evidence import build_writing_evidence
    from app.evidence.citation_validator import validate_citations
    from app.research.answer_coverage import assess_answer_coverage, _verified_audit
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    c = contract();c["answer_scope"] = {"version": "answer-inventory-v2", "entities": ["AgentAlpha", "AgentBeta"],
        "bindings": [{"canonical_name": n, "evidence_quote": n + " is a framework."} for n in ["AgentAlpha", "AgentBeta"]]}
    answer = "**AgentAlpha**：这是一个框架 [CIT-001-01]。" + separator + "其用于客户支持 [CIT-001-01]。\n\n**AgentBeta**：AgentBeta 用于文档问答 [CIT-001-01]。"
    bundle = candidate_bundle(re.sub(r"\s*\[CIT-\d{3}-\d{2}\]", "", answer))
    writing = build_writing_evidence(bundle, c)
    class ParagraphJudge(WorkJudge):
        def structured_complete(self, messages, **kwargs):
            value = json.loads(messages[-1].content)
            response = super().structured_complete(messages, **kwargs)
            data = json.loads(response.content)
            if "requirements" in data:
                claims = value["strictly_supported_claims"]
                use = next(claim for claim in claims if "其用于客户支持" in claim["text"])
                row = data["requirements"][0];row["complete"] = True
                cell = row["comparison_cells"][0]
                cell.update(complete=True, marker_starts=use["marker_starts"], entity_bound=True,
                    application_kind="concrete_task", application_workload_quote=use["text"])
            return LLMResponse(success=True, provider="fixture", content=json.dumps(data))
    judge = ParagraphJudge()
    validation = validate_citations(answer, bundle, writing_evidence=writing, task_contract=c,
        multilingual_llm_client=judge, use_multilingual_adjudication=True)
    coverage = assess_answer_coverage(answer, c, validation, judge, provenance=bundle)
    assert coverage["complete"] is valid
    assert _verified_audit(coverage, c) is valid
