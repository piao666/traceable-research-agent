"""End-to-end smoke for V2 provenance materialization and API access."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["AUTH_ENABLED"] = "false"
os.environ["EVIDENCE_PIPELINE_VERSION"] = "v2"
os.environ["REPORT_GENERATION_MODE"] = "deterministic"
os.environ["LLM_PLANNER_ENABLED"] = "false"
os.environ["EXTERNAL_TOOLS_DEFAULT_MODE"] = "mock"

from fastapi.testclient import TestClient

from app.evidence.artifact_store import ArtifactStore
from app.main import app


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={
                "task": "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report",
                "report_type": "summary",
                "source_mode": "mock",
                "allowed_tools": ["file_reader", "sql_query", "rag_search", "report_writer"],
                "execution_mode_override": "planned",
            },
        )
        assert_true(created.status_code == 200, created.text)
        run_id = created.json()["run_id"]
        completed = client.post(f"/api/tasks/{run_id}/run")
        assert_true(completed.status_code == 200, completed.text)
        assert_true(completed.json()["status"] == "completed", completed.text)

        v1 = client.get(f"/api/tasks/{run_id}/evidence")
        v2 = client.get(f"/api/tasks/{run_id}/evidence/v2")
        repeated = client.get(f"/api/tasks/{run_id}/evidence/v2")
        report = client.get(f"/api/reports/{run_id}")
        assert_true(v1.status_code == 200, v1.text)
        assert_true(v2.status_code == 200, v2.text)
        assert_true(repeated.status_code == 200, repeated.text)
        assert_true(report.status_code == 200 and report.json()["exists"], report.text)

        payload = v2.json()
        assert_true(payload["schema_version"] == "2.0", str(payload))
        assert_true(payload["status"] == "complete", str(payload))
        assert_true(payload["source_documents"], "source documents missing")
        assert_true(payload["source_snapshots"], "source snapshots missing")
        assert_true(payload["passages"], "passages missing")
        assert_true(payload["assertions"], "assertions missing")
        assert_true(payload["claims"], "claims missing")
        assert_true(payload["citations"], "citations missing")
        assert_true(payload["reasoning"]["status"] == "complete", str(payload["reasoning"]))
        assert_true(payload["reasoning"]["engine_version"] == "p2-rule-1", str(payload["reasoning"]))
        assert_true(payload["reliability_scores"], "reliability scores missing")
        assert_true(payload["resolutions"], "claim resolutions missing")
        assert_true(
            len(payload["reliability_scores"]) == len(payload["edges"]),
            "every edge must have a reliability score",
        )
        assert_true(payload["integrity"]["all_passages_resolve"], str(payload["integrity"]))
        assert_true(payload["integrity"]["all_assertions_resolve"], str(payload["integrity"]))
        assert_true(payload["integrity"]["all_edges_resolve"], str(payload["integrity"]))
        assert_true(payload["integrity"]["all_citations_resolve"], str(payload["integrity"]))
        assert_true(
            payload["citations"] == repeated.json()["citations"],
            "repeated materialization changed citation IDs",
        )

        snapshot = payload["source_snapshots"][0]
        artifact_store = ArtifactStore(ROOT / "workspace" / "artifacts")
        artifact_store.read_bytes(snapshot["artifact_path"], snapshot["content_hash"])
        markdown = report.json()["markdown"]
        assert_true("Claim Provenance V2" in markdown, "report provenance section missing")
        assert_true("可靠性、冲突与限制" in markdown, "report reasoning section missing")
        assert_true(
            payload["citations"][0]["citation_label"] in markdown,
            "report citation label missing",
        )

        print(
            {
                "provenance_v2": "ok",
                "run_id": run_id,
                "v1_compatible": True,
                "documents": len(payload["source_documents"]),
                "passages": len(payload["passages"]),
                "claims": len(payload["claims"]),
                "citations": len(payload["citations"]),
                "artifact_integrity": "ok",
                "citation_integrity": "ok",
                "idempotent": True,
            }
        )


if __name__ == "__main__":
    main()
