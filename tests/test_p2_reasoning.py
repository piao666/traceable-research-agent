"""P2 golden contracts for source reliability and conflict reconciliation."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.evidence import ClaimEvidenceMap, EvidenceBundle, EvidenceItem
from app.agent.reporter import _conflict_alert_lines, _render_reasoning_markdown
from app.config import Settings
from app.database import Base
from app.evidence.artifact_store import ArtifactStore
from app.evidence.policy import (
    classify_claim,
    classify_source,
    load_source_policy,
    score_reliability,
    source_cluster_id,
)
from app.evidence.reasoning_service import materialize_reasoning
from app.evidence.service import get_provenance_bundle, materialize_provenance_bundle
from app.evidence.models import EvidenceReasoningRun
from app.trace.models import AgentRun, ToolTrace
from app.evidence.reasoning import (
    ScoredRelation,
    classify_relation,
    normalize_fact,
    parse_llm_relation,
    resolve_conflict,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "source_policy.v1.json"


class SourcePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_source_policy(POLICY_PATH)

    def test_claim_and_source_type_change_authority_prior(self) -> None:
        self.assertEqual(classify_claim("Q3 revenue increased 10%", self.policy), "financial")
        self.assertEqual(
            classify_source("web", "https://www.sec.gov/filing", {}, self.policy),
            "regulatory",
        )
        self.assertEqual(
            classify_source("web", "https://example.net/blog", {}, self.policy),
            "blog",
        )

    def test_invalid_policy_fails_during_settings_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid_policy = Path(directory) / "invalid-policy.json"
            invalid_policy.write_text(
                json.dumps(
                    {
                        "version": "invalid",
                        "weights": {},
                        "source_classes": {},
                        "claim_types": {"generic": {}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Invalid source policy"):
                Settings(source_policy_path=str(invalid_policy))

    def test_reliability_is_explainable_and_policy_driven(self) -> None:
        arguments = {
            "claim_text": "Q3 revenue increased 10%",
            "assertion_text": "Q3 revenue increased 10%",
            "source_type": "web",
            "canonical_uri": "https://www.sec.gov/filing",
            "organization": "sec.gov",
            "source_metadata": {},
            "passage_metadata": {"published_at": "2026-07-01T00:00:00+00:00"},
            "locator": {"url": "https://www.sec.gov/filing"},
            "trace_id": "trace-1",
            "snapshot_hash": "a" * 64,
            "fetched_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "extraction_confidence": 0.9,
            "polarity": "positive",
            "scalar_present": True,
            "source_cluster": "cluster-1",
            "cluster_size": 1,
            "now": datetime(2026, 7, 16, tzinfo=timezone.utc),
        }
        original = score_reliability(policy=self.policy, **arguments)
        self.assertGreater(original.total_score, 0.8)
        self.assertEqual(set(original.dimensions()), set(self.policy.weights))
        self.assertEqual(original.rationale["weights"], self.policy.weights)

        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        raw["version"] = "authority-only-test"
        raw["weights"] = {
            "authority": 1.0,
            "traceability": 0.0,
            "freshness": 0.0,
            "relevance": 0.0,
            "independence": 0.0,
            "extraction_completeness": 0.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            changed = score_reliability(policy=load_source_policy(path), **arguments)
        self.assertEqual(changed.total_score, changed.authority)
        self.assertNotEqual(changed.total_score, original.total_score)

        raw["version"] = "blocked-domain-test"
        raw["blocked_domains"] = ["sec.gov"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked-policy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            blocked = score_reliability(policy=load_source_policy(path), **arguments)
        self.assertEqual(blocked.total_score, 0.0)
        self.assertFalse(blocked.rationale["domain_allowed"])

    def test_reposted_content_has_one_cluster(self) -> None:
        passage_hash = "b" * 64
        clusters = {
            source_cluster_id(
                passage_hash=passage_hash,
                canonical_uri=f"https://mirror{index}.example/article",
                organization=f"mirror{index}.example",
                duplicate_passage_hashes={passage_hash},
            )
            for index in range(10)
        }
        self.assertEqual(len(clusters), 1)

    def test_mock_source_cannot_cross_policy_score_cap(self) -> None:
        score = score_reliability(
            claim_text="Product feature is available",
            assertion_text="Product feature is available",
            source_type="web",
            canonical_uri="https://example.com/feature",
            organization="example.com",
            source_metadata={"is_mock": True},
            passage_metadata={"published_at": "2026-07-16T00:00:00+00:00"},
            locator={"url": "https://example.com/feature"},
            trace_id="trace",
            snapshot_hash="c" * 64,
            fetched_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            extraction_confidence=1.0,
            polarity="positive",
            scalar_present=True,
            source_cluster="mock-cluster",
            cluster_size=1,
            policy=self.policy,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(score.source_class, "mock")
        self.assertLessEqual(score.total_score, 0.35)

    def test_same_organization_sources_share_independence_cluster(self) -> None:
        clusters = {
            source_cluster_id(
                passage_hash=f"{index:064x}",
                canonical_uri=f"https://reports.example.com/{index}",
                organization="reports.example.com",
                duplicate_passage_hashes=set(),
            )
            for index in range(3)
        }
        self.assertEqual(len(clusters), 1)


class FactNormalizationTests(unittest.TestCase):
    def test_numeric_conflict_is_refuted(self) -> None:
        claim = normalize_fact("2025 Q3 revenue increased 10%")
        assertion = normalize_fact("2025 Q3 revenue declined 5%")
        decision = classify_relation(claim, assertion)
        self.assertEqual(decision.relation, "refutes")

    def test_unit_conversion_prevents_false_conflict(self) -> None:
        claim = normalize_fact("2025 revenue was 1 亿元")
        assertion = normalize_fact("2025 revenue was 10000 万元")
        decision = classify_relation(claim, assertion)
        self.assertEqual(decision.relation, "supports")

    def test_different_time_scope_is_context_not_conflict(self) -> None:
        claim = normalize_fact("2025 Q3 revenue increased 10%")
        assertion = normalize_fact("2025 Q2 revenue declined 5%")
        decision = classify_relation(claim, assertion)
        self.assertEqual(decision.relation, "contextualizes")
        self.assertEqual(decision.scope_difference, "time")

    def test_unrelated_negative_text_does_not_create_false_refutation(self) -> None:
        claim = normalize_fact("Retrieve relevant chunks from the local RAG index")
        assertion = normalize_fact("The runtime does not bundle a large embedding model")
        decision = classify_relation(claim, assertion, prior_relation="supports")
        self.assertEqual(decision.relation, "supports")


class ConflictResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_source_policy(POLICY_PATH)

    def _relation(
        self,
        relation: str,
        score: float,
        cluster: str,
        source_class: str = "blog",
        **kwargs,
    ) -> ScoredRelation:
        return ScoredRelation(relation, score, cluster, source_class, **kwargs)

    def test_no_conflict_and_scope_resolution(self) -> None:
        no_conflict = resolve_conflict(
            [self._relation("supports", 0.8, "official")],
            self.policy,
        )
        scoped = resolve_conflict(
            [
                self._relation(
                    "contextualizes",
                    0.7,
                    "older-quarter",
                    scope_difference="time",
                )
            ],
            self.policy,
        )
        self.assertEqual(no_conflict.status, "no_conflict")
        self.assertEqual(scoped.status, "resolved_by_scope")

        supported_with_scope = resolve_conflict(
            [
                self._relation("supports", 0.8, "current"),
                self._relation(
                    "contextualizes",
                    0.7,
                    "older-quarter",
                    scope_difference="time",
                ),
            ],
            self.policy,
        )
        self.assertEqual(supported_with_scope.status, "resolved_by_scope")

    def test_sql_and_blog_conflict_resolves_by_authority(self) -> None:
        resolution = resolve_conflict(
            [
                self._relation("supports", 0.88, "sql", "governed_sql"),
                self._relation("refutes", 0.52, "blog", "blog"),
            ],
            self.policy,
        )
        self.assertEqual(resolution.status, "resolved_by_authority")

    def test_official_correction_resolves_by_recency(self) -> None:
        resolution = resolve_conflict(
            [
                self._relation("supports", 0.82, "old", "official"),
                self._relation(
                    "refutes",
                    0.86,
                    "correction",
                    "official",
                    is_correction=True,
                ),
            ],
            self.policy,
        )
        self.assertEqual(resolution.status, "resolved_by_recency")

    def test_two_high_quality_sides_require_human_and_cap_confidence(self) -> None:
        resolution = resolve_conflict(
            [
                self._relation("supports", 0.82, "source-a", "official"),
                self._relation("refutes", 0.80, "source-b", "official"),
            ],
            self.policy,
        )
        self.assertEqual(resolution.status, "requires_human")
        self.assertLessEqual(resolution.confidence, 0.45)

    def test_close_moderate_sources_remain_unresolved(self) -> None:
        resolution = resolve_conflict(
            [
                self._relation("supports", 0.65, "source-a", "news"),
                self._relation("refutes", 0.62, "source-b", "news"),
            ],
            self.policy,
        )
        self.assertEqual(resolution.status, "unresolved")
        self.assertLessEqual(resolution.confidence, 0.60)

    def test_reposts_do_not_inflate_independent_support(self) -> None:
        resolution = resolve_conflict(
            [self._relation("supports", 0.7, "same-article") for _ in range(10)],
            self.policy,
        )
        self.assertEqual(resolution.independent_support_count, 1)
        self.assertAlmostEqual(resolution.support_quality, 0.7)

    def test_invalid_llm_relation_schema_is_not_accepted(self) -> None:
        self.assertIsNone(parse_llm_relation({"relation": "choose_support", "rationale": "x"}))
        self.assertIsNone(parse_llm_relation({"relation": "supports"}))


class ReasoningPersistenceTests(unittest.TestCase):
    def test_scoring_resolution_quality_gate_and_report_contract(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, Session(engine) as db:
            run = AgentRun(
                run_id="p2-integration",
                task="Resolve the 2025 Q3 revenue change",
                report_type="summary",
                source_mode="real",
                status="completed",
            )
            traces = [
                ToolTrace(
                    trace_id="p2-sql-trace",
                    run_id=run.run_id,
                    step_no=1,
                    tool_name="sql_query",
                    input_json=json.dumps({"query": "SELECT growth FROM metrics"}),
                    output_json=json.dumps({"growth": "10%"}),
                    output_summary="2025 Q3 revenue increased 10%",
                    status="success",
                    created_at=now,
                    finished_at=now,
                ),
                ToolTrace(
                    trace_id="p2-blog-trace",
                    run_id=run.run_id,
                    step_no=2,
                    tool_name="web_search",
                    input_json=json.dumps({"query": "Q3 revenue"}),
                    output_json=json.dumps({"url": "https://blog.example/revenue"}),
                    output_summary="2025 Q3 revenue declined 5%",
                    status="success",
                    created_at=now,
                    finished_at=now,
                ),
            ]
            items = [
                EvidenceItem(
                    evidence_id="E001",
                    run_id=run.run_id,
                    trace_id=traces[0].trace_id,
                    step_no=1,
                    tool_name="sql_query",
                    source_type="sql",
                    source_ref="metrics row Q3",
                    title="Governed metrics",
                    snippet="2025 Q3 revenue increased 10%",
                    status="success",
                    confidence="high",
                    metadata={"table": "metrics", "row_key": "2025-Q3"},
                ),
                EvidenceItem(
                    evidence_id="E002",
                    run_id=run.run_id,
                    trace_id=traces[1].trace_id,
                    step_no=2,
                    tool_name="web_search",
                    source_type="web",
                    source_ref="https://blog.example/revenue",
                    title="Industry blog",
                    snippet="2025 Q3 revenue declined 5%",
                    status="success",
                    confidence="high",
                    metadata={},
                ),
            ]
            bundle = EvidenceBundle(
                run_id=run.run_id,
                task=run.task,
                total_evidence_items=2,
                source_groups=[],
                claims=[
                    ClaimEvidenceMap(
                        claim_id="C001",
                        claim="2025 Q3 revenue increased 10%",
                        evidence_ids=["E001", "E002"],
                        support_level="high",
                    )
                ],
                evidence_items=items,
                unsupported_claims=[],
            )
            db.add(run)
            db.add_all(traces)
            db.commit()
            materialize_provenance_bundle(
                db,
                run,
                bundle,
                traces,
                ArtifactStore(Path(directory)),
                extractor_version="p2-test",
            )
            first = materialize_reasoning(db, run.run_id, POLICY_PATH)
            second = materialize_reasoning(db, run.run_id, POLICY_PATH)
            raw_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            raw_policy["version"] = "source-policy-v2-test"
            raw_policy["weights"] = {
                "authority": 0.30,
                "traceability": 0.20,
                "freshness": 0.15,
                "relevance": 0.15,
                "independence": 0.10,
                "extraction_completeness": 0.10,
            }
            changed_policy_path = Path(directory) / "source-policy-v2-test.json"
            changed_policy_path.write_text(json.dumps(raw_policy), encoding="utf-8")
            changed = materialize_reasoning(db, run.run_id, changed_policy_path)
            reasoning_run_count = len(
                list(
                    db.scalars(
                        select(EvidenceReasoningRun).where(
                            EvidenceReasoningRun.run_id == run.run_id
                        )
                    )
                )
            )
            payload = get_provenance_bundle(
                db,
                run.run_id,
                reasoning_run_id=first["reasoning"]["reasoning_run_id"],
            )

        self.assertEqual(len(first["reliability_scores"]), 2)
        self.assertEqual(first["reasoning"]["engine_version"], "p2-rule-1")
        self.assertEqual(first["reliability_scores"], second["reliability_scores"])
        self.assertNotEqual(
            first["reasoning"]["reasoning_run_id"],
            changed["reasoning"]["reasoning_run_id"],
        )
        self.assertEqual(reasoning_run_count, 2)
        self.assertEqual({edge["relation"] for edge in payload["edges"]}, {"supports", "refutes"})
        resolution = first["resolutions"][0]
        self.assertEqual(resolution["status"], "resolved_by_authority")
        self.assertFalse(resolution["rationale"]["quality_gate"]["passed"])
        self.assertLessEqual(resolution["confidence"], 0.69)
        dimensions = first["reliability_scores"][0]["dimensions"]
        self.assertEqual(len(dimensions), 6)

    def test_unresolved_conflict_is_visible_in_answer_and_limit_section(self) -> None:
        bundle = {
            "reasoning": {"policy_version": "test", "policy_hash": "abc"},
            "claims": [{"claim_id": "claim-1", "claim_text": "Revenue changed"}],
            "edges": [],
            "reliability_scores": [],
            "resolutions": [
                {
                    "claim_id": "claim-1",
                    "status": "unresolved",
                    "confidence": 0.6,
                    "independent_support_count": 1,
                    "independent_refute_count": 1,
                    "rationale": {"quality_gate": {"passed": False}},
                }
            ],
        }
        answer_alert = "\n".join(_conflict_alert_lines(bundle))
        limitation_section = "\n".join(_render_reasoning_markdown(bundle))
        self.assertIn("不得作为确定性事实", answer_alert)
        self.assertIn("冲突尚未解决", limitation_section)
        self.assertIn("not_passed", limitation_section)


if __name__ == "__main__":
    unittest.main()
