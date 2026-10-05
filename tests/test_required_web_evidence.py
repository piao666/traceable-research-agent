from __future__ import annotations

from app.agent.evidence_requirements import assess_required_evidence


def _contract():
    return {
        "original_task": "Explain Python asyncio event loops.",
        "evidence_requirement": "substantive",
        "required_content_basis": ["full_text", "table", "structured"],
    }


def _persisted_bundle(passage, *, role="primary_content", metadata=None, title="Python asyncio"):
    return {
        "source_documents": [{"document_id": "d1", "title": title, "canonical_uri": "https://example.test/python", "metadata": {"evidence_role": role, **(metadata or {})}}],
        "source_snapshots": [{"snapshot_id": "s1", "document_id": "d1", "content_hash": "snapshot-hash"}],
        "passages": [{**passage, "snapshot_id": "s1", "content_hash": "passage-hash"}],
    }


def test_discovery_snippet_cannot_satisfy_substantive_requirement():
    assessment = assess_required_evidence(_contract(), _persisted_bundle({
        "passage_id": "p1", "text": "Python asyncio overview", "content_basis": "snippet_only",
        "metadata": {"evidence_role": "discovery_index"},
    }, role="discovery_index"))
    assert not assessment.passed
    assert assessment.gaps[0].code == "discovery_not_full_text"


def test_relevant_full_text_is_eligible():
    assessment = assess_required_evidence(_contract(), _persisted_bundle({
        "passage_id": "p1", "text": "Python asyncio uses an event loop to run asynchronous tasks.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }))
    assert assessment.passed
    assert assessment.eligible_passage_ids == ("p1",)


def test_unrelated_high_quality_demo_text_is_rejected():
    assessment = assess_required_evidence(_contract(), _persisted_bundle({
        "passage_id": "demo", "text": "A long unrelated local accounting document with many detailed rows.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Local accounting document"))
    assert not assessment.passed
    assert assessment.gaps[0].code == "task_relevant_evidence_missing"


def test_chinese_agent_evaluation_task_accepts_relevant_english_body():
    contract = {"original_task": "调研 Agent 框架评测方法与指标", "evidence_requirement": "substantive",
                "required_content_basis": ["full_text"]}
    assessment = assess_required_evidence(contract, _persisted_bundle({
        "passage_id": "p1", "text": "Evaluation and benchmarking of agent frameworks use task success metrics.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Agent evaluation methods"))
    assert assessment.passed

    unrelated = assess_required_evidence(contract, _persisted_bundle({
        "passage_id": "p2", "text": "A benchmark for mushroom growth measures yield.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Mushroom benchmark"))
    assert not unrelated.passed


def test_project_comparison_requires_real_local_docs_evidence_not_web_substitute():
    from app.agent.research_goal import build_task_contract

    task = "\u6bd4\u8f83\u76ee\u524dAgent\u9886\u57df\u7684\u4e3b\u6d41\u8bc4\u6d4b\u6846\u67b6\u3002\u4e0e\u672c\u9879\u76ee\u7684\u8bc4\u6d4b\u505a\u7efc\u5408\u5bf9\u6bd4\u5206\u6790\u3002"
    contract = build_task_contract(task)
    assert len(contract["evidence_scope_requirements"]) == 2
    web = _persisted_bundle({
        "passage_id": "web", "text": "Evaluation of Agent frameworks uses a benchmark and metrics.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Agent framework evaluation")
    web["source_documents"][0]["source_type"] = "web"
    assessment = assess_required_evidence(contract, web)
    assert not assessment.passed
    assert assessment.gaps[0].requirement_id == "local_project_evaluation"

    local = _persisted_bundle({
        "passage_id": "local", "text": "This project evaluates Agent research by citation accuracy.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Project evaluation")
    local["source_documents"][0].update(source_type="file", canonical_uri="file://evaluation.md",
        metadata={"evidence_role": "primary_content", "file_allowed_root": "/srv/workspace/docs",
                  "file_docs_root": "/srv/workspace/docs", "file_safe_path": True,
                  "file_approved_outside_allowed_roots": False})
    local["source_documents"][0]["document_id"] = "d2"
    local["source_snapshots"][0].update(snapshot_id="s2", document_id="d2")
    local["passages"][0]["snapshot_id"] = "s2"
    combined = {key: web[key] + local[key] for key in ("source_documents", "source_snapshots", "passages")}
    assert assess_required_evidence(contract, combined).passed


def test_muse_jev_impact_needs_both_named_impact_bodies():
    from app.agent.research_goal import build_task_contract

    contract = build_task_contract("\u5206\u6790Muse\u8f6f\u4ef6\u548cJEV\u6a21\u578b\u5bf9Agent\u7684\u5f71\u54cd")
    assert len(contract["evidence_scope_requirements"]) == 2
    muse = _persisted_bundle({
        "passage_id": "muse", "text": "Muse software has an impact on Agent workflows.",
        "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }, title="Muse Agent impact")
    muse["source_documents"][0]["source_type"] = "web"
    assessment = assess_required_evidence(contract, muse)
    assert not assessment.passed
    assert assessment.gaps[0].requirement_id == "jev_agent_impact"


def test_named_impact_requires_local_connection_in_body_not_page_title():
    from app.agent.research_goal import build_task_contract

    contract = build_task_contract("分析Muse软件和JEV模型对于Agent的影响")
    bundle = _persisted_bundle({
        "passage_id": "mixed",
        "text": "Muse downloads passed a milestone. " + "Background. " * 75
                + "Jev has implications for Agent routing decisions.",
        "content_basis": "partial", "metadata": {"evidence_role": "secondary_analysis"},
    }, role="secondary_analysis", title="Muse and Jev Agent impact")
    bundle["source_documents"][0]["source_type"] = "web"
    result = assess_required_evidence(contract, bundle)
    assert not result.passed
    assert {gap.requirement_id for gap in result.gaps} == {"muse_agent_impact"}


def test_named_agent_impact_accepts_body_describing_mechanism_without_impact_word():
    from app.agent.research_goal import build_task_contract

    contract = build_task_contract("Analyze Muse software and Jev model impact on Agent systems")
    contract["evidence_scope_requirements"] = [
        {"requirement_id": "jev_agent_impact", "entity": "Jev", "dimension": "Agent 影响",
         "match_mode": "all_components", "source_scope": "external_web"},
    ]
    bundle = _persisted_bundle({
        "passage_id": "jev", "text": "Jev routes Agent requests by scoring candidate tools and returns calibrated probabilities.",
        "content_basis": "partial", "metadata": {"evidence_role": "primary_content"},
    }, title="Jev decision model")
    bundle["source_documents"][0]["source_type"] = "web"
    assert assess_required_evidence(contract, bundle).eligible_passage_ids == ("jev",)

    unrelated = _persisted_bundle({
        "passage_id": "unrelated", "text": "A generic Agent routes requests by scoring tools.",
        "content_basis": "partial", "metadata": {"evidence_role": "primary_content"},
    }, title="Jev decision model")
    unrelated["source_documents"][0]["source_type"] = "web"
    assert not assess_required_evidence(contract, unrelated).passed


def test_partial_content_is_admitted_when_contract_allows_it():
    contract = _contract()
    contract["required_content_basis"] = ["full_text", "partial", "table", "structured"]
    assessment = assess_required_evidence(contract, _persisted_bundle({
        "passage_id": "partial", "text": "Python asyncio event loop reference excerpt.",
        "content_basis": "partial", "metadata": {"evidence_role": "primary_content"},
    }))
    assert assessment.passed


def test_chinese_scope_matches_linked_document_title_and_body():
    contract = {
        "original_task": "解释异步编程的事件循环机制",
        "evidence_requirement": "substantive",
        "required_content_basis": ["full_text"],
    }
    assessment = assess_required_evidence(contract, {
        "source_documents": [{"document_id": "d1", "title": "Python 异步编程事件循环", "canonical_uri": "https://example.test/a", "metadata": {"evidence_role": "primary_content"}}],
        "source_snapshots": [{"snapshot_id": "s1", "document_id": "d1"}],
        "passages": [{"passage_id": "p1", "snapshot_id": "s1", "text": "事件循环负责调度异步任务。", "content_basis": "full_text", "metadata": {}}],
    })
    assert assessment.passed


def test_each_contract_requirement_needs_its_own_matching_passage():
    contract = {
        "evidence_requirement": "substantive", "required_content_basis": ["full_text"],
        "requirements": [
            {"requirement_id": "r-a", "entity": "AlphaSystem", "dimension": "architecture", "mandatory": True},
            {"requirement_id": "r-b", "entity": "BetaSystem", "dimension": "sandbox", "mandatory": True},
        ],
    }
    assessment = assess_required_evidence(contract, _persisted_bundle({
        "passage_id": "p-a", "text": "AlphaSystem architecture uses a coordinator.", "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"},
    }))
    assert not assessment.passed
    assert assessment.counts["covered_requirements"] == 1
    assert assessment.gaps[0].requirement_id == "r-b"


def test_missing_persisted_snapshot_or_mock_body_cannot_satisfy_real_evidence():
    passage = {"passage_id": "p1", "text": "Python asyncio event loop.", "content_basis": "full_text", "metadata": {"evidence_role": "primary_content"}}
    assert assess_required_evidence(_contract(), {"passages": [passage]}).gaps[0].code == "evidence_provenance_unresolved"
    mocked = _persisted_bundle(passage, metadata={"is_mock": True})
    assert assess_required_evidence(_contract(), mocked).gaps[0].code == "demonstration_evidence_rejected"


def test_react_early_finish_uses_shared_relevance_gate():
    from app.agent.react_executor import _early_finish_rejection_reason

    plan = {"task_contract": _contract()}
    state = {"source_context": {"sources": [{
        "source_id": "S1", "url": "https://example.test/unrelated", "title": "Accounting guide",
        "snippet": "Detailed accounting rows with no asyncio content.", "fetch_status": "fetched",
        "content_basis": "full_text", "content_hash": "trace-backed-hash",
    }]}}
    reason = _early_finish_rejection_reason(plan, ["web_fetcher"], state)
    assert reason is not None
    assert "task_relevant_evidence_missing" in reason


def test_react_early_finish_uses_configured_official_domain_policy():
    from app.agent.react_executor import _early_finish_rejection_reason
    from app.config import Settings

    plan = {"task_contract": {
        **_contract(),
        "required_content_basis": ["full_text", "partial"],
        "source_constraints": {"official_only": True},
    }}
    settings = Settings(source_policy_path="config/evidence_policy.v3.json")
    source = {
        "source_id": "S1", "url": "https://docs.python.org/3/library/asyncio-eventloop.html",
        "title": "Python asyncio event loop", "snippet": "Python asyncio event loops run tasks.",
        "fetch_status": "fetched", "content_basis": "partial", "content_hash": "trace-backed-hash",
    }
    assert _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [source]}}, settings) is None

    lookalike = {**source, "url": "https://docs.python.org.evil.example/asyncio", "official": True}
    reason = _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [lookalike]}}, settings)
    assert reason is not None and "source_constraint_unmet" in reason

    snippet = {**source, "content_basis": "snippet_only"}
    reason = _early_finish_rejection_reason(plan, ["web_fetcher"], {"source_context": {"sources": [snippet]}}, settings)
    assert reason is not None and "discovery_not_full_text" in reason


def test_source_document_role_and_policy_official_class_are_authoritative():
    contract = {
        "original_task": "Explain Python asyncio event loops.",
        "evidence_requirement": "substantive",
        "required_content_basis": ["full_text"],
        "source_constraints": {"official_only": True},
    }
    bundle = {
        "source_documents": [{
            "document_id": "d1", "title": "Python asyncio event loop",
            "canonical_uri": "https://docs.python.org/asyncio",
            "metadata": {"evidence_role": "primary_content", "source_class": "official"},
        }],
        "source_snapshots": [{"snapshot_id": "s1", "document_id": "d1"}],
        "passages": [{
            "passage_id": "p1", "snapshot_id": "s1",
            "text": "Python asyncio event loops schedule asynchronous tasks.",
            "content_basis": "full_text",
            # A derived label must not be able to override the persisted parent.
            "metadata": {"evidence_role": "discovery_index"},
        }],
    }
    assessment = assess_required_evidence(contract, bundle)
    assert assessment.passed
    assert assessment.eligible_passage_ids == ("p1",)
