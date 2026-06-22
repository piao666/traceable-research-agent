"""Offline smoke checks for the read-only Tavily search tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.tools.defaults import register_default_tools
from app.tools.registry import get_tool
from app.tools.tavily_search import tavily_search


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "answer": "A concise answer.",
                "results": [
                    {
                        "title": "Tavily result",
                        "url": "https://example.com/result",
                        "content": "Current external evidence.",
                        "score": 0.91,
                        "raw_content": "Raw external evidence.",
                    }
                ],
            }
        ).encode("utf-8")


def main() -> None:
    missing = tavily_search(
        {"query": "latest LLM research"},
        settings_obj=Settings(tavily_api_key=None, offline_mode=False),
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Missing-key path attempted network access.")
        ),
    )
    assert not missing.success
    assert missing.metadata["error_type"] == "missing_api_key"
    assert missing.metadata["tavily_configured"] is False

    calls: list[str] = []

    def fake_opener(request, **_kwargs):
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer smoke-secret"
        calls.append(request.full_url)
        return FakeResponse()

    result = tavily_search(
        {
            "query": "latest LLM research",
            "max_results": 5,
            "search_depth": "advanced",
            "include_answer": True,
            "include_raw_content": False,
        },
        settings_obj=Settings(
            tavily_api_key="smoke-secret",
            offline_mode=False,
            tavily_fallback_to_mock=False,
        ),
        opener=fake_opener,
        sleeper=lambda _seconds: None,
    )
    assert result.success and len(calls) == 1
    assert result.metadata["data_source"] == "tavily_api"
    assert result.metadata["tavily_configured"] is True
    assert result.metadata["read_only"] is True
    assert result.output["results"][0]["title"] == "Tavily result"
    assert "smoke-secret" not in json.dumps(result.model_dump())

    offline = tavily_search(
        {"query": "offline demo"},
        settings_obj=Settings(offline_mode=True, tavily_api_key=None),
    )
    assert offline.success and offline.metadata["data_source"] == "mock"

    register_default_tools()
    spec = get_tool("tavily_search")
    assert spec is not None and "read-only" in spec.tags

    print(
        json.dumps(
            {
                "tavily_search": "ok",
                "missing_api_key": "ok",
                "fake_api": "ok",
                "offline_mock": "ok",
                "registered": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
