"""Combined regressions for real-shaped output and prerequisite/action control."""
import json
import re
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.budget import BudgetRuntime, planning_budget
from app.agent.research_goal import build_task_contract
from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport, _is_substantive_window_quote
from app.research.answer_coverage import assess_answer_coverage, _verified_audit
from app.research.comparison_scope import comparison_spec, grounded_focus, select_comparison_candidates, bind_comparison_acquisition
from app.research.recovery import recover_answer_evidence, acquisition_quality_gaps
from app.research.task_understanding import understand_new_task
from app.trace import store
from app.trace.logger import record_trace_event
from app.tools.base import ToolResult
from .conftest import create_root, add_web_trace, materialize_run
from .test_manual_run_repairs import Understanding, candidate_bundle


@pytest.mark.parametrize("hint,expected", [
    ("memory mechanisms", "memory"), ("framework architecture mechanisms", "framework"),
    ("evaluation mechanisms", "evaluation"), ("memory and evaluation", None)])
def test_specific_dimension_precedes_mechanism_modifier(hint, expected):
    question = "拆分对比底层原理、框架、记忆以及评测"
    focus = grounded_focus(question, {"predicate": "Compare " + hint, "entity": hint}, comparison_spec(question))
    assert focus.get("facet") == expected


@pytest.mark.parametrize("tail", ["（出处：资料作者）", "(Source: a technical guide)"])
def test_complete_quote_before_parenthetical_attribution(tail):
    quote = "该系统通过持久化日志保存每次状态变更，并且在恢复时重放日志以重建当前状态"
    assert _is_substantive_window_quote(quote, "说明。" + quote + tail)
    assert not _is_substantive_window_quote(quote[:-2], "说明。" + quote + tail)


def matrix_fixture():
    answer = "AgentAlpha stores memory in files [CIT-001-01]. AgentBeta stores memory in files [CIT-001-01]."
    markers = [m.start() for m in re.finditer(r"\[CIT-", answer)]
    task = "对比AgentAlpha和AgentBeta的记忆。"
    contract = {"original_task": task, "comparison_scope": comparison_spec(task),
        "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": task}]}
    row = {"requirement_id": "r", "complete": True, "marker_starts": markers,
        "facets": [{"kind": "memory", "complete": True, "marker_starts": markers}]}
    cells = [{"entity": name, "facet": "memory", "complete": True, "marker_starts": [marker]}
             for name, marker in zip(["AgentAlpha", "AgentBeta"], markers)]
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", "", "", 1, marker_start=m) for m in markers])
    return answer, contract, row, cells, validation


@pytest.mark.parametrize("shape", ["nested", "top_ids", "top_global", "top_keyed"])
@pytest.mark.parametrize("primary", [False, True])
def test_equivalent_matrix_shapes_keep_quality_gate_and_audit(tmp_path, monkeypatch, shape, primary):
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    answer, contract, row, cells, validation = matrix_fixture()
    payload = {"requirements": [row]}
    if shape == "nested":
        row["comparison_cells"] = cells
    elif shape == "top_ids":
        payload["comparison_cells"] = [{**c, "requirement_id": "r"} for c in cells]
    else:
        payload["comparison_cells"] = cells if shape == "top_global" else {"r": cells}
    bundle = candidate_bundle("AgentAlpha and AgentBeta store memory in files.")
    bundle["source_snapshots"][0]["metadata"]["official"] = primary
    result = assess_answer_coverage(answer, contract, validation, Understanding(payload), provenance=bundle)
    assert result["complete"] is primary
    assert _verified_audit(result, contract) is primary
    if not primary:
        assert {g["cause"] for g in result["gaps"]} == {"source_quality_missing"}
        assert {g["entity"] for g in result["gaps"]} == {"AgentAlpha", "AgentBeta"}


def test_top_matrix_wrong_requirement_identity_stays_open(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    answer, contract, row, cells, validation = matrix_fixture()
    result = assess_answer_coverage(answer, contract, validation, Understanding({"requirements": [row],
        "comparison_cells": [{**c, "requirement_id": "unrelated"} for c in cells]}), provenance=candidate_bundle("source"))
    assert not result["complete"]


def test_persisted_old_focus_upgrades_without_provider_or_id_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    task = "比较最近几款工具的原理、框架、记忆和评测"
    contract = {**build_task_contract(task), "obligation_version": "research-obligations-v2",
        "requirements": [{"requirement_id": "memory", "question_id": "q", "predicate": task}],
        "requirement_focus": {"memory": {"facet": "mechanism", "dimension": "原理"}},
        "research_terms": {"memory": "personal Agent memory"}}
    old = deepcopy(contract)
    result = understand_new_task(contract, None)
    assert result["requirement_focus"]["memory"]["facet"] == "memory"
    assert result["requirements"][0]["requirement_id"] == "memory"
    assert result["focus_upgrade_audit"] and contract == old


@pytest.mark.parametrize("cause", ["source_quality_missing", "comparison_selection_missing"])
def test_quality_and_selection_search_despite_long_related_retained_tail(db, r12_settings, tmp_path, monkeypatch, cause):
    from app.retrieval.source_view import retain_source
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    root = create_root(db)
    text = "AgentAlpha memory architecture background. " * 1000
    trace = record_trace_event(db, root.run_id, 1, "web_fetcher", "success", {}, "source", {"pages": [{
        "url": "https://example.test/blog", "content": text[:1000], "source_artifact": retain_source(text)}]})
    contract = {"obligation_version": "v2", "original_task": "比较最近几款personal Agent的记忆",
        "as_of": "2026-10-07", "comparison_scope": comparison_spec("比较最近几款personal Agent的记忆"),
        "research_terms": {"r": "personal Agent memory"}}
    plan = {"task_contract": contract, "allowed_tools": ["tavily_search", "web_fetcher"]}
    feedback = {"answer_gaps": [{"requirement_id": "r", "cause": cause, "entity": "AgentAlpha", "facet": "memory", "detail": "first wording"}]}
    called = []
    def execute(name, args, *_):
        called.append((name, args))
        return ToolResult(success=True, output={"fetch_candidates": []})
    with patch("app.research.recovery.execute_governed_operation", side_effect=execute), patch("app.research.recovery.materialize_execution_provenance"):
        for detail in ["first wording", "completely different paraphrase", "third explanation"]:
            feedback["answer_gaps"][0]["detail"] = detail
            assert not recover_answer_evidence(db, root.run_id, plan, r12_settings, feedback, traces=[trace])
    assert len(called) == 2 and all(name == "tavily_search" for name, _ in called)
    assert len({a["repair_key"] for a in plan["answer_recovery"]["attempts"]}) == 1
    if cause == "comparison_selection_missing":
        assert "2026" in called[0][1]["query"] and "ranking" in called[0][1]["query"]


def test_recovery_preserves_finalization_handoff_without_fabricating_progress(db, r12_settings):
    from app.agent.budget import FinalizationRequired
    root = create_root(db)
    plan = {"task_contract": {"obligation_version": "v2", "research_terms": {"r": "AgentAlpha memory"}},
            "allowed_tools": ["tavily_search", "web_fetcher"]}
    with patch("app.research.recovery.execute_governed_operation", side_effect=FinalizationRequired("tokens")), \
         patch("app.research.recovery.materialize_execution_provenance"):
        assert not recover_answer_evidence(db, root.run_id, plan, r12_settings,
            {"answer_gaps": [{"requirement_id": "r", "cause": "source_quality_missing"}]})
    assert plan["answer_recovery"]["stop_reason"] == "protected_finalization_reserve"
    assert plan["answer_recovery"]["attempts"][0]["new_body_units"] == 0


@pytest.mark.parametrize("elapsed,expected", [(5, 15), (20, None)])
def test_pdf_fallback_uses_remaining_transport_deadline(monkeypatch, elapsed, expected):
    from app.retrieval.router import RetrievalRouter
    from app.retrieval.contracts import FetchRequest, FetchResult, FetchFailureCode, FetchStatus
    from app.retrieval.classifier import make_failure
    now = iter([0, 0, elapsed])
    monkeypatch.setattr("app.retrieval.router.time.monotonic", lambda: next(now))
    captured = []
    class Http:
        def fetch(self, request):
            return FetchResult(requested_url=request.url, failure=make_failure(FetchFailureCode.PDF_ROUTED, "PDF detected"))
    class Pdf:
        def fetch(self, request):
            captured.append(request.timeout_seconds)
            return FetchResult(requested_url=request.url, fetch_status=FetchStatus.SUCCESS, content="PDF body")
    result = RetrievalRouter(http_backend=Http(), pdf_backend=Pdf()).fetch(FetchRequest(url="https://example.com/download", timeout_seconds=20))
    assert captured == ([] if expected is None else [expected])
    assert result.usable is (expected is not None)


def test_repeat_body_is_not_gain_and_new_body_is_gain(db, r12_settings):
    root = create_root(db)
    body = "AgentAlpha memory is persisted through append-only logs and replay."
    trace = add_web_trace(db, root.run_id, body, "same")
    plan = {"task_contract": {"obligation_version": "v2", "research_terms": {"r": "AgentAlpha memory"}}, "allowed_tools": ["tavily_search", "web_fetcher"]}
    def execute(name, args, *_):
        if name == "tavily_search":
            return ToolResult(success=True, output={"fetch_candidates": ["https://example.com/new"]})
        return ToolResult(success=True, output={"pages": [{"url": "https://example.com/new", "content": body}]})
    with patch("app.research.recovery.execute_governed_operation", side_effect=execute), patch("app.research.recovery.materialize_execution_provenance"):
        assert recover_answer_evidence(db, root.run_id, plan, r12_settings, {"answer_gaps": [{"requirement_id": "r", "cause": "source_quality_missing"}]}, traces=[trace])
        assert not recover_answer_evidence(db, root.run_id, plan, r12_settings, {"answer_gaps": [{"requirement_id": "r", "cause": "source_quality_missing"}]})


def test_deferred_batch_url_remains_pending_and_is_not_an_actual_attempt():
    from app.agent.quick_refetch import select_pending_candidates
    from app.agent.source_context import build_source_context
    search = SimpleNamespace(trace_id="s", run_id="r", status="success", tool_name="tavily_search", input_json="{}",
        output_json=json.dumps({"results": [{"url": "https://example.test/second"}], "fetch_candidates": ["https://example.test/second"]}))
    fetch = SimpleNamespace(trace_id="f", run_id="r", status="failed", tool_name="web_fetcher",
        input_json=json.dumps({"urls": ["https://example.test/second"]}),
        output_json=json.dumps({"pages": [{"url": "https://example.test/second", "error_code": "batch_deadline_exceeded"}]}))
    assert select_pending_candidates([search, fetch], max_total_candidates=1) == ["https://example.test/second"]
    source = build_source_context([search, fetch])["sources"][0]
    assert source["fetch_status"] == "pending" and source["fetch_attempts"] == 0


def test_spent_finalization_reserve_is_not_protected_twice(db, r12_settings):
    from app.agent.budget import _final_report
    root = create_root(db)
    runtime = BudgetRuntime(db, root.run_id, r12_settings.model_copy(update={"research_max_llm_calls": 30}))
    runtime.reserve(llm=15)
    token = _final_report.set(True)
    try:
        runtime.reserve(llm=5, tokens=6000)
    finally:
        _final_report.reset(token)
    assert runtime.can_deepen(required_llm_calls=2)
    assert not runtime.can_deepen(required_llm_calls=3)
    assert runtime.snapshot()["limits"]["final_report_spent_llm_calls"] == 5


def test_final_selection_uses_root_reserve_but_keeps_acquisition_and_hard_caps(db, r12_settings):
    from app.agent.budget import _active, finalization_budget, acquisition_budget, FinalizationRequired, BudgetExceeded
    root = create_root(db)
    runtime = BudgetRuntime(db, root.run_id, r12_settings.model_copy(update={"research_max_llm_calls": 30}))
    runtime.reserve(llm=20)
    token = _active.set(runtime)
    try:
        with pytest.raises(FinalizationRequired):
            runtime.reserve(llm=1)
        with finalization_budget():
            runtime.reserve(llm=1, tokens=5000)
            with acquisition_budget(), pytest.raises(FinalizationRequired):
                runtime.reserve(llm=1)
        assert runtime.snapshot()["limits"]["final_report_spent_llm_calls"] == 1
        with finalization_budget(), pytest.raises(BudgetExceeded) as failure:
            runtime.reserve(llm=10)
        assert failure.value.reason == "llm_calls"
    finally:
        _active.reset(token)


def test_recent_query_policy_preserves_explicit_year_and_governs_actor_query(r12_settings):
    from app.agent.source_intake import prepare_tool_arguments
    contract = {"obligation_version": "v2", "as_of": "2026-10-07", "original_task": "比较最近几款工具", "comparison_scope": {"recent": True}}
    plan = {"task_contract": contract}
    query = prepare_tool_arguments("tavily_search", {"query": "personal tools ranking 2025"}, plan, r12_settings)["query"]
    assert "2026" in query and "2025" not in query
    contract["original_task"] = "比较2025年的最近几款工具"
    query = prepare_tool_arguments("tavily_search", {"query": "personal tools ranking 2026"}, plan, r12_settings)["query"]
    assert "2025" in query and "2026" not in query


def test_quick_plan_targets_each_product_before_writing():
    plan = {"research_mode": "quick", "allowed_tools": ["tavily_search", "web_fetcher", "report_writer"],
        "task_contract": {"obligation_version": "v2", "comparison_scope": comparison_spec("对比AgentAlpha和AgentBeta的原理。")},
        "steps": [{"tool_name": "tavily_search"}, {"tool_name": "report_writer"}]}
    bind_comparison_acquisition(plan)
    assert [s["tool_name"] for s in plan["steps"]] == ["tavily_search", "web_fetcher", "tavily_search", "web_fetcher", "report_writer"]
    assert "AgentAlpha" in plan["steps"][0]["arguments"]["query"]
    assert "AgentBeta" in plan["steps"][2]["arguments"]["query"]
    assert plan["steps"][3]["arguments_from"]["step_no"] == 3


def test_fixed_cohort_branch_projects_one_product_without_changing_root():
    from app.research.node_executor import _node_task_contract
    from app.research.orchestrator import _comparison_gap_branches
    contract = {"obligation_version": "v2", "original_task": "比较记忆", "comparison_scope": {"entities": ["AgentAlpha", "AgentBeta"], "dimensions": {"memory": "记忆"}},
        "requirements": [{"requirement_id": "m", "question_id": "q", "predicate": "比较记忆"}], "requirement_focus": {"m": {"facet": "memory"}}}
    branches = _comparison_gap_branches(contract, {"coverage": {"complete": False}}, [], 3, 2)["branches"]
    assert len(branches) == 2
    node = SimpleNamespace(query=branches[0]["query"], research_goal="memory", metadata_json=json.dumps(branches[0]))
    child = _node_task_contract(contract, node)
    assert child["comparison_scope"]["entities"] == ["AgentAlpha"]
    assert child["requirement_focus"]["m"]["entity"] == "AgentAlpha"
    assert contract["comparison_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
    node.metadata_json = json.dumps({**branches[0], "comparison_entities": ["InventedProduct"]})
    with pytest.raises(ValueError):
        _node_task_contract(contract, node)


def test_selection_projection_has_a_real_bound_and_retains_application_reason(tmp_path, monkeypatch):
    from app.agent.budget import estimate_text_tokens
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    bundle = {key: [] for key in ("citations", "passages", "source_snapshots", "source_documents")}
    for i in range(60):
        part = candidate_bundle("Memory frameworks implement caching and store research notes. " * 400)
        for key, id_field in [("citations", "citation_label"), ("passages", "passage_id"), ("source_snapshots", "snapshot_id"), ("source_documents", "document_id")]:
            for row in part[key]:
                for field in ("passage_id", "snapshot_id", "document_id"):
                    if field in row:
                        row[field] += str(i)
                if id_field == "citation_label":
                    row[id_field] = f"CIT-{i + 1:03}-01"
                bundle[key].append(row)
    contract = {"obligation_version": "v2", "original_task": "比较最近几款personal Agent的记忆和框架", "as_of": "2026-10-07",
                "comparison_scope": comparison_spec("比较最近几款personal Agent的记忆和框架")}
    class Client(Understanding):
        def structured_complete(self, messages, **kwargs):
            inputs = json.loads(messages[-1].content)
            assert estimate_text_tokens(json.dumps(inputs["evidence"], ensure_ascii=False)) <= 6200
            return super().structured_complete(messages, **kwargs)
    result = select_comparison_candidates(contract, bundle, Client({"candidates": []}))
    assert result["comparison_scope"]["selection_attempt"]["rejections"] == [{"cause": "comparable_cohort_missing"}]
    assert result["comparison_scope"]["selection_attempt"]["decision_audit"]["redaction_changed"] is False


def test_same_report_continues_only_when_research_state_changes():
    from app.reporting.revision_pipeline import generate_validate_revise
    calls = []
    validations = iter([False, False, True])
    state = iter(["first", "new-source", "new-source"])
    result = generate_validate_revise({}, lambda _: calls.append(1) or "same report", lambda *_: "id", lambda: None,
        validate=lambda *_: {"ok": next(validations)}, is_acceptable=lambda v: v["ok"], revision_feedback=lambda _: {},
        max_revisions=2, progress_fingerprint=lambda: next(state))
    assert result.adopted and len(calls) == 3


@pytest.mark.parametrize("protected_boundary", [False, True])
def test_deep_selects_cohort_before_product_nodes_and_passes_current_contract(db, r12_settings, tmp_path, monkeypatch, protected_boundary):
    from app.research.orchestrator import run_deep_research_v2
    from app.research.orchestrator import _refresh_comparison_scope as real_refresh
    from app.agent.budget import FinalizationRequired
    from app.research.node_executor import ResearchNodeExecutor
    from app.research.scope import resolve_research_scope, list_scope_nodes
    from tests.support.fake_react_llm import FakeReActLLMClient
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    task = "Compare several recent personal assistants in memory."
    root = create_root(db, task)
    contract = {**build_task_contract(task), "obligation_version": "v2", "as_of": "2026-10-07",
        "comparison_scope": comparison_spec(task), "requirement_focus": {"s": {"facet": "selection"}, "m": {"facet": "memory"}},
        "research_terms": {"s": "personal assistant", "m": "memory"},
        "requirements": [{"requirement_id": rid, "question_id": "q", "predicate": task} for rid in ["s", "m"]]}
    plan = json.loads(root.plan_json)
    plan["task_contract"] = contract
    plan["allowed_tools"] = ["tavily_search", "web_fetcher"]
    store.replace_agent_run_plan(db, root.run_id, plan)
    category = "AgentAlpha and AgentBeta are personal assistant products that execute tasks."
    activity = "In 2026, AgentAlpha and AgentBeta rank among tested personal assistants with active users."
    order = []
    class Client(FakeReActLLMClient):
        def complete(self, messages, **kwargs):
            inputs = json.loads(messages[-1].content)
            units = inputs["evidence"]["factual_units"]
            cohort = next((u for u in units if category in u["text"] and activity in u["text"]), None)
            payload = {"candidates": [], "criteria": "Dated comparative assessment", "time_scope": "2026"}
            if cohort:
                payload["candidates"] = [{"name": name, "citation": cohort["citation_id"], "evidence_quote": category,
                    "selection_quote": activity} for name in ["AgentAlpha", "AgentBeta"]]
                order.append("cohort-frozen")
            from app.llm.base import LLMResponse
            return LLMResponse(success=True, provider="fixture", content=json.dumps(payload))
    def runner(session, run_id, settings, client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        child_plan = json.loads(run.plan_json)
        if run_id == root.run_id:
            text, suffix = "Personal assistant research needs product-specific memory sources.", "root"
        elif child_plan.get("acquisition_stage") == "comparison_selection":
            order.append("selection-node")
            assert not child_plan["task_contract"]["comparison_scope"]["entities"]
            text, suffix = category + " " + activity, "cohort"
        else:
            members = child_plan["task_contract"]["comparison_scope"]["entities"]
            assert len(members) == 1 and "cohort-frozen" in order
            order.append(members[0])
            text, suffix = members[0] + " persists memory by appending state to a journal.", members[0]
        add_web_trace(session, run_id, text, suffix)
        materialize_run(session, run, settings)
        if run_id != root.run_id:
            status = "incomplete" if child_plan.get("acquisition_stage") else "completed"
            store.update_agent_run_status(session, run_id, status, None)
            return {"run_id": run_id, "status": status}
        return {"run_id": run_id, "status": "running"}
    def findings(bundle, contract, client, *, previous=None):
        complete = all(member in order for member in ["AgentAlpha", "AgentBeta"])
        gaps = ([{"requirement_id": "s", "cause": "comparison_selection_missing"}]
                if not contract["comparison_scope"]["entities"] else
                [{"requirement_id": "m", "entity": n, "facet": "memory", "cause": "source_quality_missing"}
                 for n in ["AgentAlpha", "AgentBeta"] if n not in order])
        return {"markdown": "", "decision_audit": None, "coverage": {"complete": complete,
            "requirements": [], "gaps": gaps}}
    def acquire(name, arguments, *_args):
        if name == "tavily_search":
            return ToolResult(success=True, output={"fetch_candidates": ["https://example.com/cohort"]})
        return ToolResult(success=True, output={"pages": [{"url": "https://example.com/cohort",
            "content": category + " " + activity, "content_basis": "full_text", "fetch_status": "success"}]})
    captured = []
    refresh_calls = []
    def refresh(*args):
        refresh_calls.append(1)
        if protected_boundary and len(refresh_calls) == 2:
            raise FinalizationRequired("llm_calls")
        return real_refresh(*args)
    def report(run, plan, observations, traces, **kwargs):
        captured.append(deepcopy(plan["task_contract"]))
        bundle = kwargs["provenance_bundle"]
        passages = {p["passage_id"]: p for p in bundle["passages"]}
        cited = [f"{passages[c['passage_id']]['text']} [{c['citation_label']}]" for c in bundle["citations"]
                 if "persists memory" in passages[c["passage_id"]]["text"]]
        return "# Report\n\n## 3. 最终回答\n\n" + "\n\n".join(cited)
    with patch("app.research.orchestrator.run_react_task", side_effect=runner), \
         patch("app.research.orchestrator._refresh_comparison_scope", side_effect=refresh), \
         patch("app.research.recovery.execute_governed_operation", side_effect=acquire), \
         patch("app.research.findings.assess_research_findings", side_effect=findings), \
         patch("app.research.orchestrator.save_report", return_value=str(tmp_path / "report.md")):
        run_deep_research_v2(db, root.run_id, r12_settings, Client([]), node_executor=ResearchNodeExecutor(runner), report_generator=report)
    if protected_boundary:
        assert len(refresh_calls) == 2 and not order
        assert captured and not captured[0]["comparison_scope"]["entities"]
        assert store.get_fresh_agent_run(db, root.run_id).status == "incomplete"
        return
    assert order == ["cohort-frozen", "AgentAlpha", "AgentBeta"]
    from app.research.models import ResearchOperation
    from sqlalchemy import select
    assert db.scalars(select(ResearchOperation).where(ResearchOperation.root_run_id == root.run_id)).first()
    assert captured and captured[0]["comparison_scope"]["entities"] == ["AgentAlpha", "AgentBeta"]
    nodes = list_scope_nodes(db, resolve_research_scope(db, root.run_id).scope_id)
    assert all(node.depth == 1 for node in nodes if node.parent_node_id)


def test_report_recovery_rebinds_writer_and_validator_contract(tmp_path, monkeypatch):
    from app.agent.reporter import generate_markdown_report
    from app.llm.base import LLMResponse
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    answer, contract, row, cells, validation = matrix_fixture()
    contract["obligation_version"] = "v2"
    old = {**contract, "comparison_scope": {**contract["comparison_scope"], "entities": [], "selection_required": True}}
    plan = {"task_contract": old, "source_mode": "real"}
    seen = []
    bundle = candidate_bundle("AgentAlpha and AgentBeta store memory in files.")
    run = SimpleNamespace(run_id="fixture", task=contract["original_task"], source_mode="real", status="running",
                          report_type="summary", current_step=1, total_steps=1, total_tool_calls=1, created_at=None)
    def synth(task, observations, client, provenance, current, *args, **kwargs):
        seen.append(("writer", current["comparison_scope"]["entities"]))
        return answer
    def coverage(answer, current, report, client, **kwargs):
        seen.append(("coverage", current["comparison_scope"]["entities"]))
        return {"complete": bool(current["comparison_scope"]["entities"]), "answer_sha256": "hash", "gaps": [
            {"requirement_id": "r", "cause": "comparison_selection_missing"}] if not current["comparison_scope"]["entities"] else []}
    def refresh(feedback):
        plan["task_contract"] = contract
        return bundle
    with patch("app.agent.reporter._llm_synthesize_answer", side_effect=synth), \
         patch("app.evidence.citation_validator.validate_citations", return_value=validation), \
         patch("app.research.answer_coverage.assess_answer_coverage", side_effect=coverage):
        generate_markdown_report(run, plan, [], [], llm_client=Understanding({}), provenance_bundle=bundle,
                                 evidence_refresh_callback=refresh)
    assert seen[:4] == [("writer", []), ("coverage", []),
        ("writer", ["AgentAlpha", "AgentBeta"]), ("coverage", ["AgentAlpha", "AgentBeta"])]
