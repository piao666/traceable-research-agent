"""R10 real-runtime profiles, provider taxonomy, and preflight contracts."""

from __future__ import annotations

from io import BytesIO
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from dotenv import dotenv_values

from app.config import Settings
from app.llm.base import LLMClient, LLMMessage, LLMResponse, LLMUsage
from app.llm.providers import OpenAICompatibleLLMClient, create_llm_client
from app.llm.errors import classify_http_error
from app.runtime.preflight import run_runtime_preflight
from app.tools.base import ToolResult


class FixtureLLM(LLMClient):
    def __init__(self, response: LLMResponse):
        self.response = response
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": "fixture", "model": "fixture", "available": True}

    def complete(self, messages: list[LLMMessage], temperature: float = 0.0, max_tokens: int = 2000) -> LLMResponse:
        self.calls += 1
        return self.response


class JsonResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class ResearchProfileTests(unittest.TestCase):
    def test_example_files_are_switchable_profile_contracts(self):
        for filename, expected in [(".env.example", "deep"), (".env.example.offline", "offline")]:
            with self.subTest(filename=filename):
                values = {key: str(value) for key, value in dotenv_values(filename).items() if value is not None}
                with patch.dict(os.environ, values, clear=True):
                    configured = Settings.from_env()
                self.assertEqual(configured.research_profile, expected)
                self.assertEqual(configured.offline_mode, expected == "offline")
                self.assertEqual(configured.external_tools_default_mode, "mock" if expected == "offline" else "real")

    def test_deep_profile_applies_real_defaults(self):
        environment = {
            "RESEARCH_PROFILE": "deep",
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://gateway.example/v1",
            "LLM_MODEL": "research-model",
            "LLM_API_KEY": "fixture-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.research_profile, "deep")
        self.assertEqual(settings.execution_mode, "react")
        self.assertTrue(settings.llm_planner_enabled)
        self.assertEqual(settings.report_generation_mode, "llm")
        self.assertTrue(settings.deep_research_enabled)
        self.assertFalse(settings.allow_mock_fallback)
        self.assertEqual(settings.research_max_tool_calls, 80)

    def test_explicit_environment_overrides_profile_defaults(self):
        environment = {
            "RESEARCH_PROFILE": "deep",
            "EXECUTION_MODE": "planned",
            "REPORT_GENERATION_MODE": "deterministic",
            "DEEP_RESEARCH_ENABLED": "false",
            "RESEARCH_MAX_TOOL_CALLS": "123",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.execution_mode, "planned")
        self.assertEqual(settings.report_generation_mode, "deterministic")
        self.assertFalse(settings.deep_research_enabled)
        self.assertEqual(settings.research_max_tool_calls, 123)

    def test_offline_profile_is_explicitly_non_real(self):
        with patch.dict(os.environ, {"RESEARCH_PROFILE": "offline"}, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.offline_mode)
        self.assertEqual(settings.external_tools_default_mode, "mock")
        self.assertEqual(settings.llm_provider, "deterministic")
        self.assertEqual(settings.report_generation_mode, "deterministic")
        self.assertFalse(settings.react_enabled)

    def test_invalid_profile_fails_configuration(self):
        with patch.dict(os.environ, {"RESEARCH_PROFILE": "unknown"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_real_profile_cannot_report_ready_with_deterministic_llm(self):
        from app.runtime.capabilities import local_capability_items, required_runtime_ready
        configured = Settings(research_profile="standard", llm_provider="deterministic", tavily_api_key="fixture")
        self.assertFalse(required_runtime_ready(configured, local_capability_items(configured)))

    def test_profile_defaults_isolate_bridge_fixture_mode(self):
        from app.mcp_bridge.registry import SourcePackRegistry
        with patch.dict(os.environ, {"RESEARCH_PROFILE": "deep"}, clear=True):
            real_registry = SourcePackRegistry.from_env()
        with patch.dict(os.environ, {"RESEARCH_PROFILE": "offline"}, clear=True):
            offline_registry = SourcePackRegistry.from_env()
        self.assertTrue(real_registry.providers)
        self.assertTrue(all(not provider.fake_mode for provider in real_registry.providers))
        self.assertTrue(all(provider.fake_mode for provider in offline_registry.providers))


class ProviderAdapterTests(unittest.TestCase):
    def client(self, retries: int = 0) -> OpenAICompatibleLLMClient:
        return OpenAICompatibleLLMClient(
            "openai_compatible", "fixture-model", "https://gateway.example/v1", "private-key", max_retries=retries
        )

    def test_generic_provider_uses_common_key_base_url_and_model(self):
        settings = Settings(
            llm_provider="openai_compatible",
            llm_api_key="private-key",
            llm_base_url="https://gateway.example/v1",
            llm_model="fixture-model",
        )
        client = create_llm_client(settings)
        self.assertTrue(client.is_available())
        description = client.describe()
        self.assertEqual(description["provider"], "openai_compatible")
        self.assertNotIn("private-key", json.dumps(description))

    def test_qwen_and_deepseek_aliases_keep_legacy_defaults(self):
        qwen = create_llm_client(Settings(llm_provider="qwen", qwen_api_key="q"))
        deepseek = create_llm_client(Settings(llm_provider="deepseek", deepseek_api_key="d"))
        self.assertEqual(qwen.describe()["model"], "qwen-plus")
        self.assertEqual(deepseek.describe()["model"], "deepseek-chat")

    def test_success_normalizes_usage(self):
        response = JsonResponse({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })
        with patch("app.llm.providers.urlopen", return_value=response):
            result = self.client().complete([LLMMessage(role="user", content="test")])
        self.assertTrue(result.success)
        self.assertEqual(result.usage.total_tokens, 3)

    def test_structured_completion_rejects_non_json_without_exposing_content(self):
        client = FixtureLLM(LLMResponse(success=True, content="not-json", provider="fixture"))
        result = client.structured_complete([LLMMessage(role="user", content="test")])
        self.assertFalse(result.success)
        self.assertEqual(result.metadata["error_type"], "structured_output_invalid")

    def test_generic_provider_rejects_invalid_base_url_locally(self):
        settings = Settings(
            llm_provider="openai_compatible",
            llm_api_key="private-key",
            llm_base_url="not-a-url",
            llm_model="fixture-model",
        )
        from app.runtime.capabilities import local_capability_items
        item = next(row for row in local_capability_items(settings) if row["name"] == "llm")
        self.assertFalse(item["configured"])
        self.assertFalse(create_llm_client(settings).is_available())

    def test_auth_error_is_not_retried_or_leaked(self):
        error = HTTPError("https://gateway.example", 401, "unauthorized", {}, BytesIO(b'{"error":"private-key"}'))
        with patch("app.llm.providers.urlopen", side_effect=error) as opener, patch("app.llm.providers.sleep"):
            result = self.client(retries=2).complete([LLMMessage(role="user", content="test")])
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(result.metadata["error_type"], "auth_error")
        self.assertNotIn("private-key", json.dumps(result.model_dump()))

    def test_rate_limit_honors_retry_after_then_recovers(self):
        error = HTTPError("https://gateway.example", 429, "limited", {"Retry-After": "2"}, BytesIO())
        response = JsonResponse({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
        with patch("app.llm.providers.urlopen", side_effect=[error, response]), patch("app.llm.providers.sleep") as sleeper:
            result = self.client(retries=1).complete([LLMMessage(role="user", content="test")])
        self.assertTrue(result.success)
        self.assertGreaterEqual(sleeper.call_args.args[0], 2)

    def test_http_error_taxonomy_is_stable(self):
        fixtures = [
            (401, "", "auth_error", False),
            (403, "", "permission_error", False),
            (404, "", "model_not_found", False),
            (400, "maximum context length exceeded", "context_overflow", False),
            (422, "bad payload", "invalid_request", False),
            (503, "", "provider_unavailable", True),
        ]
        for status, body, expected, retryable in fixtures:
            with self.subTest(status=status, expected=expected):
                result = classify_http_error(status, response_body=body)
                self.assertEqual(result.error_type, expected)
                self.assertEqual(result.retryable, retryable)

    def test_retry_after_http_date_is_bounded(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
        result = classify_http_error(429, {"Retry-After": future})
        self.assertEqual(result.retry_after_seconds, 60)

    def test_planner_fallback_preserves_sanitized_error_type(self):
        from app.agent.planner import plan_task
        client = FixtureLLM(LLMResponse(
            success=False,
            provider="fixture",
            model="fixture",
            error_message="LLM provider request failed (auth_error, HTTP 401).",
            metadata={"error_type": "auth_error"},
        ))
        with patch("app.agent.planner.create_llm_client", return_value=client):
            plan = plan_task(
                "Summarize a local fixture",
                ["file_reader", "report_writer"],
                "mock",
                planner_mode="llm",
                skill_name="missing-skill",
            )
        self.assertEqual(plan["planner_source"], "deterministic_fallback")
        self.assertEqual(plan["planner_error"]["error_type"], "auth_error")
        self.assertNotIn("Authorization", json.dumps(plan))

    def test_planner_decomposer_exposes_categorized_failure_for_later_trace(self):
        from app.agent.query_decomposer import decompose_task
        client = FixtureLLM(LLMResponse(
            success=False,
            provider="fixture",
            model="fixture",
            metadata={"error_type": "rate_limited"},
        ))
        failures: list[dict] = []
        result = decompose_task(
            "Research and compare several agent frameworks in detail",
            client,
            force=True,
            error_callback=failures.append,
        )
        self.assertEqual(result, ["Research and compare several agent frameworks in detail"])
        self.assertEqual(failures[0]["error_type"], "rate_limited")
        self.assertEqual(failures[0]["role"], "planner_decomposer")


class SynthesizerContractTests(unittest.TestCase):
    def observation(self) -> list[dict]:
        return [{
            "success": True,
            "tool_name": "web_fetcher",
            "output": {"pages": [{
                "url": "https://docs.example/page",
                "content": "Verified fixture evidence for report synthesis.",
            }]},
        }]

    def test_failed_response_without_usage_is_reported_to_trace_callback(self):
        from app.agent.reporter import _llm_synthesize_answer
        client = FixtureLLM(LLMResponse(
            success=False,
            provider="fixture",
            model="fixture",
            error_message="LLM provider request failed (auth_error, HTTP 401).",
            metadata={"error_type": "auth_error"},
        ))
        responses: list[LLMResponse] = []
        answer = _llm_synthesize_answer(
            "Summarize the fixture",
            self.observation(),
            client,
            usage_callback=responses.append,
        )
        self.assertIsNone(answer)
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].metadata["error_type"], "auth_error")

    def test_empty_success_is_classified_as_malformed_response(self):
        from app.agent.reporter import _llm_synthesize_answer
        client = FixtureLLM(LLMResponse(
            success=True,
            content=None,
            provider="fixture",
            model="fixture",
        ))
        responses: list[LLMResponse] = []
        self.assertIsNone(_llm_synthesize_answer(
            "Summarize the fixture",
            self.observation(),
            client,
            usage_callback=responses.append,
        ))
        self.assertEqual(responses[0].metadata["error_type"], "malformed_response")


class RuntimePreflightTests(unittest.TestCase):
    def real_settings(self) -> Settings:
        return Settings(
            research_profile="deep",
            llm_provider="openai_compatible",
            llm_api_key="private-key",
            llm_base_url="https://gateway.example/v1",
            llm_model="fixture-model",
            tavily_api_key="private-search-key",
            report_generation_mode="llm",
            deep_research_enabled=True,
        )

    def test_real_preflight_requires_non_mock_search_and_fetch(self):
        llm = FixtureLLM(LLMResponse(
            success=True,
            content='{"ok":true}',
            provider="fixture",
            model="fixture",
            usage=LLMUsage(prompt_tokens=4, completion_tokens=3, total_tokens=7),
        ))

        def searcher(_arguments, **_kwargs):
            return ToolResult(success=True, output={"results": [{"url": "https://docs.example/page"}]},
                              metadata={"data_source": "tavily_api", "fallback_used": False})

        def fetcher(_arguments, **_kwargs):
            return ToolResult(success=True, output={"fetched_count": 1, "pages": [{"content": "evidence"}]})

        result = run_runtime_preflight(self.real_settings(), llm_client=llm, searcher=searcher, fetcher=fetcher)
        self.assertTrue(result["ready"])
        self.assertTrue(result["verified"])
        self.assertEqual(llm.calls, 1)
        self.assertNotIn("private-key", json.dumps(result, default=str))

    def test_mock_search_cannot_pass_real_preflight(self):
        llm = FixtureLLM(LLMResponse(success=True, content='{"ok":true}', provider="fixture"))

        def searcher(_arguments, **_kwargs):
            return ToolResult(success=True, output={"results": [{"url": "https://example.invalid/mock"}]},
                              metadata={"data_source": "mock", "fallback_used": True})

        result = run_runtime_preflight(self.real_settings(), llm_client=llm, searcher=searcher)
        self.assertFalse(result["ready"])
        self.assertTrue(any(item["capability"] == "search" for item in result["blockers"]))

    def test_preflight_converts_probe_exceptions_to_sanitized_blockers(self):
        llm = FixtureLLM(LLMResponse(success=True, content='{"ok":true}', provider="fixture"))

        def searcher(_arguments, **_kwargs):
            raise RuntimeError("Authorization: Bearer private-key")

        result = run_runtime_preflight(self.real_settings(), llm_client=llm, searcher=searcher)
        serialized = json.dumps(result, default=str)
        self.assertFalse(result["ready"])
        self.assertNotIn("private-key", serialized)
        self.assertIn("provider_unavailable", serialized)

    def test_preflight_sanitizes_availability_check_exception(self):
        llm = FixtureLLM(LLMResponse(success=True, content='{"ok":true}', provider="fixture"))
        llm.is_available = Mock(side_effect=RuntimeError("Authorization: Bearer private-key"))
        searcher = Mock(return_value=ToolResult(
            success=False,
            metadata={"error_type": "provider_unavailable"},
        ))
        result = run_runtime_preflight(
            self.real_settings(),
            llm_client=llm,
            searcher=searcher,
        )
        serialized = json.dumps(result, default=str)
        self.assertFalse(result["ready"])
        self.assertNotIn("private-key", serialized)
        self.assertIn("provider_unavailable", serialized)

    def test_missing_usage_is_a_warning_not_a_false_connection_failure(self):
        llm = FixtureLLM(LLMResponse(success=True, content='{"ok":true}', provider="fixture"))

        def searcher(_arguments, **_kwargs):
            return ToolResult(success=True, output={"results": [{"url": "https://docs.example/page"}]},
                              metadata={"data_source": "tavily_api", "fallback_used": False})

        def fetcher(_arguments, **_kwargs):
            return ToolResult(success=True, output={"fetched_count": 1})

        result = run_runtime_preflight(self.real_settings(), llm_client=llm, searcher=searcher, fetcher=fetcher)
        self.assertTrue(result["ready"])
        self.assertTrue(any("usage" in warning for warning in result["warnings"]))

    def test_offline_preflight_makes_no_external_calls(self):
        settings = Settings(research_profile="offline", offline_mode=True, llm_provider="deterministic")
        with patch("app.runtime.preflight.create_llm_client") as create, patch("app.runtime.preflight.tavily_search") as search:
            result = run_runtime_preflight(settings)
        self.assertTrue(result["ready"])
        self.assertFalse(result["verified"])
        create.assert_not_called()
        search.assert_not_called()


class RealRuntimeValidatorTests(unittest.TestCase):
    def test_validator_requires_explicit_real_call_confirmation(self):
        from scripts.validate_real_runtime import main
        with patch("scripts.validate_real_runtime.run_runtime_preflight") as preflight:
            code = main([])
        self.assertEqual(code, 2)
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
