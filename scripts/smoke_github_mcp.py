"""Offline smoke checks for GitHub cache, fallback, and MCP read-only policy."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.mcp.readonly import is_http_method_allowed
from app.tools.mcp_github import github_search


CACHE_PATH = ROOT / "workspace" / "tmp" / "github_cache_smoke.json"
ARGUMENTS = {
    "query": "traceable research agent tool registry",
    "repo": "piao666/traceable-research-agent",
    "limit": 3,
}


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _settings(**overrides) -> Settings:
    values = {
        "github_search_cache_enabled": True,
        "github_search_cache_path": str(CACHE_PATH),
        "github_search_cache_ttl_seconds": 60,
        "github_public_api_timeout_seconds": 1,
        "github_public_api_max_retries": 2,
        "github_public_api_fallback_to_mock": True,
        "mcp_readonly_mode": True,
        "mcp_allow_write_tools": False,
    }
    values.update(overrides)
    return Settings(**values)


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _mock_and_validation_smoke() -> None:
    settings_obj = _settings()
    safe_summary = settings_obj.get_safe_github_mcp_config_summary()
    assert "github_token" not in safe_summary
    assert safe_summary["github_token_configured"] is False
    result = github_search({**ARGUMENTS, "mode": "mock"}, settings_obj=settings_obj)
    assert result.success and len(result.output["results"]) >= 1
    assert result.metadata["data_source"] == "mock"
    assert result.metadata["read_only"] is True
    assert result.metadata["write_operations_allowed"] is False

    missing = github_search({"query": "", "mode": "mock"}, settings_obj=_settings())
    invalid_repo = github_search(
        {"query": "trace", "repo": "invalid repo", "mode": "mock"},
        settings_obj=_settings(),
    )
    assert not missing.success and missing.metadata["error_type"] == "invalid_args"
    assert not invalid_repo.success and invalid_repo.metadata["error_type"] == "invalid_args"


def _cache_smoke() -> None:
    calls: list[str] = []
    payload = {
        "items": [
            {
                "title": "Cached read-only issue",
                "html_url": "https://github.com/example/repo/issues/1",
                "body": "Traceable evidence",
            }
        ]
    }

    def successful_opener(request, **_kwargs):
        assert request.get_method() == "GET"
        calls.append(request.full_url)
        return FakeResponse(payload)

    settings_obj = _settings()
    first = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=settings_obj,
        opener=successful_opener,
        sleeper=lambda _seconds: None,
    )
    assert first.success and first.metadata["data_source"] == "public_api"
    assert first.metadata["cache_hit"] is False and len(calls) == 1

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("Cache hit attempted network access.")

    second = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=settings_obj,
        opener=unexpected_network,
        sleeper=lambda _seconds: None,
    )
    assert second.success and second.metadata["data_source"] == "cache"
    assert second.metadata["cache_hit"] is True and len(calls) == 1
    cache_text = CACHE_PATH.read_text(encoding="utf-8")
    assert "Authorization" not in cache_text and "GITHUB_TOKEN" not in cache_text

    CACHE_PATH.write_text("{broken", encoding="utf-8")
    rebuilt = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=settings_obj,
        opener=successful_opener,
        sleeper=lambda _seconds: None,
    )
    assert rebuilt.success and rebuilt.metadata["data_source"] == "public_api"
    assert str(rebuilt.metadata["cache_error"]).startswith("cache_read_error")
    json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _repository_public_api_smoke() -> None:
    payload = {
        "items": [
            {
                "full_name": "example/most-starred",
                "name": "most-starred",
                "html_url": "https://github.com/example/most-starred",
                "stargazers_count": 12345,
                "description": "A real repository-shaped fake response.",
                "language": "Python",
                "updated_at": "2026-06-20T00:00:00Z",
            }
        ]
    }

    def repository_opener(request, **_kwargs):
        assert request.get_method() == "GET"
        assert "/search/repositories?" in request.full_url
        assert "sort=stars" in request.full_url and "order=desc" in request.full_url
        return FakeResponse(payload)

    result = github_search(
        {
            "query": "LLM agent",
            "search_type": "repositories",
            "sort": "stars",
            "order": "desc",
            "limit": 5,
        },
        settings_obj=_settings(
            github_search_cache_enabled=False,
            github_public_api_fallback_to_mock=False,
        ),
        opener=repository_opener,
        sleeper=lambda _seconds: None,
    )
    assert result.success and result.metadata["data_source"] == "public_api"
    assert result.output["mode"] == "public_api"
    repository = result.output["results"][0]
    assert repository["full_name"] == "example/most-starred"
    assert repository["stars"] == 12345
    assert repository["language"] == "Python"


def _fallback_smoke() -> None:
    attempts: list[int] = []
    backoffs: list[float] = []

    def offline(*_args, **_kwargs):
        attempts.append(1)
        raise URLError("offline")

    fallback = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=_settings(github_search_cache_enabled=False),
        opener=offline,
        sleeper=backoffs.append,
    )
    assert fallback.success and fallback.metadata["data_source"] == "fallback"
    assert fallback.metadata["fallback_used"] is True
    assert fallback.metadata["original_error_type"] == "network_error"
    assert fallback.metadata["retry_count"] == 2
    assert len(attempts) == 3 and backoffs == [0.5, 1.0]

    def rate_limited(request, **_kwargs):
        raise HTTPError(request.full_url, 403, "rate limited", {}, io.BytesIO())

    rate_fallback = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=_settings(
            github_search_cache_enabled=False,
            github_public_api_max_retries=0,
        ),
        opener=rate_limited,
        sleeper=lambda _seconds: None,
    )
    assert rate_fallback.metadata["rate_limited"] is True
    assert rate_fallback.metadata["original_error_type"] == "rate_limited"

    failed = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=_settings(
            github_search_cache_enabled=False,
            github_public_api_max_retries=0,
            github_public_api_fallback_to_mock=False,
        ),
        opener=offline,
        sleeper=lambda _seconds: None,
    )
    assert not failed.success and failed.metadata["error_type"] == "network_error"


def _readonly_smoke() -> None:
    assert is_http_method_allowed("GET")
    assert all(not is_http_method_allowed(method) for method in ("POST", "PUT", "PATCH", "DELETE"))
    result = github_search(
        {**ARGUMENTS, "mode": "mock"},
        settings_obj=_settings(mcp_readonly_mode=False, mcp_allow_write_tools=True),
    )
    assert result.metadata["write_operations_allowed"] is False
    assert result.metadata["readonly_config_ignored"] is True
    assert result.metadata["write_config_ignored"] is True


def _optional_public_api_smoke() -> None:
    if os.getenv("RUN_GITHUB_PUBLIC_API_SMOKE", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    result = github_search(
        {**ARGUMENTS, "mode": "public_api"},
        settings_obj=_settings(),
    )
    assert result.success or result.metadata["error_type"] in {
        "network_error",
        "rate_limited",
        "api_error",
        "invalid_response",
    }


def main() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _best_effort_unlink(CACHE_PATH)
    try:
        _mock_and_validation_smoke()
        _cache_smoke()
        _repository_public_api_smoke()
        _fallback_smoke()
        _readonly_smoke()
        _optional_public_api_smoke()
    finally:
        _best_effort_unlink(CACHE_PATH)
        _best_effort_unlink(CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp"))

    print(
        json.dumps(
            {
                "github_mcp": "ok",
                "mock": "ok",
                "cache": "ok",
                "repository_public_api": "ok",
                "fallback": "ok",
                "read_only": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
