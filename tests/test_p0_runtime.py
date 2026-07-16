"""P0 contracts for deterministic reports, error taxonomy, and redaction."""

from __future__ import annotations

import json
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.report_generation import resolve_report_llm_client
from app.config import Settings
from app.database import Base
from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.tools.base import ToolResult, ToolSpec
from app.tools.errors import ToolErrorCategory, classify_tool_error
from app.tools.registry import execute_tool, register_tool
from app.trace.logger import record_tool_result
from app.trace.models import AgentRun


class ExplodingLLMClient(LLMClient):
    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": "test", "available": True}

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        raise AssertionError("deterministic report mode must not call an LLM")


class ReportModeTests(unittest.TestCase):
    def test_deterministic_mode_ignores_available_client_and_keys(self) -> None:
        settings = Settings(
            report_generation_mode="deterministic",
            qwen_api_key="sk-report-secret-123456",
        )
        self.assertIsNone(resolve_report_llm_client(settings, ExplodingLLMClient()))

    def test_llm_mode_requires_provider_credentials(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(report_generation_mode="llm", llm_provider="qwen")

    def test_invalid_report_mode_fails_validation(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(report_generation_mode="automatic")

    def test_safe_runtime_summary_never_contains_secret_value(self) -> None:
        secret = "sk-summary-secret-123456"
        summary = Settings(qwen_api_key=secret).get_safe_runtime_config_summary()
        self.assertNotIn(secret, json.dumps(summary))
        self.assertTrue(summary["llm_provider_has_key"])


class ErrorTaxonomyTests(unittest.TestCase):
    def test_provider_errors_map_to_stable_categories(self) -> None:
        cases = {
            "parallel_timeout": ToolErrorCategory.TIMEOUT,
            "rate_limited": ToolErrorCategory.RATE_LIMITED,
            "missing_api_key": ToolErrorCategory.AUTH_ERROR,
            "invalid_json": ToolErrorCategory.INVALID_RESULT,
            "handler_error": ToolErrorCategory.INTERNAL_ERROR,
            "mcp_remote_call_failed": ToolErrorCategory.PROVIDER_ERROR,
            "safety_rejected": ToolErrorCategory.POLICY_ERROR,
        }
        for error_type, expected in cases.items():
            with self.subTest(error_type=error_type):
                self.assertEqual(classify_tool_error(error_type), expected)

    def test_registry_redacts_tool_results_and_adds_error_category(self) -> None:
        token = "ghp_registrysecret123456789"
        register_tool(
            ToolSpec(
                name="p0_secret_failure",
                description="P0 redaction fixture",
                input_schema={"type": "object"},
            ),
            lambda arguments: ToolResult(
                success=False,
                output={"token": token, "message": f"Bearer {token}"},
                error_message=f"authorization=Bearer-{token}",
                metadata={"error_type": "rate_limited", "api_key": token},
            ),
        )

        result = execute_tool("p0_secret_failure", {"query": "demo"})
        serialized = json.dumps(result.model_dump())
        self.assertNotIn(token, serialized)
        self.assertEqual(result.metadata["error_category"], "rate_limited")
        self.assertEqual(result.metadata["api_key"], "[REDACTED]")


class TraceRedactionTests(unittest.TestCase):
    def test_trace_persistence_redacts_inputs_outputs_and_errors(self) -> None:
        token = "sk-trace-secret-123456"
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(
                AgentRun(
                    run_id="p0-trace",
                    task="redaction test",
                    report_type="summary",
                    source_mode="mock",
                    status="running",
                )
            )
            db.commit()
            trace = record_tool_result(
                db,
                "p0-trace",
                1,
                "fixture",
                {"api_key": token, "query": f"token={token}"},
                ToolResult(
                    success=False,
                    output={"authorization": token, "text": f"Bearer {token}"},
                    error_message=f"provider failed token={token}",
                    metadata={"error_type": "timeout", "secret": token},
                ),
                10,
            )

        serialized = " ".join(
            value or ""
            for value in (
                trace.input_summary,
                trace.input_json,
                trace.output_summary,
                trace.output_json,
                trace.error_message,
            )
        )
        self.assertNotIn(token, serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertEqual(json.loads(trace.output_json)["metadata"]["error_category"], "timeout")


if __name__ == "__main__":
    unittest.main()
