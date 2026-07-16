"""Verify indexed Claim evidence audit queries at 10,000 scored edges."""

from __future__ import annotations

import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Base
from app.evidence.models import (
    ClaimEvidenceEdge,
    EvidenceAssertion,
    EvidencePassage,
    EvidenceReasoningRun,
    EvidenceReliabilityScore,
    ResearchClaim,
    SourceDocument,
    SourceSnapshot,
)
from app.evidence.reasoning_service import get_claim_evidence_audit
from app.trace.models import AgentRun, ToolTrace


EVIDENCE_COUNT = 10_000


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    run_id = "p2-capacity"
    claim_id = "claim-capacity"
    reasoning_run_id = "reason-capacity"
    with Session(engine) as db:
        db.add(
            AgentRun(
                run_id=run_id,
                task="P2 capacity",
                report_type="summary",
                source_mode="mock",
                status="completed",
            )
        )
        db.add(
            ToolTrace(
                trace_id="trace-capacity",
                run_id=run_id,
                step_no=1,
                tool_name="capacity_fixture",
                status="success",
                created_at=now,
                finished_at=now,
            )
        )
        db.commit()
        db.add(
            SourceDocument(
                document_id="doc-capacity",
                run_id=run_id,
                source_type="web",
                canonical_uri="https://example.com/capacity",
                title="Capacity source",
                provider="fixture",
                organization="example.com",
                metadata_json="{}",
            )
        )
        db.flush()
        db.add(
            SourceSnapshot(
                snapshot_id="snap-capacity",
                document_id="doc-capacity",
                trace_id="trace-capacity",
                content_hash="a" * 64,
                artifact_path="aa/capacity.json.gz",
                content_type="application/json",
                fetched_at=now,
                extractor_version="capacity",
                metadata_json="{}",
            )
        )
        db.add(
            ResearchClaim(
                claim_id=claim_id,
                run_id=run_id,
                claim_text="Capacity claim",
                normalized_predicate="states",
                qualifier_json="{}",
                status="high",
                extractor_version="capacity",
            )
        )
        db.add(
            EvidenceReasoningRun(
                reasoning_run_id=reasoning_run_id,
                run_id=run_id,
                policy_version="capacity",
                policy_hash="b" * 64,
                status="complete",
            )
        )
        db.flush()

        passages = []
        assertions = []
        edges = []
        scores = []
        for ordinal in range(EVIDENCE_COUNT):
            suffix = f"{ordinal:05d}"
            passage_id = f"pass-{suffix}"
            assertion_id = f"assert-{suffix}"
            edge_id = f"edge-{suffix}"
            passages.append(
                {
                    "passage_id": passage_id,
                    "snapshot_id": "snap-capacity",
                    "trace_id": "trace-capacity",
                    "ordinal": ordinal,
                    "content_hash": f"{ordinal:064x}",
                    "text": f"Evidence {ordinal}",
                    "locator_json": "{}",
                    "metadata_json": "{}",
                    "created_at": now,
                }
            )
            assertions.append(
                {
                    "assertion_id": assertion_id,
                    "passage_id": passage_id,
                    "trace_id": "trace-capacity",
                    "object_text": f"Evidence {ordinal}",
                    "qualifier_json": "{}",
                    "polarity": "positive",
                    "extraction_confidence": 0.9,
                    "extractor_version": "capacity",
                    "created_at": now,
                }
            )
            edges.append(
                {
                    "edge_id": edge_id,
                    "claim_id": claim_id,
                    "assertion_id": assertion_id,
                    "relation": "supports",
                    "score": 0.8,
                    "created_at": now,
                }
            )
            scores.append(
                {
                    "score_id": f"score-{suffix}",
                    "reasoning_run_id": reasoning_run_id,
                    "edge_id": edge_id,
                    "claim_type": "generic",
                    "source_class": "official",
                    "source_cluster_id": f"cluster-{ordinal % 100}",
                    "authority": 0.9,
                    "traceability": 1.0,
                    "freshness": 1.0,
                    "relevance": 0.8,
                    "independence": 0.01,
                    "extraction_completeness": 0.9,
                    "total_score": 0.8,
                    "rationale_json": "{}",
                    "computed_at": now,
                }
            )
        db.execute(insert(EvidencePassage), passages)
        db.execute(insert(EvidenceAssertion), assertions)
        db.execute(insert(ClaimEvidenceEdge), edges)
        db.execute(insert(EvidenceReliabilityScore), scores)
        db.commit()

        durations = []
        for _ in range(7):
            started = time.perf_counter()
            rows = get_claim_evidence_audit(db, claim_id, reasoning_run_id)
            durations.append(time.perf_counter() - started)
            if len(rows) != EVIDENCE_COUNT:
                raise AssertionError(f"expected {EVIDENCE_COUNT} rows, got {len(rows)}")
        query_p95 = _p95(durations)
        if query_p95 >= 0.3:
            raise AssertionError(f"Claim evidence audit p95 {query_p95:.4f}s exceeded 0.3s")
    engine.dispose()
    print(
        {
            "reasoning_capacity": "ok",
            "evidence_count": EVIDENCE_COUNT,
            "query_p95_ms": round(query_p95 * 1000, 3),
            "target_ms": 300,
        }
    )


if __name__ == "__main__":
    main()
