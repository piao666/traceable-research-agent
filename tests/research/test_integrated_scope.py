from __future__ import annotations

import asyncio
import json

from sqlalchemy import select
from unittest.mock import patch

from app.agent.evidence_exporter import resolve_export_path
from app.agent.outcome import load_observations
from app.api.tasks import (
    _export_run_evidence,
    get_task_result_context,
    get_task_result_evidence,
    get_task_result_trace,
)
from app.evidence.citation_validator import get_report_occurrence_bundle
from app.evidence.models import (
    EvidenceAssertion,
    EvidencePassage,
    ReportRevision,
    ResearchClaim,
    SourceDocument,
    SourceSnapshot,
)
from app.evidence.reference_verifier import extract_cited_academic_references
from app.evidence.scope_service import get_scope_provenance_bundle
from app.evidence.service import materialize_execution_provenance
from app.eval.fake_react_llm import FakeReActLLMClient
from app.improvement.evaluator import auto_evaluate_and_log
from app.reporting.integrity import assess_report_integrity
from app.research.node_executor import ResearchNodeExecutor
from app.research.orchestrator import run_deep_research_v2
from app.research.scope import list_scope_nodes, resolve_research_scope
from app.trace import store

from .conftest import add_web_trace, create_root


def _run_evidence_rows(db, run_id: str):
    assertion = db.scalars(
        select(EvidenceAssertion)
        .join(EvidencePassage, EvidenceAssertion.passage_id == EvidencePassage.passage_id)
        .join(SourceSnapshot, EvidencePassage.snapshot_id == SourceSnapshot.snapshot_id)
        .join(SourceDocument, SourceSnapshot.document_id == SourceDocument.document_id)
        .where(SourceDocument.run_id == run_id)
    ).first()
    passage = db.get(EvidencePassage, assertion.passage_id)
    snapshot = db.get(SourceSnapshot, passage.snapshot_id)
    document = db.get(SourceDocument, snapshot.document_id)
    claim = db.scalars(select(ResearchClaim).where(ResearchClaim.run_id == run_id)).first()
    return claim, assertion, passage, document


def _set_numeric_fact(db, run_id: str, value: int) -> None:
    claim, assertion, _passage, _document = _run_evidence_rows(db, run_id)
    text = f"Market size in 2025 is {value} USD."
    claim.claim_text = text
    claim.value_json = json.dumps({"value": value})
    claim.unit = "USD"
    claim.time_scope = "2025"
    assertion.object_text = text
    assertion.value_json = json.dumps({"value": value})
    assertion.unit = "USD"
    assertion.time_scope = "2025"
    assertion.polarity = "positive"
    db.commit()


def _set_reuters_identity(db, run_id: str) -> None:
    _claim, _assertion, _passage, document = _run_evidence_rows(db, run_id)
    metadata = json.loads(document.metadata_json or "{}")
    metadata["source_identity"] = {
        "canonical_story_hash": "reuters-integrated-story",
        "independence_group": "reuters-integrated-story",
    }
    metadata["evidence_role"] = "secondary_analysis"
    document.metadata_json = json.dumps(metadata, sort_keys=True)
    db.commit()


def _set_academic_identity(db, run_id: str) -> None:
    _claim, _assertion, passage, document = _run_evidence_rows(db, run_id)
    document.source_type = "academic_paper"
    document.canonical_uri = "https://doi.org/10.1234/integrated.2026"
    document.title = "Integrated Scope Study"
    metadata = json.loads(document.metadata_json or "{}")
    metadata.update(
        {
            "evidence_role": "official_metadata",
            "external_ids": {"DOI": "10.1234/integrated.2026"},
            "authors": ["Example, Ada"],
            "year": 2026,
            "venue": "Journal of Integrated Research",
            "research_eligible": True,
        }
    )
    document.metadata_json = json.dumps(metadata, sort_keys=True)
    passage_metadata = json.loads(passage.metadata_json or "{}")
    passage_metadata["evidence_role"] = "official_metadata"
    passage.metadata_json = json.dumps(passage_metadata, sort_keys=True)
    db.commit()


def test_integrated_scope_result_governance_chain(db, r12_settings):
    """Exercise the mandatory Root + Child A/B/C R12.1 integration fixture."""

    root = create_root(db, "Integrated research result governance fixture")
    child_run_ids: dict[str, str] = {}

    def runner(session, run_id, settings, _client):
        run = store.mark_agent_run_running_unless_cancelled(session, run_id)
        if run_id == root.run_id:
            text, suffix = "Market size in 2025 is 100 USD.", "shared-market-source"
        elif run.task == "Child A numeric conflict":
            text, suffix = "Market size in 2025 is 130 USD.", "shared-market-source"
        elif run.task == "Child B Reuters syndication":
            text, suffix = "Reuters published the verified market context for 2025.", "reuters-copy"
        else:
            text, suffix = (
                "Integrated Scope Study was published in 2026 with DOI 10.1234/integrated.2026.",
                "academic-doi",
            )
        add_web_trace(session, run_id, text, suffix)
        traces = store.list_tool_traces(session, run_id)
        materialize_execution_provenance(
            session,
            run,
            json.loads(run.plan_json or "{}"),
            load_observations(traces),
            traces,
            settings,
        )
        if run_id == root.run_id:
            _set_numeric_fact(session, run_id, 100)
            return {"run_id": run_id, "status": "running"}
        child_run_ids[run.task] = run_id
        if run.task == "Child A numeric conflict":
            _set_numeric_fact(session, run_id, 130)
        elif run.task == "Child B Reuters syndication":
            _set_reuters_identity(session, run_id)
        else:
            _set_academic_identity(session, run_id)
        store.update_agent_run_status(session, run_id, "completed", None)
        return {"run_id": run_id, "status": "completed"}

    def branch_planner(_client, **kwargs):
        if kwargs["depth"] == 1:
            return {
                "branches": [
                    {
                        "topic": "numeric conflict",
                        "query": "Child A numeric conflict",
                        "research_goal": "Test same-source numeric conflict",
                        "node_type": "contradiction_check",
                        "priority": 1,
                        "required": True,
                    },
                    {
                        "topic": "Reuters syndication",
                        "query": "Child B Reuters syndication",
                        "research_goal": "Preserve syndicated-source identity",
                        "node_type": "verification",
                        "priority": 2,
                        "required": True,
                    },
                    {
                        "topic": "academic DOI",
                        "query": "Child C academic DOI",
                        "research_goal": "Preserve academic work identity",
                        "node_type": "query",
                        "priority": 3,
                        "required": True,
                    },
                ],
                "is_comprehensive": False,
            }
        return {"branches": [], "is_comprehensive": True}

    def report_generator(_run, _plan, _observations, _traces, **kwargs):
        bundle = kwargs["provenance_bundle"]
        lines = ["# Integrated Scope Report", "", "## 3. 最终回答", ""]
        for passage in bundle["passages"]:
            citation = next(
                item for item in bundle["citations"] if item["passage_id"] == passage["passage_id"]
            )
            lines.append(f"{passage['text']} [{citation['citation_label']}]")
            if passage["origin_run_id"] == child_run_ids["Child C academic DOI"]:
                lines.append(f"{passage['text']} [{citation['citation_label']}]")
        return "\n\n".join(lines)

    settings = r12_settings.model_copy(update={"reference_verification_enabled": False})
    with (
        patch("app.research.orchestrator.run_react_task", side_effect=runner),
        patch("app.research.orchestrator.save_report", return_value="workspace/reports/integrated.md"),
    ):
        result = run_deep_research_v2(
            db,
            root.run_id,
            settings,
            FakeReActLLMClient([]),
            branch_planner=branch_planner,
            node_executor=ResearchNodeExecutor(runner=runner),
            report_generator=report_generator,
        )

    assert result["status"] == "completed"
    scope = resolve_research_scope(db, root.run_id)
    assert scope.status == "completed"
    assert len(list_scope_nodes(db, scope.scope_id)) == 4
    bundle = get_scope_provenance_bundle(db, scope)

    assert bundle["metrics"] == {
        "raw_source_count": 4,
        "effective_unique_source_count": 3,
        "raw_passage_count": 4,
        "effective_unique_passage_count": 4,
    }
    shared_source = next(
        item
        for item in bundle["scope_identity"]["source_aliases"]
        if set(item["origin_run_ids"])
        == {root.run_id, child_run_ids["Child A numeric conflict"]}
    )
    assert len(shared_source["member_document_ids"]) == 2
    assert all(
        len(item["member_passage_ids"]) == 1
        for item in bundle["scope_identity"]["passage_aliases"]
    )
    assert any(
        item["identity_key"] == "story:reuters-integrated-story"
        for item in bundle["scope_identity"]["source_aliases"]
    )

    conflict = next(
        item
        for item in bundle["scope_claim_groups"]
        if {member["origin_run_id"] for member in item["members"]}
        == {root.run_id, child_run_ids["Child A numeric conflict"]}
    )
    resolution = next(
        item for item in bundle["scope_resolutions"] if item["group_id"] == conflict["group_id"]
    )
    assert {item["relation"] for item in resolution["rationale"]["relations"]} == {
        "supports",
        "refutes",
    }

    revision = db.scalars(
        select(ReportRevision).where(ReportRevision.root_run_id == root.run_id)
    ).one()
    occurrences = get_report_occurrence_bundle(db, revision.report_revision_id)
    assert len(occurrences["citation_occurrences"]) == 5
    assert all(item["verdict"] == "supported" for item in occurrences["citation_occurrences"])
    assert {
        item["origin_run_id"] for item in occurrences["citation_occurrences"]
    } == {root.run_id, *child_run_ids.values()}
    integrity = assess_report_integrity(occurrences)
    assert integrity.status == "passed"

    cited_labels = {
        item["citation_label"] for item in occurrences["citation_occurrences"]
    }
    academic = extract_cited_academic_references(bundle, cited_labels)
    assert len(academic) == 1
    assert academic[0]["doi"] == "10.1234/integrated.2026"

    context = asyncio.run(get_task_result_context(root.run_id, db))
    evidence = asyncio.run(get_task_result_evidence(root.run_id, db))
    traces = asyncio.run(get_task_result_trace(root.run_id, db))
    assert context.scope_id == scope.scope_id
    assert set(context.member_run_ids) == {root.run_id, *child_run_ids.values()}
    assert {item["origin_run_id"] for item in evidence.passages} == {
        root.run_id,
        *child_run_ids.values(),
    }
    assert {item.origin_run_id for item in traces} == {root.run_id, *child_run_ids.values()}

    exported = _export_run_evidence(db, root.run_id, "json")
    export_path = resolve_export_path(exported.export_path)
    try:
        export_payload = json.loads(export_path.read_text(encoding="utf-8"))
        assert {item["origin_run_id"] for item in export_payload["passages"]} == {
            root.run_id,
            *child_run_ids.values(),
        }
    finally:
        export_path.unlink(missing_ok=True)

    improvement = auto_evaluate_and_log(db, root.run_id)
    assert improvement is not None
    assert improvement.execution_mode == "deep_research_v2"
    improvement_metadata = json.loads(improvement.evaluation_metadata_json)
    assert improvement_metadata["result_scope"] == "research_scope"
    assert improvement_metadata["scope_id"] == scope.scope_id
    assert improvement_metadata["effective_source_count"] == 3
    assert improvement_metadata["effective_passage_count"] == 4
