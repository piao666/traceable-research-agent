from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport, validate_citations
from app.llm.base import LLMResponse
from app.reporting.writing_evidence import build_writing_evidence
from app.research.answer_coverage import assess_answer_coverage, persist_final_answer_coverage
from app.research.task_understanding import understand_new_task
from app.reporting.revision_pipeline import generate_validate_revise
from .conftest import create_root, db
from .test_writing_evidence import _bundle, _MultilingualJudge


def _contract():
    return {"original_task": "Explain concurrency and network filesystem limitations.",
        "obligation_version": "research-obligations-v1", "output_constraints": {"language": "en"},
        "questions": [{"question_id": "q1", "text": "concurrency", "requirement_ids": ["r1"]},
                      {"question_id": "q2", "text": "network filesystem limitations", "requirement_ids": ["r2"]}],
        "requirements": [{"requirement_id": "r1", "question_id": "q1", "predicate": "Explain concurrency"},
                         {"requirement_id": "r2", "question_id": "q2", "predicate": "Explain network filesystem limitations"}]}


class CoverageJudge:
    calls = 0
    def is_available(self):
        return True
    def structured_complete(self, messages, **kwargs):
        self.calls += 1
        facts = json.loads(messages[-1].content)["strictly_supported_claims"]
        text = " ".join(f["text"] for f in facts)
        return LLMResponse(success=True, provider="fixture", model="coverage",
            content=json.dumps({"requirements": [
                {"requirement_id": "r1", "complete": True, "marker_starts": [m for f in facts for m in f["marker_starts"]], "reason": "concurrency answered", "facets": [{"kind": "limitations", "complete": True, "marker_starts": [m for f in facts for m in f["marker_starts"]]}]},
                {"requirement_id": "r2", "complete": "shared memory" in text,
                 "marker_starts": [m for f in facts for m in f["marker_starts"]], "reason": "requires shared memory answer", "facets": [{"kind": "limitations", "complete": "shared memory" in text, "marker_starts": [m for f in facts for m in f["marker_starts"]]}]}]}))


def test_deleting_required_answer_reopens_gap_and_invalidates_cache():
    judge, cache = CoverageJudge(), {}
    full = "Readers run concurrently [CIT-001-01]. A network filesystem cannot provide shared memory [CIT-002-01]."
    def validate(text):
        import re
        return CitationValidationReport(details=[CitationValidationDetail(m.group(0), "supported", text, "fact", 1,
            marker_start=m.start(), marker_end=m.end()) for m in re.finditer(r"CIT-\d{3}-\d{2}", text)])
    first = assess_answer_coverage(full, _contract(), validate(full), judge, cache=cache)
    assert first["complete"]
    deleted = "Readers run concurrently [CIT-001-01].\n\n## Network limitations"
    second = assess_answer_coverage(deleted, _contract(), validate(deleted), judge, cache=cache)
    assert not second["complete"]
    assert second["gaps"][0]["requirement_id"] == "r2"
    assert judge.calls == 2


def test_weak_claims_cannot_discharge_required_answer():
    text = "Readers run concurrently [CIT-001-01]."
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "weakly_supported", text, "fact", .3, marker_start=25)])
    judge = CoverageJudge()
    result = assess_answer_coverage(text, _contract(), validation, judge)
    assert not result["complete"] and len(result["gaps"]) == 2
    assert judge.calls == 0


def test_same_language_lexical_overlap_needs_semantic_entailment():
    bundle = _bundle()
    fact = "The official body says the feature is stable."
    writing = build_writing_evidence(bundle)
    answer = fact + " [CIT-001-02]"
    result = validate_citations(answer, bundle, writing_evidence=writing,
        task_contract=_contract(), multilingual_llm_client=_MultilingualJudge(verdict="unsupported"))
    assert result.supported == 0 and result.unsupported == 1


def test_conditional_checkpoint_claim_cannot_drop_parameter_even_if_judge_accepts():
    bundle = _bundle()
    fact = "If synchronous=NORMAL is configured, commits omit synchronization. The application therefore avoids synchronization when checkpoint runs separately."
    bundle["passages"][1]["text"] = fact
    quote = "The application therefore avoids synchronization when checkpoint runs separately."
    result = validate_citations("The application avoids synchronization when checkpoint runs separately [CIT-001-02].",
        bundle, writing_evidence=build_writing_evidence(bundle), task_contract=_contract(),
        multilingual_llm_client=_MultilingualJudge(quote=quote))
    assert result.supported == 0
    assert result.unsupported == 1


def test_complete_acquired_source_tail_survives_short_view_and_continuation(tmp_path, monkeypatch):
    from app.retrieval.source_view import retain_source
    from app.agent.source_context import resolve_source_snapshot
    from app.tools.source_snapshot import read_snapshot
    from app.agent.evidence import _items_from_record
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    body = ("Acquired source paragraph with body evidence.\n\n" * 650) + "SQLITE_BUSY can occur during recovery of the WAL file."
    ref = retain_source(body)
    page = {"url": "https://sqlite.org/wal.html", "content": body[:8000], "source_artifact": ref, "content_basis": "partial"}
    trace = SimpleNamespace(status="success", tool_name="web_fetcher", trace_id="original", output_json=json.dumps({"pages": [page]}))
    source_id = "S" + hashlib.sha256(page["url"].encode()).hexdigest()[:12]
    snapshot = resolve_source_snapshot([trace], source_id)
    assert snapshot.text == body
    result = read_snapshot({"_source_snapshot": snapshot, "offset": len(body)-100, "max_chars": 100})
    record = {"tool_name": "web_fetcher", "success": True, "status": "success", "trace_id": "continuation", "output": result.output}
    items = _items_from_record("run", record, 0)
    assert items and "SQLITE_BUSY" in items[0].snippet
    assert items[0].trace_id == "original"
    assert items[0].metadata["fragment_locator"]["char_end"] == len(body)
    fetched = _items_from_record("run", {**record, "trace_id": "original", "output": {"pages": [page]}}, 0)
    assert any("SQLITE_BUSY" in item.snippet for item in fetched)
    assert all(item.metadata["content_basis"] == "full_text" for item in fetched)
    assert _items_from_record("run", record, 999)[0].evidence_id == items[0].evidence_id


def test_model_omission_keeps_original_aggregate_obligation():
    class UnderstandingClient:
        def structured_complete(self, messages, **kwargs):
            return LLMResponse(success=True, provider="fixture", content=json.dumps({
                "questions": [{"question_id": "q1", "text": "concurrency", "requirement_ids": ["r1"]}],
                "requirements": [{"requirement_id": "r1", "question_id": "q1", "predicate": "Explain concurrency"}]}))
    result = understand_new_task({"original_task": _contract()["original_task"]}, UnderstandingClient())
    assert {r["requirement_id"] for r in result["requirements"]} == {"r1", "req-original"}
    assert result["requirements"][-1]["predicate"] == _contract()["original_task"]


def test_presentation_clause_remains_an_instruction_without_source_obligation():
    original = "解释 GET 与 POST 的幂等性。逐项给出正文引用，用中文回答。"
    class UnderstandingClient:
        def structured_complete(self, messages, **kwargs):
            return LLMResponse(success=True, provider="fixture", content=json.dumps({
                "questions": [
                    {"question_id": "q1", "text": "解释 GET 与 POST 的幂等性", "requirement_ids": ["r1"]},
                    {"question_id": "q2", "text": "逐项给出正文引用，用中文回答", "requirement_ids": ["r2"]},
                ],
                "requirements": [
                    {"requirement_id": "r1", "question_id": "q1", "predicate": "GET POST idempotency"},
                    {"requirement_id": "r2", "question_id": "q2", "predicate": "Chinese answer"},
                ],
            }))
    contract = understand_new_task({"original_task": original}, UnderstandingClient())
    assert {r["requirement_id"] for r in contract["requirements"]} == {"r1", "req-original"}
    assert contract["presentation_instructions"] == ["逐项给出正文引用，用中文回答"]
    assert contract["original_task"] == original


def test_output_facets_are_not_separate_source_questions():
    from app.research.task_understanding import _is_presentation_only
    assert _is_presentation_only("安全性和幂等性分别回答")
    assert _is_presentation_only("说明机制、适用条件和限制")
    assert not _is_presentation_only("勿把幂等理解为多次请求必然得到相同响应")
    assert not _is_presentation_only("解释 HTTP GET 的幂等性")


def test_context_configuration_and_exact_conversion_are_mechanisms():
    from app.research.answer_coverage import _missing_facets
    claims = [
        {"marker_starts": [10], "text": "prec 用于设置算术运算的精度 [CIT-001-01]。"},
        {"marker_starts": [20], "text": "从 float 构造时执行精确转换 [CIT-002-01]。"},
    ]
    row = {"marker_starts": [10, 20], "facets": [
        {"kind": "mechanism", "complete": True, "marker_starts": [10, 20]},
    ]}
    assert _missing_facets(row, ["mechanism"], claims) == []


def test_one_sided_comparison_cannot_pass_even_when_model_calls_it_complete():
    import re

    class AlwaysCompleteJudge:
        def is_available(self):
            return True

        def structured_complete(self, messages, **kwargs):
            inputs = json.loads(messages[-1].content)
            markers = [m for c in inputs["strictly_supported_claims"] for m in c["marker_starts"]]
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"requirements": [{
                "requirement_id": "r1", "complete": True, "marker_starts": markers,
                "reason": "Both alternatives answered.",
                "facets": [{"kind": kind, "complete": True, "marker_starts": markers}
                           for kind in inputs["required_facets"]["r1"]],
            }]}))

    cases = [
        ("Decimal 从字符串与从 float 构造为何可能不同",
         "## 字符串与 float\n从 float 构造执行精确转换 [CIT-001-01]。",
         "从字符串构造由输入数字决定保存的数值 [CIT-002-01]。", "字符串"),
        ("Explain the difference between files and streams.",
         "## Files and streams\nStreams transfer data incrementally [CIT-001-01].",
         "Files retain data on storage [CIT-002-01].", "files"),
    ]
    for predicate, partial, missing_answer, missing_member in cases:
        contract = {"original_task": predicate, "questions": [{"question_id": "q1", "text": predicate}],
                    "requirements": [{"requirement_id": "r1", "question_id": "q1", "predicate": predicate}]}

        def validate(text):
            return CitationValidationReport(details=[CitationValidationDetail(
                m.group(0), "supported", text, "verified source", 1,
                marker_start=m.start(), marker_end=m.end(),
            ) for m in re.finditer(r"CIT-\d{3}-\d{2}", text)])

        rejected = assess_answer_coverage(partial, contract, validate(partial), AlwaysCompleteJudge())
        assert not rejected["complete"]
        assert missing_member in rejected["requirements"][0]["reason"]
        full = partial + "\n" + missing_answer
        assert assess_answer_coverage(full, contract, validate(full), AlwaysCompleteJudge())["complete"]


def test_comparison_accepts_standard_type_translation_in_supported_claims():
    from app.research.answer_coverage import _missing_comparison_members
    predicate = "Decimal 从字符串与从 float 构造为何可能不同"
    row = {"marker_starts": [10, 20]}
    claims = [{"text": "从字符串构造保存输入数字。", "marker_starts": [10]},
              {"text": "从浮点数构造执行精确转换。", "marker_starts": [20]}]
    assert _missing_comparison_members(row, predicate, claims) == []
    assert _missing_comparison_members(row, predicate, claims[:1]) == ["float"]


def test_findings_rewrite_is_bounded_and_requires_retained_body_evidence(tmp_path, monkeypatch):
    import app.research.findings as findings

    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(findings, "build_writing_evidence", lambda *_: SimpleNamespace(prompt_payload=lambda: {}, factual_units=()))
    monkeypatch.setattr(findings, "validate_citations", lambda *_a, **_kw: CitationValidationReport())
    monkeypatch.setattr(findings, "assess_answer_coverage", lambda text, *_a, **_kw: {
        "complete": text == "repaired", "gaps": [] if text == "repaired" else [{"requirement_id": "r1"}],
    })

    class Client:
        def __init__(self):
            self.inputs = []

        def complete(self, messages, **kwargs):
            self.inputs.append(json.loads(messages[-1].content))
            return LLMResponse(success=True, provider="fixture",
                               content="partial" if len(self.inputs) == 1 else "repaired")

    for ready, expected_attempts in [(False, 1), (True, 2)]:
        monkeypatch.setattr(findings, "assess_required_evidence", lambda *_a: SimpleNamespace(passed=ready))
        client = Client()
        result = findings.assess_research_findings({}, _contract(), client)
        assert len(result["attempts"]) == len(client.inputs) == expected_attempts
        assert result["coverage"]["complete"] is ready
        if ready:
            assert client.inputs[1]["revision_feedback"]["answer_gaps"] == [{"requirement_id": "r1"}]
            assert client.inputs[1]["revision_feedback"]["previous_findings"] == "partial"


def test_candidate_and_validation_are_separate_append_events():
    retained, judged = [], []
    result = generate_validate_revise({}, lambda ctx: "candidate", lambda *args: retained.append(args) or "candidate-id",
        lambda: None, validate=lambda *args: {"supported_occurrences": 1}, is_acceptable=lambda value: True,
        revision_feedback=lambda value: {}, persist_validation=lambda *args: judged.append(args))
    assert result.adopted
    assert len(retained) == 1 and retained[0][2]["code"] == "candidate_retained"
    assert judged[0][1] == "candidate-id" and judged[0][2]["code"] == "accepted"


def test_terminal_rejects_coverage_from_different_answer_bytes(db):
    root = create_root(db)
    coverage = {"complete": True, "answer_sha256": "stale", "decision_audit": {"decision_sha256": "audit"},
        "requirements": [{"requirement_id": "r1", "answer_status": "answered", "marker_starts": [10]},
                         {"requirement_id": "r2", "answer_status": "answered", "marker_starts": [10]}]}
    occurrences = {"report_revision": {"final_answer_hash": "current"}, "citation_occurrences": [
        {"marker_start": 10, "verdict": "supported", "claim_occurrence_id": "claim"}]}
    result = persist_final_answer_coverage(db, root, _contract(), coverage, occurrences)
    assert not result["complete"] and len(result["gaps"]) == 2
    assert result["snapshot_id"]


def test_conflicting_duplicate_semantic_verdicts_fail_closed():
    class DuplicateJudge(_MultilingualJudge):
        def structured_complete(self, messages, **kwargs):
            response = super().structured_complete(messages, **kwargs)
            payload = json.loads(response.content)
            payload["verdicts"].append({**payload["verdicts"][0], "verdict": "unsupported"})
            response.content = json.dumps(payload)
            return response
    bundle = _bundle()
    result = validate_citations("The official body says the feature is stable [CIT-001-02].", bundle,
        writing_evidence=build_writing_evidence(bundle), task_contract=_contract(),
        multilingual_llm_client=DuplicateJudge())
    assert result.supported == 0 and result.unsupported == 1


def test_coverage_requires_independence_and_body_basis():
    from app.research.answer_coverage import _evidence_check
    requirement = {"acceptable_content_basis": ["full_text"], "min_independent_sources": 2, "min_reliability": 0}
    sources = {"10": [{"passage_id": "p1", "content_basis": "full_text", "independence_group": "a"}],
               "20": [{"passage_id": "p2", "content_basis": "full_text", "independence_group": "a"}],
               "30": [{"passage_id": "p3", "content_basis": "snippet_only", "independence_group": "b"}]}
    result = _evidence_check(requirement, [10, 20, 30], sources)
    assert result["independent_sources"] == 1 and result["gaps"] == ["missing_independence"]
    sources["30"][0]["content_basis"] = "full_text"
    assert not _evidence_check(requirement, [10, 30], sources)["gaps"]


def test_task_understanding_unavailable_still_requires_original_answer():
    result = understand_new_task({"original_task": "Explain mechanisms and limitations."}, None)
    assert result["obligation_version"]
    assert result["requirements"][0]["predicate"] == result["original_task"]
    assert result["understanding_fallback"]


def test_tampered_coverage_audit_cannot_be_adopted(tmp_path, monkeypatch):
    from app.research.answer_coverage import _verified_audit
    from app.evidence.decision_audit import retain_decision
    from app.research.contracts import normalize_requirements
    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    contract = {**_contract(), "original_task": "Explain features."}
    requirements = [r.model_dump(mode="json") for r in normalize_requirements(contract)]
    rows = [{"requirement_id": r["requirement_id"], "complete": True, "marker_starts": [10]} for r in requirements]
    sources = {"10": [{"passage_id": "p", "content_basis": "partial", "independence_group": "a"}]}
    ref = retain_decision("answer_coverage_decision", {"answer_sha256": "current", "requirements": requirements,
        "evidence_sources": sources}, {"success": True, "content": json.dumps({"requirements": rows})})
    coverage = {"answer_sha256": "current", "decision_audit": ref, "requirements": rows}
    assert _verified_audit(coverage, contract)
    coverage["requirements"] = [{**r, "marker_starts": [20]} for r in rows]
    assert not _verified_audit(coverage, contract)


def test_findings_seed_requires_audited_same_answer_and_contract(tmp_path, monkeypatch):
    from app.agent.reporter import _audited_findings_candidate
    from app.evidence.decision_audit import retain_decision
    from app.research.contracts import normalize_requirements

    monkeypatch.setenv("EVIDENCE_ARTIFACT_ROOT", str(tmp_path))
    contract = {**_contract(), "original_task": "Explain features."}
    requirements = [r.model_dump(mode="json") for r in normalize_requirements(contract)]
    text = "The feature is stable [CIT-001-01]."
    digest = hashlib.sha256(text.encode()).hexdigest()
    rows = [{"requirement_id": r["requirement_id"], "complete": True, "marker_starts": [10]} for r in requirements]
    sources = {"10": [{"passage_id": "p", "content_basis": "partial", "independence_group": "a"}]}
    coverage_ref = retain_decision("answer_coverage_decision", {
        "answer_sha256": digest, "requirements": requirements, "evidence_sources": sources,
    }, {"success": True, "content": json.dumps({"requirements": rows})})
    finding_ref = retain_decision("research_findings_decision", {}, {"success": True, "content": text})
    finding = {"markdown": text, "decision_audit": finding_ref, "coverage": {
        "complete": True, "answer_sha256": digest, "decision_audit": coverage_ref, "requirements": rows,
    }}
    assert _audited_findings_candidate({"research_findings": [finding]}, contract) == (text, finding_ref)
    assert _audited_findings_candidate({"research_findings": [{**finding, "markdown": text + " altered"}]}, contract) is None
    other = {**contract, "requirements": [{**r, "predicate": "different question"} for r in contract["requirements"]]}
    assert _audited_findings_candidate({"research_findings": [finding]}, other) is None


def test_contract_projection_inserts_in_foreign_key_order(db):
    from sqlalchemy import text
    from app.research.assessor import persist_plan_contract
    from app.research.models import EvidenceRequirement, ResearchQuestion
    root = create_root(db)
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    revision = persist_plan_contract(db, root_run_id=root.run_id, contract=_contract())
    db.commit()
    assert revision.revision_id
    assert db.execute(text("PRAGMA foreign_key_check")).all() == []


def test_model_cannot_turn_a_guessed_answer_into_an_obligation():
    class Client:
        def structured_complete(self, messages, **kwargs):
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"questions": [{"question_id": "q", "text": "network limitations", "requirement_ids": ["r"]}],
                "requirements": [{"requirement_id": "r", "question_id": "q", "predicate": "All network filesystems support shared memory"}]}))
    result = understand_new_task({"original_task": "Explain network limitations."}, Client())
    assert result["requirements"][0]["predicate"] == "network limitations"


def test_authority_declaration_is_task_scoped_and_excludes_community():
    from app.evidence.policy import user_declared_documentation
    contract = {"obligation_version": "v1", "source_constraints": {"mode": "restrict", "official_only": True, "domains": ["manual.example.org"]}}
    assert user_declared_documentation("https://manual.example.org/features.html", contract)
    assert not user_declared_documentation("https://manual.example.org/forum/features.html", contract)
    assert not user_declared_documentation("https://manual.example.org.evil.org/features.html", contract)
    assert not user_declared_documentation("https://manual.example.org/features.html", {})


def test_version_selector_does_not_silently_read_newer_source():
    from app.agent.source_context import resolve_source_snapshot
    url = "https://example.org/manual.html"
    source_id = "S" + hashlib.sha256(url.encode()).hexdigest()[:12]
    def trace(identity, text):
        return SimpleNamespace(status="success", tool_name="web_fetcher", trace_id=identity,
            output_json=json.dumps({"pages": [{"url": url, "content": text}]}))
    old = "Old version body fact. " * 20
    new = "New version body fact. " * 20
    traces = [trace("old", old), trace("new", new)]
    assert resolve_source_snapshot(traces, source_id).text == new
    assert resolve_source_snapshot(traces, source_id, source_content_sha256=hashlib.sha256(old.encode()).hexdigest()).text == old
    assert resolve_source_snapshot(traces, source_id, origin_trace_id="old").text == old
    assert resolve_source_snapshot(traces, source_id, source_content_sha256="0"*64) is None


def test_effect_statement_is_not_a_mechanism_even_if_coverage_model_says_complete():
    from app.research.answer_coverage import _missing_facets
    claims = [{"text": "Reads do not block writes.", "marker_starts": [10]}]
    row = {"complete": True, "marker_starts": [10], "facets": [{"kind": "mechanism", "complete": True, "marker_starts": [10]}]}
    assert _missing_facets(row, ["mechanism"], claims) == ["mechanism"]
    claims[0]["text"] = "Writes append to a separate log, while readers use their snapshot."
    assert _missing_facets(row, ["mechanism"], claims) == []


def test_question_projection_keeps_mechanism_ahead_of_boilerplate():
    from app.reporting.writing_evidence import build_writing_evidence
    mechanism = ("Writers merely append new content to the end of the log file. "
                 "Because writers do nothing that interferes with readers, writers and readers run at the same time. "
                 "However, since there is only one log file, there can only be one writer at a time.")
    boilerplate = "Log mode single file format documentation navigation. " * 24
    bundle = {"source_documents": [{"document_id": "d", "canonical_uri": "https://manual.example.org/log.html"}],
              "source_snapshots": [{"snapshot_id": "s", "document_id": "d", "metadata": {"evidence_role": "primary_content"}}],
              "passages": [{"passage_id": str(i), "snapshot_id": "s", "text": text, "content_basis": "full_text"}
                           for i, text in enumerate([boilerplate, mechanism])],
              "citations": [{"citation_label": f"CIT-00{i + 1}-01", "passage_id": str(i)} for i in range(2)]}
    contract = {"obligation_version": "v1", "requirements": [{"requirement_id": "r", "predicate": "Why a single writer?"}],
                "research_terms": {"r": "log mode single writer"}}
    writing = build_writing_evidence(bundle, contract, budget=200)
    assert writing.factual_units[0].passage_id == "1"
    assert "one writer at a time" in writing.factual_units[0].text
    unit = writing.factual_units[0]
    assert mechanism[unit.locator["writing_window_start"]:unit.locator["writing_window_end"]] == unit.text


def test_bilingual_negation_paraphrase_is_not_a_lexical_contradiction():
    from app.evidence.citation_validator import _has_explicit_contradiction
    assert not _has_explicit_contradiction("WAL 写入不覆盖原始数据库，因此读者可读取旧内容。", "WAL mode permits simultaneous readers and writers. Changes do not overwrite the original database file.")
    assert not _has_explicit_contradiction("WAL 读事务期间提交的写入仍不可见。", "WAL write transactions remain invisible to the read transaction.")
    assert _has_explicit_contradiction("WAL never permits simultaneous readers and writers", "WAL permits simultaneous readers and writers.")


def test_conditional_code_examples_do_not_become_additional_prose_premises():
    from app.evidence.citation_validator import _omits_condition, _has_explicit_contradiction
    quote = ("If the FloatOperation signal is trapped, accidental mixing of decimals and floats "
             "in constructors or ordering comparisons raises an exception: >>> c = getcontext () "
             ">>> c . traps [ FloatOperation ] = True >>> Decimal ( 3.14 ) "
             "Traceback (most recent call last): decimal.FloatOperation")
    claim = "若 `FloatOperation` 信号被捕获，构造器中意外混用 Decimal 与 float 会引发异常，例如 `Decimal(3.14)` 抛出 `decimal.FloatOperation`"
    assert not _omits_condition(claim, quote, quote)
    assert not _has_explicit_contradiction(claim, quote.split(": >>>")[0] + ":")
    # Colon and sentence boundaries end the premise, but real parameter
    # qualifications preceding that boundary remain mandatory.
    source = "If runtime.precision=28, conversion is rounded: >>> result = True"
    assert _omits_condition("Conversion is rounded", source, source)
    assert not _omits_condition("Conversion is rounded with runtime.precision=28", source, source)


def test_supported_translated_constructor_condition_passes_actual_validator():
    bundle = _bundle()
    fact = ("If the FloatOperation signal is trapped, accidental mixing of decimals and floats "
            "in constructors or ordering comparisons raises an exception: >>> c . traps [ FloatOperation ] = True "
            ">>> Decimal ( 3.14 ) Traceback (most recent call last): decimal.FloatOperation")
    bundle["passages"][1]["text"] = fact
    answer = "若 `FloatOperation` 信号被捕获，构造器中意外混用 Decimal 与 float 会引发异常 [CIT-001-02]。"
    result = validate_citations(answer, bundle, writing_evidence=build_writing_evidence(bundle),
        task_contract=_contract(), multilingual_llm_client=_MultilingualJudge(quote=fact))
    assert result.supported == 1 and result.unsupported == 0


def test_english_contraction_preserves_translated_negative_condition():
    from app.evidence.citation_validator import _has_explicit_contradiction
    claim = "若 `kwargs` 提供 `Context` 不支持的属性则抛出 `TypeError`"
    for contraction in ("doesn't", "doesn’t"):
        source = f"Raises TypeError if kwargs supplies an attribute that Context {contraction} support."
        assert not _has_explicit_contradiction(claim, source)
    assert _has_explicit_contradiction("TaskGroup doesn't cancel siblings", "TaskGroup cancels siblings.")
    assert not _has_explicit_contradiction("TaskGroup 不能取消任务", "TaskGroup can't cancel tasks.")


def test_repl_input_output_pair_is_substantive_without_relaxing_semantics():
    from app.evidence.citation_validator import _is_substantive_window_quote
    example = ">>> Decimal ( '3.14' ) Decimal('3.14')"
    assert _is_substantive_window_quote(example, "Example: " + example + " >>> next()")
    assert _is_substantive_window_quote(example[4:], "Example: " + example + " >>> next()")
    assert not _is_substantive_window_quote(example[4:], "Example: " + example[4:])
    assert not _is_substantive_window_quote(example[4:], ">>> other() unrelated " + example[4:])
    assert not _is_substantive_window_quote(">>> Decimal ( '3.14' )", ">>> Decimal ( '3.14' )")
    assert not _is_substantive_window_quote("Decimal('3.14')", "Decimal('3.14')")
    bundle = _bundle()
    bundle["passages"][1]["text"] = example
    answer = "从字符串构造所得数值是 `Decimal('3.14')` [CIT-001-02]。"
    for verdict, expected in (("supported", 1), ("unsupported", 0)):
        result = validate_citations(answer, bundle, writing_evidence=build_writing_evidence(bundle),
            task_contract=_contract(), multilingual_llm_client=_MultilingualJudge(quote=example, verdict=verdict))
        assert result.supported == expected


def test_whether_question_cannot_be_answered_by_an_adjacent_operation_fact():
    from app.research.answer_coverage import _required_facets, _has_direct_decisions
    question = "precision 是否限制构造时保存的数字"
    assert "decision" in _required_facets({"original_task": question}, question)
    adjacent = "prec 是设定算术运算精度的整数。create_decimal 不允许字符串含有空白。"
    assert not _has_direct_decisions([adjacent], question)
    assert _has_direct_decisions(["构造时保存的数字不受上下文 precision 限制。"], question)
    assert _has_direct_decisions(["构造时保存的数字受 precision 限制。"], question)
    english = "Explain whether precision limits stored constructor digits"
    assert not _has_direct_decisions(["Precision limits arithmetic operations."], english)
    assert _has_direct_decisions(["Precision does not limit stored constructor digits."], english)


def test_decision_facet_uses_actual_supported_answers_even_if_provider_says_complete():
    question = "precision 是否限制构造时保存的数字"
    contract = {"original_task": question, "obligation_version": "v1",
                "requirements": [{"requirement_id": "r", "predicate": question}]}
    class AllCompleteJudge:
        def is_available(self):
            return True
        def structured_complete(self, messages, **kwargs):
            inputs = json.loads(messages[-1].content)
            markers = [m for c in inputs["strictly_supported_claims"] for m in c["marker_starts"]]
            return LLMResponse(success=True, provider="fixture", model="coverage", content=json.dumps({
                "requirements": [{"requirement_id": "r", "complete": True, "marker_starts": markers,
                    "facets": [{"kind": k, "complete": True, "marker_starts": markers}
                               for k in inputs["required_facets"]["r"]]}]}))
    def evaluate(text):
        answer = text + " [CIT-001-01]。"
        validation = CitationValidationReport(details=[CitationValidationDetail(
            "CIT-001-01", "supported", answer, "fact", 1, marker_start=answer.index("[CIT"))])
        return assess_answer_coverage(answer, contract, validation, AllCompleteJudge())
    missing = evaluate("prec 是设定算术运算精度的整数；create_decimal 不允许字符串含有空白")
    assert not missing["complete"] and "decision" in missing["gaps"][0]["detail"]
    assert evaluate("构造时保存的数字不受上下文 precision 限制")["complete"]


def test_context_windows_keep_sentences_and_decimal_api_literals():
    from app.reporting.writing_evidence import _sentence_windows, _substantive_windows
    from app.agent.evidence import _contextual_web_windows
    first = "Decimal('1.100000000000000088817841970012523233890533447265625') preserves the exact value of a float."
    following = "Checkpoint must stop when it reaches a page past the end mark of any current reader."
    body = ((first + " ") * 40) + following + " The checkpoint can continue when readers finish."
    assert _sentence_windows(first) == [(0, len(first), first)]
    for left, right, window in _substantive_windows(body):
        assert window == body[left:right]
        assert window.endswith('.')
        assert not window.startswith("100000")
    for left, right in _contextual_web_windows(body):
        assert body[left:right].rstrip().endswith('.')
        assert right - left <= 6000
    assert any(following in body[a:b] for a,b in _contextual_web_windows(body))


def test_dependent_condition_accepts_prose_configuration_and_keeps_contrasting_cases():
    from app.evidence.citation_validator import _omits_condition
    from app.reporting.writing_evidence import _substantive_windows
    premise = "Writers sync on commit if PRAGMA synchronous is set to FULL but omit this sync if PRAGMA synchronous is set to NORMAL."
    middle = "Checkpointing must synchronize the WAL before moving content into the database file. " * 20
    quote = "If an application therefore runs checkpoints separately, the main thread never blocks on sync operations."
    body = premise + " " + middle + quote
    claim = "The main thread never blocks on sync operations when checkpoints run separately."
    assert _omits_condition(claim, quote, body)
    assert not _omits_condition(claim + " when synchronous=NORMAL is configured", quote, body)
    dependent = [w for _,_,w in _substantive_windows(body) if quote in w]
    assert dependent and all(premise in w for w in dependent)


def test_exact_substantive_quotes_support_non_latin_documentation():
    from app.evidence.citation_validator import _is_substantive_window_quote
    korean = "컨텍스트는 산술 연산을 위한 환경입니다."
    ukrainian = "Контекст визначає точність арифметичних операцій."
    assert _is_substantive_window_quote(korean, korean)
    assert _is_substantive_window_quote(ukrainian, ukrainian)
    assert not _is_substantive_window_quote("컨텍스트", korean)


def test_facet_markers_join_requirement_mapping_but_unknown_markers_stay_invalid():
    from app.research.answer_coverage import _coverage_rows
    row = {"requirement_id": "r1", "complete": True, "marker_starts": [1],
           "facets": [{"kind": "limitations", "complete": True, "marker_starts": [2,999]}]}
    normalized = _coverage_rows({"requirements": [row]})[0]
    assert normalized["marker_starts"] == [1,2,999]
    assert row["marker_starts"] == [1]
    assert 999 in normalized["marker_starts"]  # The validation boundary sees and rejects it.


def test_question_specific_identifier_precedes_shared_topic_phrases():
    bundle = _bundle()
    bundle["passages"][1]["text"] = "SQLite in WAL mode remains persistent across closing and reopening the database."
    other = dict(bundle["passages"][1], passage_id="busy", text="SQLITE_BUSY occurs when another connection holds an exclusive lock on the database.")
    bundle["passages"].append(other)
    bundle["citations"].append({"citation_label": "CIT-002-01", "passage_id": "busy"})
    contract = {"obligation_version": "research-obligations-v1", "requirements": [
        {"requirement_id": "r1", "predicate": "SQLite WAL persistence"},
        {"requirement_id": "r2", "predicate": "SQLite WAL SQLITE_BUSY"}]}
    writing = build_writing_evidence(bundle, contract)
    assert any("SQLITE_BUSY occurs" in unit.text for unit in writing.factual_units)


def test_unknown_facet_marker_cannot_complete_a_real_answer_mapping():
    import re
    from app.research.answer_coverage import assess_answer_coverage
    text = "Readers run concurrently [CIT-001-01]."
    pos = text.index("CIT-")
    validation = CitationValidationReport(details=[CitationValidationDetail("CIT-001-01", "supported", text, "fact", 1, marker_start=pos)])
    class Judge:
        def is_available(self): return True
        def structured_complete(self,*args,**kwargs):
            return LLMResponse(success=True, provider="fixture", content=json.dumps({"requirements":[
                {"requirement_id":"r1","complete":True,"marker_starts":[pos],
                 "facets":[{"kind":"limitations","complete":True,"marker_starts":[999]}]}]}))
    coverage = assess_answer_coverage(text, _contract(), validation, Judge())
    assert coverage["requirements"][0]["answer_status"] == "unanswered"
    assert not coverage["complete"]


def test_compound_explicit_questions_keep_independent_literal_obligations():
    from app.research.task_understanding import _split_explicit_compound_questions
    original = "precision 是否限制构造时保存的数字及何时作用于运算"
    result = _split_explicit_compound_questions({"questions":[{"question_id":"q1","text":original,"requirement_ids":["r1"]}],
       "requirements":[{"requirement_id":"r1","question_id":"q1","entity":"Decimal precision construction arithmetic"}]})
    assert [q["text"] for q in result["questions"]] == ["precision 是否限制构造时保存的数字", "何时作用于运算"]
    assert all(q["text"] in original for q in result["questions"])
    assert len({r["requirement_id"] for r in result["requirements"]}) == 2


def test_copy_restore_process_counts_as_mechanism_while_effect_does_not():
    from app.research.answer_coverage import _missing_facets
    row={"marker_starts":[10],"facets":[{"kind":"mechanism","complete":True,"marker_starts":[10]}]}
    assert not _missing_facets(row,["mechanism"],[{"text":"进入 with 时设为副本，退出时恢复先前上下文。","marker_starts":[10]}])
    assert _missing_facets(row,["mechanism"],[{"text":"执行得更快。","marker_starts":[10]}])
