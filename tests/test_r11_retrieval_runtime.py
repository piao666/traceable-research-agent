"""R11 runtime configuration, Docker, and guarded real-smoke contracts."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

from app.agent.evidence import EvidenceItem
from app.config import Settings
from app.evidence.artifact_store import ArtifactStore
from app.evidence.service import _materialize_item
from app.trace.models import AgentRun, ToolTrace


ROOT = Path(__file__).resolve().parents[1]


def test_r11_environment_settings_are_bounded() -> None:
    with patch.dict(
        "os.environ",
        {
            "FETCH_ROUTER_ENABLED": "true",
            "FETCH_HTTP_ENABLED": "false",
            "FETCH_BROWSER_ENABLED": "true",
            "FETCH_REMOTE_EXTRACT_ENABLED": "false",
            "FETCH_BROWSER_TIMEOUT_SECONDS": "999",
            "FETCH_BROWSER_MAX_CONCURRENCY": "99",
            "FETCH_REMOTE_EXTRACT_PROVIDER_ORDER": "exa,firecrawl",
            "FETCH_QUALITY_MIN_SCORE": "1.5",
            "URL_CANONICALIZATION_ENABLED": "false",
            "CONTENT_DEDUP_ENABLED": "false",
        },
        clear=False,
    ):
        settings = Settings.from_env()
    assert settings.fetch_router_enabled is True
    assert settings.fetch_http_enabled is False
    assert settings.fetch_browser_enabled is True
    assert settings.fetch_remote_extract_enabled is False
    assert settings.fetch_browser_timeout_seconds == 120
    assert settings.fetch_browser_max_concurrency == 8
    assert settings.fetch_remote_extract_provider_order == "exa,firecrawl"
    assert settings.fetch_quality_min_score == 1.0
    assert settings.url_canonicalization_enabled is False
    assert settings.content_dedup_enabled is False


def test_advanced_environment_documents_r11_without_expanding_minimal_profile() -> None:
    full = (ROOT / ".env.example.full").read_text(encoding="utf-8")
    minimal = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "FETCH_ROUTER_ENABLED",
        "FETCH_HTTP_ENABLED",
        "FETCH_BROWSER_ENABLED",
        "FETCH_REMOTE_EXTRACT_ENABLED",
        "FETCH_BROWSER_TIMEOUT_SECONDS",
        "FETCH_BROWSER_MAX_CONCURRENCY",
        "FETCH_REMOTE_EXTRACT_PROVIDER_ORDER",
        "FETCH_QUALITY_MIN_SCORE",
        "URL_CANONICALIZATION_ENABLED",
        "CONTENT_DEDUP_ENABLED",
    ):
        assert f"{key}=" in full
        assert f"{key}=" not in minimal


def test_docker_installs_matching_playwright_chromium_runtime() -> None:
    requirements = (ROOT / "requirements" / "api.txt").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "playwright==1.62.0" in requirements
    assert "python -m playwright install --with-deps chromium" in dockerfile
    assert 'shm_size: "1gb"' in compose


def test_real_smoke_requires_explicit_confirmation() -> None:
    from scripts.validate_real_runtime import main

    assert main(["--r11-fetch-smoke"]) == 2


def test_source_snapshot_persists_fetch_and_extraction_metadata() -> None:
    run = AgentRun(
        run_id="r11-snapshot-run",
        task="fixture",
        report_type="summary",
        source_mode="real",
    )
    now = datetime.now(timezone.utc)
    trace = ToolTrace(
        trace_id="r11-snapshot-trace",
        run_id=run.run_id,
        step_no=1,
        tool_name="web_fetcher",
        input_json='{"urls":["https://example.com/raw"]}',
        output_json='{"pages":[]}',
        output_summary="fixture",
        status="success",
        created_at=now,
        finished_at=now,
    )
    metadata = {
        "requested_url": "https://example.com/raw?utm_source=x",
        "transport_url": "https://example.com/raw?utm_source=x",
        "final_url": "https://example.com/final",
        "canonical_url": "https://example.com/canonical",
        "provider": "local_http",
        "extraction_method": "beautifulsoup",
        "extraction_confidence": 0.7,
        "fetch_status": "success",
        "content_hash": "source-content-hash",
        "published_at": "2026-09-09",
        "redirect_chain": ["https://example.com/raw", "https://example.com/final"],
        "source_identity": {"independence_group": "srcgrp_fixture"},
    }
    item = EvidenceItem(
        evidence_id="E001",
        run_id=run.run_id,
        trace_id=trace.trace_id,
        step_no=1,
        tool_name="web_fetcher",
        source_type="web",
        source_ref=metadata["canonical_url"],
        title="Fixture",
        snippet="Traceable source evidence with a canonical identity.",
        status="success",
        confidence="high",
        metadata=metadata,
    )
    with tempfile.TemporaryDirectory() as directory:
        document, snapshot, _, _ = _materialize_item(
            run,
            item,
            1,
            trace,
            ArtifactStore(Path(directory)),
            "r11-test",
            4000,
        )
    document_metadata = json.loads(document.metadata_json)
    snapshot_metadata = json.loads(snapshot.metadata_json)
    assert document.canonical_uri == metadata["canonical_url"]
    assert document.provider == "local_http"
    assert document_metadata["source_identity"]["independence_group"] == "srcgrp_fixture"
    for key in (
        "requested_url",
        "transport_url",
        "final_url",
        "canonical_url",
        "provider",
        "extraction_method",
        "extraction_confidence",
        "fetch_status",
        "published_at",
        "redirect_chain",
        "source_identity",
    ):
        assert snapshot_metadata[key] == metadata[key]
