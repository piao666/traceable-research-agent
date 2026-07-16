"""P1 contracts for immutable artifacts and Claim-level provenance."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.agent.evidence import ClaimEvidenceMap, EvidenceBundle, EvidenceItem
from app.agent.reporter import _valid_synthesis_citations
from app.database import Base
from app.evidence.artifact_store import ArtifactIntegrityError, ArtifactStore
from app.evidence.normalizers import canonicalize_url, passage_locator
from app.evidence.service import materialize_provenance_bundle
from app.trace.models import AgentRun, ToolTrace
from scripts.migrate_database import P2_TABLES, V1_TABLES, V2_TABLES, bootstrap_revision_for_tables


class ArtifactStoreTests(unittest.TestCase):
    def test_content_addressing_is_idempotent_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            first = store.put_text('{"value": 1}')
            second = store.put_text('{"value": 1}')
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertEqual(first.artifact_path, second.artifact_path)
            self.assertEqual(store.read_text(first.artifact_path, first.content_hash), '{"value": 1}')

            with gzip.open(store.resolve(first.artifact_path), "wb") as handle:
                handle.write(b"tampered")
            with self.assertRaises(ArtifactIntegrityError):
                store.read_bytes(first.artifact_path, first.content_hash)

    def test_artifact_path_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            with self.assertRaises(ValueError):
                store.resolve("../outside.json.gz")


class SourceNormalizerTests(unittest.TestCase):
    def test_url_normalization_removes_fragment_tracking_and_default_port(self) -> None:
        normalized = canonicalize_url(
            "HTTPS://Example.COM:443/docs//guide/?utm_source=test&b=2&a=1#section"
        )
        self.assertEqual(normalized, "https://example.com/docs/guide?a=1&b=2")

    def _item(self, source_type: str, source_ref: str, metadata: dict | None = None) -> EvidenceItem:
        return EvidenceItem(
            evidence_id="E001",
            run_id="locator-run",
            trace_id="locator-trace",
            step_no=1,
            tool_name=f"{source_type}_tool",
            source_type=source_type,
            source_ref=source_ref,
            title="Locator source",
            snippet="Evidence text",
            status="success",
            confidence="high",
            metadata=metadata or {},
        )

    def test_rag_locator_preserves_document_chunk_and_offsets(self) -> None:
        locator = passage_locator(
            self._item(
                "rag",
                "handbook.pdf",
                {"chunk_id": "chunk-7", "hit_metadata": {"start": 120, "end": 245}},
            ),
            {},
        )
        self.assertEqual(locator["kind"], "rag")
        self.assertEqual(locator["document"], "handbook.pdf")
        self.assertEqual(locator["chunk_id"], "chunk-7")
        self.assertEqual((locator["start"], locator["end"]), (120, 245))

    def test_sql_locator_preserves_query_identity_and_row_shape(self) -> None:
        locator = passage_locator(
            self._item(
                "sql",
                "query result",
                {"table": "metrics", "columns": ["quarter", "revenue"], "row_key": "Q3"},
            ),
            {"query": "SELECT quarter, revenue FROM metrics", "database": "analytics"},
        )
        self.assertEqual(locator["kind"], "sql")
        self.assertEqual(locator["database"], "analytics")
        self.assertEqual(locator["table"], "metrics")
        self.assertEqual(locator["row_key"], "Q3")
        self.assertEqual(len(locator["query_hash"]), 64)

    def test_github_locator_takes_precedence_over_generic_web(self) -> None:
        locator = passage_locator(
            self._item(
                "github_commit",
                "https://github.com/acme/research/blob/abc123/app/main.py#L10-L12",
                {
                    "commit": "abc123",
                    "path": "app/main.py",
                    "line_start": 10,
                    "line_end": 12,
                },
            ),
            {},
        )
        self.assertEqual(locator["kind"], "github")
        self.assertEqual(locator["repository"], "acme/research")
        self.assertEqual(locator["commit"], "abc123")
        self.assertEqual(locator["path"], "app/main.py")
        self.assertEqual((locator["line_start"], locator["line_end"]), (10, 12))
        self.assertEqual(
            locator["url"],
            "https://github.com/acme/research/blob/abc123/app/main.py",
        )


class ProvenanceMaterializationTests(unittest.TestCase):
    def _engine(self, database_path: Path):
        engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        return engine

    def test_materialized_chain_is_complete_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory) / "provenance.sqlite")
            with Session(engine) as db:
                run = AgentRun(
                    run_id="p1-run",
                    task="Verify revenue growth",
                    report_type="summary",
                    source_mode="real",
                    status="completed",
                )
                trace = ToolTrace(
                    trace_id="p1-trace",
                    run_id=run.run_id,
                    step_no=1,
                    tool_name="tavily_search",
                    input_json=json.dumps({"query": "revenue"}),
                    output_json=json.dumps(
                        {"results": [{"url": "https://example.com/report"}]}
                    ),
                    output_summary="One source",
                    status="success",
                    created_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                )
                db.add_all([run, trace])
                db.commit()
                item = EvidenceItem(
                    evidence_id="E001",
                    run_id=run.run_id,
                    trace_id=trace.trace_id,
                    step_no=1,
                    tool_name="tavily_search",
                    source_type="web",
                    source_ref="https://example.com/report?utm_source=test#results",
                    title="Official report",
                    snippet="Revenue grew 10% in 2025 Q3.",
                    status="success",
                    confidence="high",
                    metadata={"data_source": "tavily_api"},
                )
                claim = ClaimEvidenceMap(
                    claim_id="C001",
                    claim="Revenue growth: 10% in 2025 Q3",
                    evidence_ids=[item.evidence_id],
                    support_level="high",
                )
                bundle = EvidenceBundle(
                    run_id=run.run_id,
                    task=run.task,
                    total_evidence_items=1,
                    source_groups=[],
                    claims=[claim],
                    evidence_items=[item],
                    unsupported_claims=[],
                )

                first = materialize_provenance_bundle(
                    db,
                    run,
                    bundle,
                    [trace],
                    ArtifactStore(Path(directory)),
                    extractor_version="test-v1",
                )
                second = materialize_provenance_bundle(
                    db,
                    run,
                    bundle,
                    [trace],
                    ArtifactStore(Path(directory)),
                    extractor_version="test-v1",
                )
            engine.dispose()

        self.assertEqual(first["schema_version"], "2.0")
        self.assertEqual(len(first["source_documents"]), 1)
        self.assertEqual(len(first["source_snapshots"]), 1)
        self.assertEqual(len(first["passages"]), 1)
        self.assertEqual(len(first["assertions"]), 1)
        self.assertEqual(len(first["claims"]), 1)
        self.assertEqual(len(first["edges"]), 1)
        self.assertEqual(len(first["citations"]), 1)
        self.assertTrue(first["integrity"]["all_passages_resolve"])
        self.assertTrue(first["integrity"]["all_assertions_resolve"])
        self.assertTrue(first["integrity"]["all_edges_resolve"])
        self.assertTrue(first["integrity"]["all_citations_resolve"])
        self.assertEqual(first["integrity"]["citation_coverage"], 1.0)
        self.assertEqual(first["source_documents"], second["source_documents"])
        self.assertEqual(first["citations"], second["citations"])
        self.assertEqual(first["passages"][0]["locator"]["url"], "https://example.com/report")
        self.assertEqual(first["assertions"][0]["value"], {"value": 10.0})
        self.assertEqual(first["assertions"][0]["unit"], "%")


class ReporterCitationTests(unittest.TestCase):
    def test_unknown_or_missing_citation_is_rejected(self) -> None:
        bundle = {"citations": [{"citation_label": "CIT-001-01"}]}
        self.assertTrue(_valid_synthesis_citations("Fact [CIT-001-01]", bundle))
        self.assertFalse(_valid_synthesis_citations("Fact without citation", bundle))
        self.assertFalse(_valid_synthesis_citations("Fact [CIT-999-01]", bundle))


class MigrationBootstrapTests(unittest.TestCase):
    def test_fresh_and_versioned_databases_need_no_bootstrap_stamp(self) -> None:
        self.assertIsNone(bootstrap_revision_for_tables(set()))
        self.assertIsNone(
            bootstrap_revision_for_tables(
                {"alembic_version", *V1_TABLES},
                current_revision="0001_initial_trace_schema",
            )
        )

    def test_empty_version_table_does_not_hide_legacy_schema(self) -> None:
        self.assertEqual(
            bootstrap_revision_for_tables({"alembic_version", *V1_TABLES}),
            "0001_initial_trace_schema",
        )

    def test_legacy_v1_and_complete_v2_databases_are_classified(self) -> None:
        self.assertEqual(
            bootstrap_revision_for_tables(set(V1_TABLES)),
            "0001_initial_trace_schema",
        )
        self.assertEqual(
            bootstrap_revision_for_tables(V1_TABLES | V2_TABLES),
            "0002_claim_provenance_schema",
        )
        self.assertEqual(
            bootstrap_revision_for_tables(V1_TABLES | V2_TABLES | P2_TABLES),
            "0003_evidence_reasoning",
        )

    def test_versioned_schema_ahead_state_is_reconciled(self) -> None:
        self.assertIsNone(
            bootstrap_revision_for_tables(
                V1_TABLES | V2_TABLES,
                current_revision="0002_claim_provenance_schema",
            )
        )
        self.assertEqual(
            bootstrap_revision_for_tables(
                V1_TABLES | V2_TABLES | P2_TABLES,
                current_revision="0002_claim_provenance_schema",
            ),
            "0003_evidence_reasoning",
        )
        with self.assertRaises(RuntimeError):
            bootstrap_revision_for_tables(
                V1_TABLES | V2_TABLES,
                current_revision="0003_evidence_reasoning",
            )

    def test_partial_legacy_schemas_are_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            bootstrap_revision_for_tables({"agent_runs"})
        with self.assertRaises(RuntimeError):
            bootstrap_revision_for_tables(V1_TABLES | {"source_documents"})
        with self.assertRaises(RuntimeError):
            bootstrap_revision_for_tables(V1_TABLES | V2_TABLES | {"claim_resolutions"})


if __name__ == "__main__":
    unittest.main()
