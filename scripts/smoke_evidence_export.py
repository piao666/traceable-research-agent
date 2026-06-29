"""Smoke checks for Day42/Day43 evidence export."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.agent.executor import run_plan
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.tools.base import ToolResult
from app.trace import store
from app.trace.logger import record_tool_result
from scripts.smoke_evidence_aggregation import (
    assert_true,
    create_run,
    multi_source_plan,
    prepare_runtime,
    test_fallback_trace_bundle,
    test_remote_failure_bundle,
)


def _export(client: TestClient, run_id: str, export_format: str) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{run_id}/evidence/export?format={export_format}")
    assert_true(
        response.status_code == 200,
        f"export {export_format} failed: {response.status_code} {response.text}",
    )
    payload = response.json()
    path = ROOT / payload["export_path"]
    assert_true(path.exists() and path.is_file(), f"export file missing: {path}")
    assert_true(
        path.resolve().is_relative_to((ROOT / "workspace" / "exports").resolve()),
        f"export escaped workspace/exports: {path}",
    )
    return payload


def _content(client: TestClient, run_id: str, export_format: str) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{run_id}/evidence/export/content?format={export_format}")
    assert_true(
        response.status_code == 200,
        f"content {export_format} failed: {response.status_code} {response.text}",
    )
    payload = response.json()
    path = ROOT / payload["export_path"]
    assert_true(path.exists() and path.is_file(), f"content export file missing: {path}")
    assert_true(
        path.resolve().is_relative_to((ROOT / "workspace" / "exports").resolve()),
        f"content export escaped workspace/exports: {path}",
    )
    assert_true(payload["content"] == path.read_text(encoding="utf-8"), "content payload mismatch")
    assert_true(payload["content_type"], "content_type missing")
    return payload


def _download(client: TestClient, run_id: str, export_format: str) -> bytes:
    response = client.get(f"/api/tasks/{run_id}/evidence/export/download?format={export_format}")
    assert_true(
        response.status_code == 200,
        f"download {export_format} failed: {response.status_code} {response.text}",
    )
    disposition = response.headers.get("content-disposition", "")
    assert_true("attachment" in disposition.lower(), "download missing attachment disposition")
    assert_true(
        f"evidence_{run_id}" in disposition,
        f"download filename missing run id: {disposition}",
    )
    assert_true(response.content is not None, "download response missing content")
    return response.content


def _read_export(payload: dict[str, Any]) -> str:
    return (ROOT / payload["export_path"]).read_text(encoding="utf-8")


def test_completed_run_exports(client: TestClient, db) -> str:
    run = create_run(db, multi_source_plan())
    summary = run_plan(db, run.run_id)
    assert_true(summary["status"] == "completed", "completed export run failed")

    json_export = _export(client, run.run_id, "json")
    json_payload = json.loads(_read_export(json_export))
    assert_true(json_payload["run_id"] == run.run_id, "JSON export run_id mismatch")
    assert_true(json_payload["evidence_items"], "JSON export missing evidence items")
    assert_true(any(item["is_mock"] for item in json_payload["evidence_items"]), "mock marker lost")
    json_content = _content(client, run.run_id, "json")
    assert_true(json.loads(json_content["content"])["run_id"] == run.run_id, "JSON content invalid")
    assert_true(json_content["content_type"] == "application/json", "JSON content type mismatch")

    jsonl_export = _export(client, run.run_id, "jsonl")
    jsonl_lines = [line for line in _read_export(jsonl_export).splitlines() if line.strip()]
    assert_true(len(jsonl_lines) == jsonl_export["item_count"], "JSONL item count mismatch")
    assert_true(all(json.loads(line).get("evidence_id") for line in jsonl_lines), "invalid JSONL evidence")
    jsonl_content = _content(client, run.run_id, "jsonl")
    assert_true(jsonl_content["content_type"] == "application/x-ndjson", "JSONL content type mismatch")
    assert_true(
        len([line for line in jsonl_content["content"].splitlines() if line.strip()])
        == jsonl_content["item_count"],
        "JSONL content item count mismatch",
    )

    markdown_export = _export(client, run.run_id, "markdown")
    markdown = _read_export(markdown_export)
    assert_true("# Evidence Packet" in markdown, "Markdown export missing title")
    assert_true("Claim-Evidence Map" in markdown, "Markdown export missing claim map")
    markdown_content = _content(client, run.run_id, "markdown")
    assert_true("# Evidence Packet" in markdown_content["content"], "Markdown content missing title")
    assert_true(markdown_content["content_type"] == "text/markdown", "Markdown content type mismatch")

    assert_true(json.loads(_download(client, run.run_id, "json")), "JSON download invalid")
    assert_true(_download(client, run.run_id, "jsonl").decode("utf-8").strip(), "JSONL download empty")
    assert_true(
        "# Evidence Packet" in _download(client, run.run_id, "markdown").decode("utf-8"),
        "Markdown download invalid",
    )
    return run.run_id


def test_empty_trace_export(client: TestClient, db) -> str:
    run = store.create_agent_run(
        db,
        "Export an empty evidence bundle without trace rows.",
        "summary",
        "mock",
        [],
    )
    payload = _export(client, run.run_id, "json")
    exported = json.loads(_read_export(payload))
    assert_true(exported["total_evidence_items"] == 0, "empty trace export should have zero items")
    content = _content(client, run.run_id, "markdown")
    assert_true("No structured evidence items" in content["content"], "empty trace content missing")
    return run.run_id


def test_fallback_export(client: TestClient, db) -> str:
    run_id = test_fallback_trace_bundle(client, db)
    payload = _export(client, run_id, "json")
    exported = json.loads(_read_export(payload))
    assert_true(
        any(item["is_fallback"] for item in exported["evidence_items"]),
        "fallback marker lost in export",
    )
    content = _content(client, run_id, "json")
    assert_true(
        any(item["is_fallback"] for item in json.loads(content["content"])["evidence_items"]),
        "fallback marker lost in content",
    )
    return run_id


def test_remote_failure_export(client: TestClient, db) -> str:
    run_id = test_remote_failure_bundle(client, db)
    payload = _export(client, run_id, "json")
    exported = json.loads(_read_export(payload))
    remote_failed = [
        item
        for item in exported["evidence_items"]
        if item["metadata"].get("tool_source") == "mcp_remote" and item.get("unsupported_reason")
    ]
    assert_true(remote_failed, "remote MCP failure evidence missing from export")
    content = _content(client, run_id, "markdown")
    assert_true("mcp_remote" in content["content"], "remote MCP failure missing from content")
    return run_id


def test_secret_filter_export(client: TestClient, db) -> str:
    plan = {
        "version": "day42-secret-smoke",
        "task": "Export sanitized evidence metadata.",
        "source_mode": "mock",
        "allowed_tools": ["mcp_github_search"],
        "planner_source": "smoke",
        "execution_mode": "planned",
        "steps": [
            {
                "step_no": 1,
                "goal": "Capture evidence with sensitive metadata.",
                "tool_name": "mcp_github_search",
                "arguments": {"query": "sanitized evidence"},
                "expected_output": "Sanitized evidence.",
                "completion_criteria": "Export removes secret-bearing fields.",
                "risk_level": "low",
                "requires_confirmation": False,
            }
        ],
        "notes": [],
    }
    run = create_run(db, plan)
    record_tool_result(
        db,
        run.run_id,
        1,
        "mcp_github_search",
        {"query": "sanitized evidence"},
        ToolResult(
            success=True,
            output={
                "results": [
                    {
                        "full_name": "demo/sanitized",
                        "url": "https://github.com/demo/sanitized",
                        "description": "Token-shaped value sk-secretvalue123456 should be redacted.",
                    }
                ],
            },
            output_summary="mcp_github_search returned sanitized mock results.",
            metadata={
                "tool_source": "local",
                "data_source": "mock",
                "api_key": "sk-secretvalue123456",
                "token": "ghp_secretvalue123456",
            },
        ),
        latency_ms=0,
    )
    payload = _export(client, run.run_id, "json")
    text = _read_export(payload).lower()
    assert_true("api_key" not in text, "api_key field leaked into export")
    assert_true("ghp_secretvalue" not in text, "token value leaked into export")
    assert_true("sk-secretvalue" not in text, "secret-shaped value leaked into export")
    content = _content(client, run.run_id, "json")
    content_text = content["content"].lower()
    assert_true("api_key" not in content_text, "api_key field leaked into content")
    assert_true("ghp_secretvalue" not in content_text, "token value leaked into content")
    assert_true("sk-secretvalue" not in content_text, "secret-shaped value leaked into content")
    return run.run_id


def test_missing_run_export_404(client: TestClient) -> None:
    missing_run_id = "missing-day43-evidence-export-run"
    for path in (
        f"/api/tasks/{missing_run_id}/evidence/export?format=json",
        f"/api/tasks/{missing_run_id}/evidence/export/content?format=json",
        f"/api/tasks/{missing_run_id}/evidence/export/download?format=json",
    ):
        response = client.get(path)
        assert_true(response.status_code == 404, f"missing run should return 404 for {path}")


def main() -> None:
    original = {
        "parallel_execution_enabled": settings.parallel_execution_enabled,
        "llm_planner_enabled": settings.llm_planner_enabled,
        "llm_planner_mode": settings.llm_planner_mode,
    }
    try:
        prepare_runtime()
        settings.parallel_execution_enabled = False
        settings.llm_planner_enabled = False
        settings.llm_planner_mode = "deterministic"
        with TestClient(app) as client:
            with SessionLocal() as db:
                test_completed_run_exports(client, db)
                test_empty_trace_export(client, db)
                test_fallback_export(client, db)
                test_remote_failure_export(client, db)
                test_secret_filter_export(client, db)
                test_missing_run_export_404(client)
        print(
            json.dumps(
                {
                    "evidence_export": "ok",
                    "json_export": "ok",
                    "jsonl_export": "ok",
                    "markdown_export": "ok",
                    "content_preview_api": "ok",
                    "download_api": "ok",
                    "empty_trace_export": "ok",
                    "mock_and_fallback_preserved": "ok",
                    "remote_mcp_failure_visible": "ok",
                    "exports_path_guard": "ok",
                    "secret_filter": "ok",
                    "missing_run_404": "ok",
                },
                indent=2,
            )
        )
    finally:
        settings.parallel_execution_enabled = original["parallel_execution_enabled"]
        settings.llm_planner_enabled = original["llm_planner_enabled"]
        settings.llm_planner_mode = original["llm_planner_mode"]


if __name__ == "__main__":
    main()
