"""Capacity check for V2 provenance writes and fixed-query graph reads."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.evidence import ClaimEvidenceMap, EvidenceBundle, EvidenceItem
from app.database import Base
from app.evidence.artifact_store import ArtifactStore
from app.evidence.service import get_provenance_bundle, materialize_provenance_bundle
from app.trace.models import AgentRun, ToolTrace


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def run_case(size: int) -> dict[str, float | int]:
    engine = _engine()
    run_id = f"capacity-{size}"
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as directory, Session(engine) as db:
        run = AgentRun(
            run_id=run_id,
            task=f"Capacity check with {size} evidence items",
            report_type="summary",
            source_mode="mock",
            status="completed",
        )
        traces: list[ToolTrace] = []
        items: list[EvidenceItem] = []
        evidence_ids: list[str] = []
        for ordinal in range(1, size + 1):
            trace_id = f"trace-{size}-{ordinal:04d}"
            evidence_id = f"E{ordinal:04d}"
            url = f"https://example.com/reports/{ordinal}"
            traces.append(
                ToolTrace(
                    trace_id=trace_id,
                    run_id=run_id,
                    step_no=ordinal,
                    tool_name="web_search",
                    input_json=json.dumps({"query": f"metric {ordinal}"}),
                    output_json=json.dumps({"url": url, "value": ordinal}),
                    output_summary=f"Metric {ordinal}: {ordinal}%",
                    status="success",
                    created_at=now,
                    finished_at=now,
                )
            )
            items.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    step_no=ordinal,
                    tool_name="web_search",
                    source_type="web",
                    source_ref=url,
                    title=f"Report {ordinal}",
                    snippet=f"Metric {ordinal} is {ordinal}% in 2025.",
                    status="success",
                    confidence="high",
                    metadata={"data_source": "capacity_fixture"},
                )
            )
            evidence_ids.append(evidence_id)

        bundle = EvidenceBundle(
            run_id=run_id,
            task=run.task,
            total_evidence_items=size,
            source_groups=[],
            claims=[
                ClaimEvidenceMap(
                    claim_id="C001",
                    claim="The fixture contains all capacity metrics",
                    evidence_ids=evidence_ids,
                    support_level="high",
                )
            ],
            evidence_items=items,
            unsupported_claims=[],
        )
        db.add(run)
        db.add_all(traces)
        db.commit()

        started = time.perf_counter()
        payload = materialize_provenance_bundle(
            db,
            run,
            bundle,
            traces,
            ArtifactStore(Path(directory)),
            extractor_version="capacity-v1",
        )
        write_seconds = time.perf_counter() - started
        if len(payload["passages"]) != size or len(payload["citations"]) != size:
            raise AssertionError(f"capacity graph is incomplete for {size} items")

        statement_count = 0
        counting = False

        @event.listens_for(engine, "before_cursor_execute")
        def count_statements(connection, cursor, statement, parameters, context, executemany) -> None:
            nonlocal statement_count
            if counting:
                statement_count += 1

        query_durations: list[float] = []
        query_counts: list[int] = []
        for _ in range(5):
            statement_count = 0
            counting = True
            query_started = time.perf_counter()
            repeated = get_provenance_bundle(db, run_id)
            query_durations.append(time.perf_counter() - query_started)
            counting = False
            query_counts.append(statement_count)
            if not repeated["integrity"]["all_citations_resolve"]:
                raise AssertionError(f"citation integrity failed for {size} items")

        max_query_count = max(query_counts)
        if max_query_count > 10:
            raise AssertionError(
                f"V2 graph read used {max_query_count} statements; expected at most 10"
            )
        return {
            "evidence_items": size,
            "write_seconds": round(write_seconds, 4),
            "query_p95_seconds": round(_percentile(query_durations, 0.95), 4),
            "query_statements": max_query_count,
        }


def main() -> None:
    results = [run_case(size) for size in (20, 100, 1000)]
    if len({result["query_statements"] for result in results}) != 1:
        raise AssertionError(f"query statement count changed with graph size: {results}")
    print({"provenance_capacity": "ok", "cases": results, "n_plus_one": False})


if __name__ == "__main__":
    main()
