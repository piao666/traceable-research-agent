from app.agent.evidence_requirements import assess_required_evidence
from app.agent.reporter import (
    _revision_citation_labels, _revision_prompt_context, _synthesis_revision_feedback,
)
from app.reporting.claim_occurrence import is_evidence_limitation_statement
from app.reporting.integrity import _is_uncertain_or_limitation
from app.reporting.writing_evidence import build_writing_evidence
from app.evidence.citation_validator import (
    CitationValidationDetail,
    _citation_local_claim,
    _has_explicit_contradiction,
    validate_citations,
)


class _MultilingualJudge:
    def __init__(self, verdict: str = "supported", quote: str | None = None):
        self.verdict = verdict
        self.quote = quote
        self.calls = 0
        self.max_tokens = None

    def is_available(self):
        return True

    def structured_complete(self, messages, temperature=0.0, max_tokens=2000):
        from app.llm.base import LLMResponse, LLMUsage
        import json

        self.calls += 1
        self.max_tokens = max_tokens
        case = json.loads(messages[-1].content)["cases"][0]
        identity = case["identity"]
        return LLMResponse(
            success=True,
            content=json.dumps({"verdicts": [{
                "citation_label": identity["citation_label"],
                "marker_start": identity["marker_start"],
                "verdict": self.verdict,
                "evidence_quote": self.quote if self.quote is not None else case["evidence_window"],
                **identity,
            }]}),
            provider="fixture", model="multilingual-judge",
            usage=LLMUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        )


class _AllMultilingualJudge(_MultilingualJudge):
    """Fixture judge that supports each supplied frozen-window case."""

    def structured_complete(self, messages, temperature=0.0, max_tokens=2000):
        from app.llm.base import LLMResponse, LLMUsage
        import json

        self.calls += 1
        self.max_tokens = max_tokens
        cases = json.loads(messages[-1].content)["cases"]
        return LLMResponse(
            success=True,
            content=json.dumps({"verdicts": [{
                "citation_label": case["identity"]["citation_label"],
                "marker_start": case["identity"]["marker_start"],
                "verdict": self.verdict,
                "evidence_quote": self.quote if self.quote is not None else case["evidence_window"],
                **case["identity"],
            } for case in cases]}),
            provider="fixture", model="multilingual-judge",
            usage=LLMUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        )


def test_multilingual_marker_uses_its_own_clause_without_borrowing_another_fact():
    sentence = (
        "Muse 能执行多步任务 [CIT-001-01]，运行于独立虚拟机 [CIT-002-01]；"
        "Jev 处理类型化决策 [CIT-003-01][CIT-004-01]。"
    )
    cases = [
        ("CIT-001-01", "Muse 能执行多步任务"),
        ("CIT-002-01", "运行于独立虚拟机"),
        ("CIT-003-01", "Jev 处理类型化决策"),
        ("CIT-004-01", "Jev 处理类型化决策"),
    ]
    for label, expected in cases:
        detail = CitationValidationDetail(
            citation_label=label, verdict="weakly_supported", sentence=sentence,
            passage_text="", keyword_overlap=0.0,
            marker_start=sentence.index(label), sentence_start=0,
        )
        assert _citation_local_claim(detail) == expected
    # An uncited date before the first marker stays inside that marker's
    # proposition: local segmentation cannot silently legitimize it.
    dated = sentence.replace("Muse 能", "2026 年 Muse 能")
    detail = CitationValidationDetail(
        citation_label="CIT-001-01", verdict="weakly_supported", sentence=dated,
        passage_text="", keyword_overlap=0.0,
        marker_start=dated.index("CIT-001-01"), sentence_start=0,
    )
    assert _citation_local_claim(detail).startswith("2026 年")


def test_multilingual_judge_receives_marker_local_claims_not_the_joined_sentence():
    import json

    bundle = _bundle()
    bundle["passages"][1]["text"] = "Muse executes multi-step tasks in a chat interface."
    bundle["passages"].append({
        "passage_id": "body2", "snapshot_id": "s3",
        "text": "Jev returns typed decisions with probability scores.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    })
    bundle["source_snapshots"].append({"snapshot_id": "s3", "document_id": "d3"})
    bundle["source_documents"].append({"document_id": "d3", "canonical_uri": "https://x/jev"})
    bundle["citations"].append({"citation_label": "CIT-002-01", "passage_id": "body2"})
    frozen = build_writing_evidence(bundle)
    sentence = "Muse 在聊天界面执行多步任务 [CIT-001-02]，Jev 返回带概率的类型化决策 [CIT-002-01]。"

    class RecordingJudge(_AllMultilingualJudge):
        def structured_complete(self, messages, temperature=0.0, max_tokens=2000):
            self.cases = json.loads(messages[-1].content)["cases"]
            return super().structured_complete(messages, temperature, max_tokens)

    judge = RecordingJudge()
    validation = validate_citations(
        sentence, bundle, writing_evidence=frozen,
        task_contract={"output_constraints": {"language": "zh"}},
        multilingual_llm_client=judge, min_supported_overlap=0.15,
        min_weak_overlap=0.05,
    )
    assert [case["claim_sentence"] for case in judge.cases] == [
        "Muse 在聊天界面执行多步任务", "Jev 返回带概率的类型化决策",
    ]
    assert validation.supported == 2


def test_revision_feedback_prioritizes_hard_failures_when_strict_rate_passes():
    from types import SimpleNamespace

    details = [
        SimpleNamespace(citation_label="CIT-001-01", verdict="unsupported", sentence="Unsupported conclusion"),
        SimpleNamespace(citation_label="CIT-002-01", verdict="weakly_supported", sentence="Tolerated paraphrase"),
    ]
    validation = SimpleNamespace(
        total=10, supported=7, details=details,
        uncited_claims=["Uncited inference"],
    )
    feedback = _synthesis_revision_feedback(validation)
    assert feedback["must_remove_or_rewrite_unsupported"] == [
        {"citation": "CIT-001-01", "sentence": "Unsupported conclusion"}
    ]
    assert feedback["must_remove_or_cite_uncited"] == ["Uncited inference"]
    assert feedback["weak_citations_to_improve_only_if_needed"] == []
    validation.supported = 4
    assert _synthesis_revision_feedback(validation)["weak_citations_to_improve_only_if_needed"]


def test_narrow_evidence_limit_intro_does_not_require_a_factual_citation():
    meta = "关于 Jev 对 Agent 的影响，现有证据只能支持有限结论。"
    assert is_evidence_limitation_statement(meta)
    assert _is_uncertain_or_limitation(meta)
    assert is_evidence_limitation_statement(
        "关于 Jev 对 Agent 的影响，现有证据同样只能支持有限结论。"
    )
    for factual in (
        "关于 Jev 对 Agent 的影响，现有证据只能支持有限结论：Jev 提速 200 倍。",
        "现有证据只能支持有限结论，Jev 已成为 Agent 标准。",
    ):
        assert not is_evidence_limitation_statement(factual)


def _bundle():
    return {
        "passages": [
            {"passage_id": "search", "snapshot_id": "s1", "text": "search snippet", "content_basis": "snippet_only", "metadata": {"evidence_role": "discovery_index"}},
            {"passage_id": "body", "snapshot_id": "s2", "text": "The official body says the feature is stable.", "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"}},
        ],
        "source_snapshots": [{"snapshot_id": "s1", "document_id": "d1"}, {"snapshot_id": "s2", "document_id": "d2"}],
        "source_documents": [{"document_id": "d1", "canonical_uri": "https://x/search"}, {"document_id": "d2", "canonical_uri": "https://x/body"}],
        "citations": [{"citation_id": "persisted-search", "citation_label": "CIT-001-01", "passage_id": "search"}, {"citation_id": "persisted-body", "citation_label": "CIT-001-02", "passage_id": "body"}],
    }


def test_factual_projection_excludes_discovery_but_retains_index():
    result = build_writing_evidence(_bundle(), {"evidence_requirement": "substantive"})
    assert result.allowed_citation_ids == ("CIT-001-02",)
    assert result.factual_units[0].passage_id == "body"
    assert result.discovery_references == ({"citation_id": "CIT-001-01", "source_url": "https://x/search"},)


def test_revision_focus_reselects_exact_body_window_without_promoting_snippets():
    import hashlib
    from copy import deepcopy
    bundle = _bundle()
    introduction = "The component overview describes its architecture."
    target = "TaskGroup cancels sibling tasks when one task fails."
    passage = introduction + "\n\n" + target
    bundle["passages"][1].update(text=passage, content_hash=hashlib.sha256(passage.encode()).hexdigest())
    before = deepcopy(bundle)
    contract = {"original_task": "component overview architecture"}
    first = build_writing_evidence(bundle, contract, 1000)
    assert first.factual_units[0].text == introduction
    refreshed = build_writing_evidence(bundle, contract, 1000, focus_by_citation={
        "CIT-001-02": target, "CIT-001-01": "search snippet", "CIT-999-99": target,
    })
    unit = refreshed.factual_units[0]
    assert refreshed.allowed_citation_ids == ("CIT-001-02",)
    assert unit.text == target
    assert passage[unit.locator["writing_window_start"]:unit.locator["writing_window_end"]] == target
    assert unit.passage_sha256 == first.factual_units[0].passage_sha256
    assert unit.text_sha256 == hashlib.sha256(target.encode()).hexdigest()
    assert bundle == before
    bundle["passages"][1]["text"] += "tampered"
    assert not build_writing_evidence(bundle, contract, 1000, focus_by_citation={"CIT-001-02": target}).factual_units


def test_revision_rebinds_only_the_same_persisted_citation_and_exact_window():
    from copy import deepcopy
    original = _bundle()
    original["citations"][1]["citation_id"] = "persisted-citation-body"
    first = build_writing_evidence(original, budget=1000)
    refreshed = deepcopy(original)
    refreshed["citations"][1]["citation_label"] = "CIT-019-03"
    second = build_writing_evidence(refreshed, budget=1000)
    labels = _revision_citation_labels(original, refreshed, first)
    assert labels == {"CIT-001-02": "CIT-019-03"}
    draft, feedback = _revision_prompt_context(
        "The feature is stable [CIT-001-02].",
        {"must_remove_or_rewrite_unsupported": [
            {"citation": "CIT-001-02", "sentence": "The feature is stable [CIT-001-02]."},
        ]}, first, second, labels,
    )
    assert draft == "The feature is stable [CIT-019-03]."
    assert feedback["must_remove_or_rewrite_unsupported"][0]["citation"] == "CIT-019-03"

    # Reusing the visible label for a different persisted citation must not
    # carry the old answer into the new model prompt.
    replaced = deepcopy(refreshed)
    replaced["citations"][1]["citation_id"] = "another-citation"
    assert _revision_citation_labels(original, replaced, first) == {}
    draft, feedback = _revision_prompt_context(
        "The feature is stable [CIT-001-02].",
        {"citation": "CIT-001-02"}, first, build_writing_evidence(replaced, budget=1000), {},
    )
    assert draft == ""
    assert feedback["citation"] == "prior source unavailable"

    # A source body revision can keep the persisted citation ID while losing
    # the original exact window; the old statement still must be regenerated.
    changed = deepcopy(refreshed)
    changed["passages"][1]["text"] = "The official body now says this feature is deprecated."
    labels = _revision_citation_labels(original, changed, first)
    assert labels == {}


def test_when_question_projects_complete_source_enumeration():
    from app.reporting.writing_evidence import _substantive_windows
    body = (
        "The file format is described here. "
        "Cases where a query can return E_LOCK include the following: "
        "If another connection owns an exclusive lock, the query returns E_LOCK. "
        "When the final connection cleans up, a concurrent query may return E_LOCK. "
        "If recovery holds the exclusive lock, a third query returns E_LOCK. "
        "10. Other topics describe the file header."
    )
    windows = _substantive_windows(body)
    assert any(
        "Cases where a query" in value
        and "another connection" in value
        and "final connection" in value
        and "recovery holds" in value
        and "Other topics" not in value
        and body[start:end] == value
        for start, end, value in windows
    )


def test_report_describes_the_semantic_validator_it_used():
    from app.evidence.citation_validator import CitationValidationReport, render_citation_validation_section
    report = CitationValidationReport(
        occurrence_total=1, unique_citation_count=1, supported_occurrences=1,
        multilingual_adjudication={"version": "multilingual-window-entailment-v2"},
    )
    rendered = "\n".join(render_citation_validation_section(report))
    assert "逐条语义裁决" in rendered
    assert "关键词重叠仅用于筛选" in rendered
    assert "引用准确性由关键词重叠率" not in rendered


def test_report_writer_revision_and_final_validator_share_frozen_windows(db, monkeypatch):
    from app.agent import reporter
    from app.config import settings
    from app.evidence import citation_validator
    from app.evidence.citation_validator import CitationValidationReport
    from app.trace import store
    bundle = _bundle()
    intro = "The component overview describes its architecture."
    target = "TaskGroup cancels sibling tasks when one task fails."
    bundle["passages"][1]["text"] = intro + "\n\n" + target
    plan = {"execution_mode": "deep_research_v2", "steps": [],
            "task_contract": {"original_task": "component overview architecture"}}
    run = store.create_agent_run(db, "component overview architecture", "summary", "real")
    written, validated, saved = [], [], []
    answer = target + " [CIT-001-02]"
    def writer(*args, writing_evidence=None, **kwargs):
        written.append(writing_evidence)
        return answer
    def validator(*args, writing_evidence=None, **kwargs):
        validated.append(writing_evidence)
        supported = writing_evidence.factual_units[0].text == target
        return CitationValidationReport(
            occurrence_total=1, unique_citation_count=1,
            supported_occurrences=int(supported), unsupported_occurrences=int(not supported),
            details=[CitationValidationDetail(citation_label="CIT-001-02",
                verdict="supported" if supported else "unsupported", sentence=answer,
                passage_text=writing_evidence.factual_units[0].text, keyword_overlap=1.0 if supported else 0.0)],
        )
    monkeypatch.setattr(reporter, "_llm_synthesize_answer", writer)
    monkeypatch.setattr(citation_validator, "validate_citations", validator)
    monkeypatch.setattr(settings, "citation_validation_enabled", True)
    monkeypatch.setattr(settings, "citation_validation_llm_enabled", False)
    reporter.generate_markdown_report(run, plan, [], [], llm_client=object(), provenance_bundle=bundle,
        revision_attempt_callback=lambda attempt, text, diagnostic: saved.append(diagnostic) or str(attempt))
    assert len(written) == 2
    assert len(validated) == 3
    assert validated[0] is written[0]
    assert validated[1] is validated[2] is written[1]
    assert written[0].factual_units[0].text == intro
    assert written[1].factual_units[0].text == target
    assert plan["report_draft_result"]["adopted"] is True
    assert saved[0]["writing_windows"][0]["text_sha256"] != saved[1]["writing_windows"][0]["text_sha256"]


def test_report_refresh_uses_new_bundle_and_audits_snapshot_per_attempt(db, monkeypatch):
    from copy import deepcopy
    from app.agent import reporter
    from app.config import settings
    from app.evidence import citation_validator
    from app.evidence.citation_validator import CitationValidationReport
    from app.trace import store
    bundle = _bundle()
    refreshed = deepcopy(bundle)
    target = "TaskGroup cancels sibling tasks when one task fails."
    refreshed["passages"][1]["text"] = target
    run = store.create_agent_run(db, "Explain TaskGroup", "summary", "real")
    plan = {"execution_mode": "deep_research_v2", "steps": [], "task_contract": {"original_task": run.task}}
    seen, saved, feedback_calls = [], [], []
    def validator(answer, actual_bundle, **kwargs):
        seen.append(actual_bundle)
        good = actual_bundle is refreshed
        return CitationValidationReport(occurrence_total=1, supported_occurrences=int(good),
            unsupported_occurrences=int(not good), details=[CitationValidationDetail(
                citation_label="CIT-001-02", sentence=target, passage_text="", keyword_overlap=0,
                verdict="supported" if good else "unsupported")])
    monkeypatch.setattr(reporter, "_llm_synthesize_answer", lambda *a, **k: target + " [CIT-001-02]")
    monkeypatch.setattr(citation_validator, "validate_citations", validator)
    monkeypatch.setattr(settings, "citation_validation_enabled", True)
    monkeypatch.setattr(settings, "citation_validation_llm_enabled", False)
    reporter.generate_markdown_report(run, plan, [], [], llm_client=object(), provenance_bundle=bundle,
        evidence_refresh_callback=lambda feedback: feedback_calls.append(feedback) or refreshed,
        revision_attempt_callback=lambda attempt, text, diagnostic: saved.append(diagnostic) or str(attempt))
    assert len(feedback_calls) == 1
    assert seen[0] is bundle and seen[1] is seen[2] is refreshed
    assert plan["report_draft_result"]["adopted"]
    assert saved[0]["evidence_snapshot_sha256"] != saved[1]["evidence_snapshot_sha256"]


def test_findings_seed_is_revalidated_before_report_adoption(db, monkeypatch):
    from app.agent import reporter
    from app.config import settings
    from app.evidence import citation_validator
    from app.evidence.citation_validator import CitationValidationReport
    from app.trace import store

    run = store.create_agent_run(db, "Explain the feature", "summary", "real")
    plan = {"steps": [], "task_contract": {"original_task": run.task}}
    seed = "The feature is unsafe [CIT-001-02]."
    revised = "The official body says the feature is stable [CIT-001-02]."
    seen, written, saved = [], [], []

    def validator(answer, *_args, **_kwargs):
        seen.append(answer)
        good = answer == revised
        return CitationValidationReport(occurrence_total=1, supported_occurrences=int(good),
            unsupported_occurrences=int(not good), details=[CitationValidationDetail(
                citation_label="CIT-001-02", sentence=answer, passage_text="source", keyword_overlap=0,
                marker_start=answer.index("CIT-"), verdict="supported" if good else "unsupported")])

    def writer(*_args, **_kwargs):
        written.append(True)
        return revised

    monkeypatch.setattr(reporter, "_audited_findings_candidate", lambda *_: (seed, {"decision_sha256": "stage"}))
    monkeypatch.setattr(reporter, "_llm_synthesize_answer", writer)
    monkeypatch.setattr(citation_validator, "validate_citations", validator)
    monkeypatch.setattr(settings, "citation_validation_enabled", True)
    monkeypatch.setattr(settings, "citation_validation_llm_enabled", False)
    markdown = reporter.generate_markdown_report(run, plan, [], [], llm_client=object(), provenance_bundle=_bundle(),
        revision_attempt_callback=lambda attempt, text, diagnostic: saved.append(diagnostic) or str(attempt))
    assert seen[0] == seed and revised in seen
    assert len(written) == 1
    assert saved[0]["candidate_source"]["kind"] == "audited_research_findings"
    assert saved[1]["candidate_source"]["kind"] == "report_synthesis"
    assert plan["report_draft_result"]["adopted"]
    assert revised in markdown and seed not in markdown


def test_writer_prompt_omits_repeated_audit_fields_but_keeps_frozen_units():
    result = build_writing_evidence(_bundle(), {"evidence_requirement": "substantive"})
    projected = result.prompt_payload()["factual_units"][0]
    assert projected == {
        "citation_id": "CIT-001-02",
        "source_url": "https://x/body",
        "evidence_role": "primary_content",
        "content_basis": "full_text",
        "text": "The official body says the feature is stable.",
    }
    assert result.factual_units[0].passage_id == "body"
    assert result.factual_units[0].text_sha256


def test_modern_unknown_role_is_not_promoted_to_factual_evidence():
    bundle = _bundle()
    bundle["passages"][1]["metadata"] = {}
    result = build_writing_evidence(bundle)
    assert result.allowed_citation_ids == ()
    assert result.gaps[0]["code"] == "no_eligible_writing_evidence"


def test_snapshot_role_controls_same_source_acquisition_and_passage_hash_remains_fail_closed():
    bundle = _bundle()
    # One canonical source can have a discovery snapshot and a later full-text
    # snapshot. The former must never be promoted just because the latter was
    # read; the snapshot's acquisition role is the authority for its bytes.
    bundle["source_documents"] = [{
        "document_id": "d1", "canonical_uri": "https://x/shared",
        "metadata": {"evidence_role": "discovery_index"},
    }]
    bundle["source_snapshots"] = [
        {"snapshot_id": "s1", "document_id": "d1", "metadata": {"evidence_role": "discovery_index"}},
        {"snapshot_id": "s2", "document_id": "d1", "metadata": {"evidence_role": "primary_content"}},
    ]
    frozen = build_writing_evidence(bundle)
    assert frozen.allowed_citation_ids == ("CIT-001-02",)
    assessment = assess_required_evidence(
        {"evidence_requirement": "substantive", "original_task": "feature stable"}, bundle
    )
    assert assessment.passed
    validation = validate_citations(
        "Search snippet [CIT-001-01]. The official body says the feature is stable [CIT-001-02].",
        bundle,
    )
    assert [detail.verdict for detail in validation.details] == ["unsupported", "supported"]

    # Historical rows lack snapshot roles. Their persisted document policy
    # still outranks a derived passage label, so old discovery cannot be
    # forged into factual evidence.
    legacy = _bundle()
    legacy["source_documents"][1]["metadata"] = {"evidence_role": "discovery_index"}
    assert build_writing_evidence(legacy).allowed_citation_ids == ()
    legacy_validation = validate_citations(
        "The feature is stable [CIT-001-02]", legacy
    )
    assert legacy_validation.unsupported == 1

    bundle = _bundle()
    bundle["passages"][1]["content_hash"] = "not-the-persisted-bytes"
    frozen = build_writing_evidence(bundle)
    assert frozen.allowed_citation_ids == ()


def test_validator_rejects_tampered_frozen_window():
    bundle = _bundle()
    frozen = build_writing_evidence(bundle)
    unit = frozen.factual_units[0]
    object.__setattr__(unit, "text", "invented fact")
    report = validate_citations("The feature is invented [CIT-001-02]", bundle, writing_evidence=frozen)
    assert report.unsupported == 1


def test_original_task_selects_later_relevant_window_with_exact_offsets():
    prefix = "This introductory paragraph discusses an unrelated overview."
    target = "Asyncio TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = prefix + "\n\n" + target
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(
        bundle["passages"][1]["text"].encode("utf-8")
    ).hexdigest()
    frozen = build_writing_evidence(bundle, {"original_task": "How does asyncio TaskGroup cancel sibling tasks?"})
    unit = frozen.factual_units[0]
    assert unit.text == target
    assert bundle["passages"][1]["text"][unit.locator["writing_window_start"]:unit.locator["writing_window_end"]] == target


def test_validator_rejects_window_with_invalid_offset_binding():
    repeated = "The feature is stable."
    bundle = _bundle()
    bundle["passages"][1]["text"] = repeated + "\n\n" + repeated
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(
        bundle["passages"][1]["text"].encode("utf-8")
    ).hexdigest()
    frozen = build_writing_evidence(bundle, {"original_task": "feature stable"})
    unit = frozen.factual_units[0]
    object.__setattr__(unit, "locator", {"writing_window_start": 1, "writing_window_end": len(repeated) + 1})
    report = validate_citations("The feature is stable [CIT-001-02]", bundle, writing_evidence=frozen)
    assert report.unsupported == 1


def test_long_unparagraphized_body_selects_relevant_sentence_with_exact_offsets():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    body = " ".join(f"navigationtopic{index}." for index in range(180)) + " " + fact
    bundle = _bundle()
    bundle["passages"][1]["text"] = body
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
    frozen = build_writing_evidence(
        bundle, {"original_task": "How does TaskGroup cancel sibling tasks?"}
    )
    unit = frozen.factual_units[0]
    assert unit.text == fact
    assert body[unit.locator["writing_window_start"]:unit.locator["writing_window_end"]] == fact
    report = validate_citations(
        f"{fact} [CIT-001-02]", bundle, writing_evidence=frozen,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert report.supported == 1


def test_cjk_focuses_real_fact_but_cross_language_paraphrase_stays_non_supported():
    fact = "Jev 是用于结构化决策的系统一模型。"
    body = "".join(f"无关导航词{index}。" for index in range(180)) + fact
    bundle = _bundle()
    bundle["passages"][1]["text"] = body
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
    frozen = build_writing_evidence(bundle, {"original_task": "Jev 模型是什么？"})
    unit = frozen.factual_units[0]
    assert unit.text == fact
    assert body[unit.locator["writing_window_start"]:unit.locator["writing_window_end"]] == fact
    same_language = validate_citations(
        f"{fact}[CIT-001-02]", bundle, writing_evidence=frozen,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert same_language.supported == 1
    cross_language = validate_citations(
        "Jev is a System One model for structured decisions. [CIT-001-02]",
        bundle, writing_evidence=frozen, min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert cross_language.supported == 0


def test_explicit_chinese_output_can_adjudicate_an_english_frozen_window_fail_closed():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {
        "original_task": "Explain TaskGroup cancellation in Chinese.",
        "output_constraints": {"language": "Chinese"},
    }
    frozen = build_writing_evidence(bundle, contract)
    answer = "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]"

    disabled = validate_citations(
        answer, bundle, writing_evidence=frozen, task_contract={
            **contract, "output_constraints": {"language": "Chinese", "multilingual_citation_validation": False}
        }, multilingual_llm_client=_MultilingualJudge(), min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert disabled.supported == 0

    judge = _MultilingualJudge()
    result = validate_citations(
        answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert result.supported == 1
    assert result.details[0].judgment_source == "multilingual_llm"
    assert judge.calls == 1
    assert result.multilingual_adjudication["version"] == "multilingual-window-entailment-v8"
    assert result.multilingual_adjudication["method"] == "bounded_frozen_window_semantic_adjudication"
    assert result.to_dict()["min_supported_overlap"] == 0.15


def test_multilingual_adjudication_refuses_contradiction_and_invalid_frozen_evidence():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain TaskGroup in Chinese.", "output_constraints": {"language": "Chinese"}}
    frozen = build_writing_evidence(bundle, contract)
    judge = _MultilingualJudge()
    contradiction = validate_citations(
        "TaskGroup 不会在任务失败时取消同级任务。[CIT-001-02]", bundle,
        writing_evidence=frozen, task_contract=contract, multilingual_llm_client=judge,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert contradiction.supported == 0
    assert judge.calls == 0

    unit = frozen.factual_units[0]
    object.__setattr__(unit, "text", "tampered")
    invalid = validate_citations(
        "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]", bundle,
        writing_evidence=frozen, task_contract=contract, multilingual_llm_client=judge,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert invalid.supported == 0
    assert invalid.details[0].judgment_source == "hard_invalid_citation"
    assert judge.calls == 0


def test_multilingual_adjudication_refuses_a_non_window_quote():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain TaskGroup in Chinese.", "output_constraints": {"language": "Chinese"}}
    frozen = build_writing_evidence(bundle, contract)
    result = validate_citations(
        "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]", bundle,
        writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=_MultilingualJudge(quote="invented quotation"),
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert result.supported == 0
    assert result.details[0].judgment_source == "rule"


def test_multilingual_adjudication_refuses_a_short_generic_window_substring():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain TaskGroup in Chinese.", "output_constraints": {"language": "Chinese"}}
    result = validate_citations(
        "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]", bundle,
        writing_evidence=build_writing_evidence(bundle, contract), task_contract=contract,
        multilingual_llm_client=_MultilingualJudge(quote="TaskGroup cancels"),
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert result.supported == 0


def test_multilingual_adjudication_checks_cancellation_before_provider_call():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain TaskGroup in Chinese.", "output_constraints": {"language": "Chinese"}}
    judge = _MultilingualJudge()
    frozen = build_writing_evidence(bundle, contract)
    try:
        validate_citations(
            "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]", bundle,
            writing_evidence=frozen, task_contract=contract, multilingual_llm_client=judge,
            cancellation_check=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")),
            min_supported_overlap=0.15, min_weak_overlap=0.05,
        )
    except RuntimeError as exc:
        assert str(exc) == "cancelled"
    else:
        raise AssertionError("cancellation must stop adjudication")
    assert judge.calls == 0


def test_multilingual_adjudication_caches_a_token_accounted_call_by_frozen_case():
    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain TaskGroup in Chinese.", "output_constraints": {"language": "Chinese"}}
    frozen = build_writing_evidence(bundle, contract)
    answer = "TaskGroup 会在一个任务失败时取消同级任务。[CIT-001-02]"
    judge = _MultilingualJudge()
    cache = {}
    recorded = []
    first = validate_citations(answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, multilingual_adjudication_cache=cache,
        multilingual_usage_callback=recorded.append, min_supported_overlap=0.15, min_weak_overlap=0.05)
    second = validate_citations(answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, multilingual_adjudication_cache=cache,
        multilingual_usage_callback=recorded.append, min_supported_overlap=0.15, min_weak_overlap=0.05)
    assert first.supported == second.supported == 1
    assert judge.calls == 1
    assert len(recorded) == 1
    assert judge.max_tokens == 6000
    assert first.multilingual_adjudication["usage_traced"] is True
    assert second.multilingual_adjudication["cached"] is True
    assert second.multilingual_adjudication["usage_traced"] is False


def test_multilingual_adjudication_reviews_more_than_one_bounded_batch():
    import hashlib

    fact = "TaskGroup cancels sibling tasks when one task fails."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = hashlib.sha256(fact.encode("utf-8")).hexdigest()
    bundle["citations"] = [
        {"citation_label": f"CIT-001-{index:02d}", "passage_id": "body"}
        for index in range(1, 21)
    ]
    contract = {"output_constraints": {"language": "zh"}}
    frozen = build_writing_evidence(bundle, contract)
    answer = "".join(
        f"TaskGroup \u5728\u4e00\u4e2a\u4efb\u52a1\u5931\u8d25\u65f6\u53d6\u6d88\u540c\u7ea7\u4efb\u52a1 "
        f"[CIT-001-{index:02d}]\u3002"
        for index in range(1, 21)
    )
    judge = _AllMultilingualJudge()
    cache = {}
    recorded = []
    first = validate_citations(
        answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, multilingual_adjudication_cache=cache,
        multilingual_usage_callback=recorded.append,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert first.total == first.supported == 20
    assert first.multilingual_adjudication["candidate_count"] == 20
    assert judge.calls == len(recorded) == 2
    second = validate_citations(
        answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, multilingual_adjudication_cache=cache,
        multilingual_usage_callback=recorded.append,
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert second.supported == 20
    assert second.multilingual_adjudication["cached"] is True
    assert judge.calls == len(recorded) == 2


def test_chinese_prose_with_inline_python_identifier_is_still_multilingual():
    fact = "asyncio.get_running_loop() raises RuntimeError when no running event loop exists."
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    contract = {"original_task": "Explain asyncio in Chinese.", "output_constraints": {"language": "Chinese"}}
    result = validate_citations(
        "当没有正在运行的事件循环时，`asyncio.get_running_loop()` 会抛出 `RuntimeError`。[CIT-001-02]",
        bundle, writing_evidence=build_writing_evidence(bundle, contract), task_contract=contract,
        multilingual_llm_client=_MultilingualJudge(), min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    assert result.supported == 1


def test_round7_chinese_event_loop_claim_ignores_citation_marker_pseudo_entities():
    fact = (
        "The event loop is the core of every asyncio application. Event loops run "
        "asynchronous tasks and callbacks, perform network IO operations, and run subprocesses."
    )
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    bundle["citations"] = [
        {"citation_label": "CIT-009-01", "passage_id": "body"},
        {"citation_label": "CIT-010-01", "passage_id": "body"},
    ]
    contract = {
        "original_task": "Explain Python asyncio event loops in concise Chinese.",
        "output_constraints": {"language": "zh"},
    }
    frozen = build_writing_evidence(bundle, contract)
    result = validate_citations(
        "事件循环是每个 asyncio 应用的核心：它运行异步任务与回调、执行网络 IO 操作，并运行子进程"
        "[CIT-009-01][CIT-010-01]。",
        bundle,
        writing_evidence=frozen,
        task_contract=contract,
        multilingual_llm_client=_MultilingualJudge(),
        min_supported_overlap=0.15,
        min_weak_overlap=0.05,
    )
    assert result.multilingual_adjudication["candidate_count"] == 2
    assert result.supported == 1


def test_round8_multilingual_prefilter_ignores_unrelated_window_negation_and_page_entities():
    """A later source sentence cannot negate a different cited fact."""
    window = (
        "The event loop is the core of every asyncio application. Event loops run "
        "asynchronous tasks and callbacks, perform network IO operations, and run subprocesses. "
        "Application developers should typically use high-level functions such as asyncio.run(). "
        "If there is no running event loop, a RuntimeError is raised. "
        "The BaseEventLoop implementation should not be used directly."
    )
    core = "Python asyncio 的事件循环是每个 asyncio 应用的核心：它运行异步任务与回调、执行网络 IO 操作并运行子进程 [CIT-009-01]。"
    high_level = "应用开发者通常应使用 `asyncio.run()` 之类的高层函数，很少需要直接调用 loop 方法 [CIT-010-01]。"
    facilities = "事件循环能注册、执行和取消延迟调用，并为通信创建 transport [CIT-014-01]。"

    assert not _has_explicit_contradiction(core, window)
    assert not _has_explicit_contradiction(high_level, window)
    assert not _has_explicit_contradiction(facilities, window)


def test_multilingual_prefilter_keeps_local_number_negation_and_api_conflicts_hard():
    assert _has_explicit_contradiction(
        "TaskGroup does not cancel sibling tasks when one task fails.",
        "TaskGroup cancels sibling tasks when one task fails.",
    )
    assert _has_explicit_contradiction(
        "TaskGroup supports 3 workers.",
        "TaskGroup supports 2 workers.",
    )
    assert _has_explicit_contradiction(
        "`asyncio.get_running_loop()` returns the running loop.",
        "`asyncio.get_event_loop()` returns the current loop.",
    )
    assert _has_explicit_contradiction(
        "`asyncio.run()` does not start an event loop.",
        "The asyncio.run() function starts an event loop and executes the coroutine.",
    )


def test_long_window_unrelated_negation_reaches_quote_guarded_adjudication():
    import hashlib

    first = "FastAPI WebSocket supports reading data from the client and sending data to it."
    fact = first + " FastAPI does not infer unrelated database permissions. " + (
        "A WebSocket connection carries messages between peers. " * 8
    )
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = hashlib.sha256(fact.encode("utf-8")).hexdigest()
    contract = {"output_constraints": {"language": "zh"}}
    answer = "FastAPI WebSocket \u53ef\u4ee5\u4ece\u5ba2\u6237\u7aef\u8bfb\u53d6\u6570\u636e\u5e76\u5411\u5ba2\u6237\u7aef\u53d1\u9001\u6570\u636e [CIT-001-02]\u3002"
    frozen = build_writing_evidence(bundle, contract)
    assert len(frozen.factual_units[0].text) > 256
    judge = _MultilingualJudge(quote=first)
    result = validate_citations(answer, bundle, writing_evidence=frozen, task_contract=contract,
        multilingual_llm_client=judge, min_supported_overlap=0.15, min_weak_overlap=0.05)
    assert judge.calls == 1
    assert result.supported == 1


def test_round8_style_unrelated_window_context_reaches_semantic_adjudication():
    fact = (
        "The event loop is the core of every asyncio application. Event loops run "
        "asynchronous tasks and callbacks, perform network IO operations, and run subprocesses. "
        "If there is no running event loop, a RuntimeError is raised."
    )
    bundle = _bundle()
    bundle["passages"][1]["text"] = fact
    bundle["passages"][1]["content_hash"] = __import__("hashlib").sha256(fact.encode("utf-8")).hexdigest()
    bundle["citations"] = [{"citation_label": "CIT-009-01", "passage_id": "body"}]
    contract = {
        "original_task": "Explain Python asyncio event loops in concise Chinese.",
        "output_constraints": {"language": "zh"},
    }
    result = validate_citations(
        "Python asyncio 的事件循环是每个 asyncio 应用的核心：它运行异步任务与回调、执行网络 IO 操作并运行子进程 [CIT-009-01]。",
        bundle,
        writing_evidence=build_writing_evidence(bundle, contract),
        task_contract=contract,
        multilingual_llm_client=_AllMultilingualJudge(),
        min_supported_overlap=0.15,
        min_weak_overlap=0.05,
    )
    assert result.multilingual_adjudication["candidate_count"] == 1
    assert result.details[0].judgment_source == "multilingual_llm"
    assert result.supported == 1


def test_rendered_validation_thresholds_match_the_active_validation_policy():
    from app.evidence.citation_validator import render_citation_validation_section

    result = validate_citations(
        "The official body says the feature is stable [CIT-001-02].", _bundle(),
        min_supported_overlap=0.15, min_weak_overlap=0.05,
    )
    rendered = "\n".join(render_citation_validation_section(result))
    assert "15%" in rendered
    assert "5%" in rendered
    assert result.to_dict()["min_weak_overlap"] == 0.05
