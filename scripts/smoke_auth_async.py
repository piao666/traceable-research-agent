"""Smoke checks for optional API-key auth, request context, and async runs."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import Request
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security.context import get_request_context


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ],
        }
    )


def main() -> None:
    original = {
        "auth_enabled": settings.auth_enabled,
        "demo_api_key": settings.demo_api_key,
        "async_run_enabled": settings.async_run_enabled,
        "llm_planner_enabled": settings.llm_planner_enabled,
        "llm_planner_mode": settings.llm_planner_mode,
    }
    demo_key = "demo-" + "secret"

    try:
        settings.auth_enabled = False
        settings.demo_api_key = None
        settings.async_run_enabled = True
        settings.llm_planner_enabled = False
        settings.llm_planner_mode = "deterministic"

        with TestClient(app) as client:
            assert_true(client.get("/api/tools").status_code == 200, "auth-disabled request failed")

            settings.auth_enabled = True
            settings.demo_api_key = demo_key
            assert_true(client.get("/api/tools").status_code == 401, "missing key was not rejected")
            assert_true(
                client.get("/api/tools", headers={"X-API-Key": "wrong"}).status_code == 403,
                "invalid key was not rejected",
            )
            assert_true(
                client.get("/api/tools", headers={"X-API-Key": demo_key}).status_code == 200,
                "configured API key header was rejected",
            )
            assert_true(
                client.get(
                    "/api/tools", headers={"Authorization": f"Bearer {demo_key}"}
                ).status_code
                == 200,
                "configured Bearer credential was rejected",
            )

            settings.auth_enabled = False
            explicit = get_request_context(
                make_request(
                    {
                        "X-Tenant-ID": "tenant_29.demo",
                        "X-User-ID": "user-29",
                    }
                ),
                settings,
            )
            fallback = get_request_context(make_request({}), settings)
            invalid = get_request_context(
                make_request({"X-Tenant-ID": "invalid tenant!", "X-User-ID": "user/29"}),
                settings,
            )
            assert_true(explicit.tenant_id == "tenant_29.demo", "tenant header extraction failed")
            assert_true(explicit.user_id == "user-29", "user header extraction failed")
            assert_true(fallback.tenant_id == "demo", "default tenant fallback failed")
            assert_true(fallback.user_id == "local-user", "default user fallback failed")
            assert_true(invalid.tenant_id == "demo", "invalid tenant was not sanitized")
            assert_true(invalid.user_id == "local-user", "invalid user was not sanitized")

            create_response = client.post(
                "/api/tasks",
                json={
                    "task": "Read local docs and generate a markdown report",
                    "report_type": "summary",
                    "source_mode": "mock",
                    "allowed_tools": ["file_reader", "report_writer"],
                },
            )
            assert_true(create_response.status_code == 200, "task creation failed")
            run_id = create_response.json()["run_id"]

            async_response = client.post(f"/api/tasks/{run_id}/run_async", json={})
            assert_true(async_response.status_code == 200, "async run request failed")
            assert_true(async_response.json()["status"] == "running", "async run did not start")

            final_status = None
            for _ in range(10):
                status_response = client.get(f"/api/tasks/{run_id}")
                assert_true(status_response.status_code == 200, "async status query failed")
                final_status = status_response.json()["status"]
                if final_status in {"completed", "waiting_human"}:
                    break
                time.sleep(0.5)
            assert_true(
                final_status in {"completed", "waiting_human"},
                f"unexpected async terminal status: {final_status}",
            )
            assert_true(client.get(f"/api/tasks/{run_id}/trace").status_code == 200, "trace query failed")
            assert_true(client.get(f"/api/reports/{run_id}").status_code == 200, "report query failed")

            repeated = client.post(f"/api/tasks/{run_id}/run_async", json={})
            assert_true(repeated.status_code == 200, "repeated async request failed")
            assert_true(repeated.json()["status"] == final_status, "repeated async changed status")
            assert_true(
                "no tools executed" in repeated.json()["message"]
                or "waiting for human" in repeated.json()["message"],
                "repeated async guard message missing",
            )

        print(
            json.dumps(
                {
                    "auth_async": "ok",
                    "auth_disabled_default": "ok",
                    "api_key_validation": "ok",
                    "tenant_context": "ok",
                    "run_async": "ok",
                    "repeated_async_guard": "ok",
                },
                indent=2,
            )
        )
    finally:
        settings.auth_enabled = original["auth_enabled"]
        settings.demo_api_key = original["demo_api_key"]
        settings.async_run_enabled = original["async_run_enabled"]
        settings.llm_planner_enabled = original["llm_planner_enabled"]
        settings.llm_planner_mode = original["llm_planner_mode"]


if __name__ == "__main__":
    main()
