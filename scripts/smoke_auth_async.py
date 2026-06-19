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
from app.database import SessionLocal
from app.main import app
from app.security.context import get_request_context
from app.trace import store


REGULAR_TASK = {
    "task": "Read local docs and generate a markdown report",
    "report_type": "summary",
    "source_mode": "mock",
    "allowed_tools": ["file_reader", "report_writer"],
}
HITL_TASK = {
    "task": "Read local docs and generate a markdown report with human approval",
    "report_type": "summary",
    "source_mode": "mock",
    "allowed_tools": ["file_reader", "report_writer"],
}


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


def create_task(client: TestClient, headers: dict[str, str] | None = None, *, hitl: bool = False) -> str:
    response = client.post(
        "/api/tasks",
        headers=headers,
        json=HITL_TASK if hitl else REGULAR_TASK,
    )
    assert_true(response.status_code == 200, "task creation failed")
    return response.json()["run_id"]


def trace_count(client: TestClient, run_id: str, headers: dict[str, str] | None = None) -> int:
    response = client.get(f"/api/tasks/{run_id}/trace", headers=headers)
    assert_true(response.status_code == 200, "trace query failed")
    return len(response.json())


def assert_auth_status(
    client: TestClient,
    method: str,
    path: str,
    expected_status: int,
    *,
    headers: dict[str, str] | None = None,
    payload: dict | None = None,
) -> None:
    response = client.request(method, path, headers=headers, json=payload)
    assert_true(
        response.status_code == expected_status,
        f"{method} {path} returned {response.status_code}, expected {expected_status}",
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
            assert_true(client.get("/health").status_code == 200, "health check failed")
            assert_true(client.get("/api/tools").status_code == 200, "auth-disabled tools failed")
            assert_true(client.get("/api/tools/file_reader").status_code == 200, "auth-disabled tool detail failed")
            assert_true(
                client.post(
                    "/api/tools/file_reader/execute",
                    json={"arguments": {"path": "demo_research_note.md", "max_chars": 100}},
                ).status_code
                == 200,
                "auth-disabled tool execution failed",
            )
            disabled_run_id = create_task(client)
            assert_true(client.get(f"/api/tasks/{disabled_run_id}").status_code == 200, "auth-disabled status failed")
            assert_true(client.get(f"/api/tasks/{disabled_run_id}/plan").status_code == 200, "auth-disabled plan failed")
            assert_true(client.post(f"/api/tasks/{disabled_run_id}/run", json={}).status_code == 200, "auth-disabled sync run failed")
            assert_true(client.get(f"/api/reports/{disabled_run_id}").status_code == 200, "auth-disabled report failed")

            disabled_async_id = create_task(client)
            assert_true(
                client.post(f"/api/tasks/{disabled_async_id}/run_async", json={}).status_code == 200,
                "auth-disabled async run failed",
            )

            disabled_hitl_id = create_task(client, hitl=True)
            waiting = client.post(f"/api/tasks/{disabled_hitl_id}/run", json={})
            assert_true(waiting.json()["status"] == "waiting_human", "auth-disabled HITL did not wait")
            assert_true(
                client.post(
                    f"/api/tasks/{disabled_hitl_id}/confirm",
                    json={"approved": True, "resume": True, "comment": "smoke approval"},
                ).status_code
                == 200,
                "auth-disabled confirmation failed",
            )

            settings.auth_enabled = True
            settings.demo_api_key = demo_key
            assert_true(client.get("/health").status_code == 200, "health should be auth exempt")
            protected_endpoints = [
                ("POST", "/api/tasks", REGULAR_TASK),
                ("GET", f"/api/tasks/{disabled_run_id}", None),
                ("GET", f"/api/tasks/{disabled_run_id}/plan", None),
                ("POST", f"/api/tasks/{disabled_run_id}/run", {}),
                ("POST", f"/api/tasks/{disabled_run_id}/run_async", {}),
                ("GET", f"/api/tasks/{disabled_run_id}/trace", None),
                (
                    "POST",
                    f"/api/tasks/{disabled_run_id}/confirm",
                    {"approved": True, "resume": True},
                ),
                ("GET", f"/api/reports/{disabled_run_id}", None),
                ("GET", "/api/tools", None),
                ("GET", "/api/tools/file_reader", None),
                (
                    "POST",
                    "/api/tools/file_reader/execute",
                    {"arguments": {"path": "demo_research_note.md", "max_chars": 100}},
                ),
            ]
            for method, path, payload in protected_endpoints:
                assert_auth_status(client, method, path, 401, payload=payload)
                assert_auth_status(
                    client,
                    method,
                    path,
                    403,
                    headers={"X-API-Key": "wrong"},
                    payload=payload,
                )

            key_headers = {"X-API-Key": demo_key}
            for method, path, payload in protected_endpoints:
                response = client.request(method, path, headers=key_headers, json=payload)
                assert_true(
                    response.status_code not in {401, 403, 503},
                    f"configured API key was rejected for {method} {path}",
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

            run_id = create_task(client)
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
            completed_trace_count = trace_count(client, run_id)
            assert_true(client.get(f"/api/reports/{run_id}").status_code == 200, "report query failed")

            repeated = client.post(f"/api/tasks/{run_id}/run_async", json={})
            assert_true(repeated.status_code == 200, "repeated async request failed")
            assert_true(repeated.json()["status"] == final_status, "repeated async changed status")
            assert_true(
                "no tools executed" in repeated.json()["message"]
                or "waiting for human" in repeated.json()["message"],
                "repeated async guard message missing",
            )
            assert_true(
                trace_count(client, run_id) == completed_trace_count,
                "completed repeated async wrote duplicate traces",
            )

            running_id = create_task(client)
            with SessionLocal() as db:
                store.update_agent_run_status(db, running_id, "running", None)
            running_trace_count = trace_count(client, running_id)
            running_repeat = client.post(f"/api/tasks/{running_id}/run_async", json={})
            assert_true(running_repeat.status_code == 200, "running guard request failed")
            assert_true(running_repeat.json()["status"] == "running", "running guard changed status")
            assert_true(
                trace_count(client, running_id) == running_trace_count,
                "running repeated async wrote duplicate traces",
            )

            hitl_id = create_task(client, hitl=True)
            hitl_start = client.post(f"/api/tasks/{hitl_id}/run_async", json={})
            assert_true(hitl_start.json()["status"] == "running", "HITL async did not start")
            hitl_status = client.get(f"/api/tasks/{hitl_id}").json()["status"]
            assert_true(hitl_status == "waiting_human", "async run bypassed HITL")
            waiting_trace_count = trace_count(client, hitl_id)
            waiting_repeat = client.post(f"/api/tasks/{hitl_id}/run_async", json={})
            assert_true(waiting_repeat.json()["status"] == "waiting_human", "waiting guard changed status")
            assert_true(
                trace_count(client, hitl_id) == waiting_trace_count,
                "waiting repeated async wrote duplicate traces",
            )
            confirmed = client.post(
                f"/api/tasks/{hitl_id}/confirm",
                json={"approved": True, "resume": True, "comment": "smoke approval"},
            )
            assert_true(confirmed.status_code == 200, "HITL confirmation failed")
            assert_true(confirmed.json()["status"] == "completed", "HITL did not complete after approval")

            disabled_id = create_task(client)
            settings.async_run_enabled = False
            disabled_async = client.post(f"/api/tasks/{disabled_id}/run_async", json={})
            assert_true(disabled_async.status_code == 400, "disabled async was not rejected")
            assert_true(
                disabled_async.json().get("detail") == "Async run is disabled.",
                "disabled async error changed",
            )
            sync_fallback = client.post(f"/api/tasks/{disabled_id}/run", json={})
            assert_true(sync_fallback.status_code == 200, "sync run failed while async was disabled")
            assert_true(sync_fallback.json()["status"] == "completed", "sync fallback did not complete")
            settings.async_run_enabled = True

        print(
            json.dumps(
                {
                    "auth_async": "ok",
                    "auth_disabled_default": "ok",
                    "api_key_validation": "ok",
                    "protected_endpoints": "ok",
                    "health_exempt": "ok",
                    "tenant_context": "ok",
                    "run_async": "ok",
                    "run_async_states": "ok",
                    "hitl_guard": "ok",
                    "async_disabled_sync_fallback": "ok",
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
