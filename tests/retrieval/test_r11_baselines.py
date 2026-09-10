"""Frozen R10 contracts and explicit legacy defects for the R11-R14 cutover."""

from __future__ import annotations

import inspect
from unittest.mock import patch

import httpx
import pytest

from app.config import Settings


def test_web_fetcher_legacy_tool_contract_is_frozen() -> None:
    from app.tools.web_fetcher import web_fetch

    html = "<html><title>Contract</title><main>" + ("verified source sentence. " * 20) + "</main></html>"
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                text=html,
                headers={"content-type": "text/html"},
            )
        )
    )
    try:
        with patch(
            "app.tools.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            result = web_fetch(
                {"urls": ["https://example.com/report"], "max_chars": 4000},
                client=client,
                settings_obj=Settings(
                    web_fetcher_cache_enabled=False,
                    web_fetcher_trafilatura_enabled=False,
                ),
            )
    finally:
        client.close()

    assert result.success is True
    assert set(result.output) == {"pages", "fetched_count", "failed_count", "total_count"}
    assert {"url", "title", "content", "content_basis", "extraction_method", "fetched_at_ms"}.issubset(
        result.output["pages"][0]
    )


def test_web_fetch_output_still_materializes_as_evidence_v2_item() -> None:
    from app.agent.evidence import _web_page_items

    record = {
        "trace_id": "trace-r11-baseline",
        "run_id": "run-r11-baseline",
        "step_no": 1,
        "tool_name": "web_fetcher",
        "success": True,
        "status": "success",
        "metadata": {},
        "output": {
            "pages": [
                {
                    "url": "https://example.com/report",
                    "title": "Contract",
                    "content": "Research evidence sentence with a stable source locator.",
                    "content_basis": "full_text",
                    "extraction_method": "beautifulsoup",
                    "extraction_confidence": 0.7,
                    "content_hash": "abc",
                }
            ]
        },
    }
    items = _web_page_items("run-r11-baseline", record, 0)
    assert len(items) == 1
    assert items[0].source_ref == "https://example.com/report"
    assert items[0].metadata["content_basis"] == "full_text"
    assert items[0].metadata["extraction_method"] == "beautifulsoup"


def test_legacy_deepening_no_longer_excludes_child_evidence() -> None:
    from app.agent.deepening import run_deepening

    source = inspect.getsource(run_deepening)
    assert "parent_observations" not in source
    assert 'not obs.get("metadata", {}).get("sub_run_id")' not in source


@pytest.mark.xfail(
    strict=True,
    reason="Legacy one-shot report context is capped at 7000 chars; R14 replaces it with section packs.",
)
def test_legacy_report_context_is_not_fixed_to_7000_characters() -> None:
    from app.agent.reporter import _provenance_llm_context

    passages = []
    report_claims = []
    citations = []
    for index in range(12):
        passage_id = f"pass-{index}"
        claim_id = f"claim-{index}"
        report_claim_id = f"report-{index}"
        passages.append(
            {
                "passage_id": passage_id,
                "trace_id": f"trace-{index}",
                "text": (f"unique evidence {index} " * 100),
                "locator": {"url": f"https://example.com/{index}"},
            }
        )
        report_claims.append(
            {
                "report_claim_id": report_claim_id,
                "claim_id": claim_id,
                "claim_text": f"Claim {index}",
            }
        )
        citations.append(
            {
                "report_claim_id": report_claim_id,
                "passage_id": passage_id,
                "citation_label": f"CIT-{index:03d}-01",
            }
        )
    rendered = _provenance_llm_context(
        {
            "schema_version": "v2",
            "passages": passages,
            "report_claims": report_claims,
            "citations": citations,
            "resolutions": [],
        }
    )
    assert len(rendered) > 7000
