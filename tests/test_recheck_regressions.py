"""Behavior-focused regressions found during the frontend readiness recheck."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.events import _seed_cursor_after_event_id
from app.api.tasks import (
    _run_task_in_background,
    _tool_trace_response,
    cancel_task,
    list_tasks,
    retry_task,
)
from app.config import Settings
from app.database import Base
from app.evidence import models as evidence_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.schemas import TaskCancelRequest, TaskRetryRequest
from app.tools.base import RiskLevel, ToolResult, ToolSpec
from app.tools.registry import register_tool
from app.trace import store
from app.trace.events import TraceEventCursor, build_incremental_events
from app.trace.logger import record_trace_event
from app.trace import models as trace_models  # noqa: F401


def _plan(mode: str = "planned") -> dict:
    return {
        "version": "test-v1",
        "task": "regression task",
        "source_mode": "mock",
        "allowed_tools": ["file_reader", "report_writer"],
        "execution_mode": mode,
        "requested_execution_mode": mode,
        "steps": [
            {
                "step_no": 1,
                "goal": "Read a fixture.",
                "tool_name": "file_reader",
                "arguments": {"path": "demo_research_note.md"},
                "expected_output": "text",
                "completion_criteria": "text returned",
                "risk_level": "low",
                "requires_confirmation": False,
            }
        ],
        "notes": [],
    }


class ApiRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _create_run(self, mode: str = "planned", status: str = "pending"):
        run = store.create_agent_run(self.db, f"{mode} task", "summary", "mock")
        run = store.update_agent_run_plan(self.db, run.run_id, _plan(mode))
        if status != "pending":
            run = store.update_agent_run_status(self.db, run.run_id, status, None)
        return run

    def test_task_list_filters_and_total_use_the_same_constraints(self) -> None:
        self._create_run("planned")
        react = self._create_run("react")

        response = list_tasks(
            session_id=None,
            status=None,
            execution_mode="react",
            created_after=None,
            created_before=None,
            limit=50,
            offset=0,
            db=self.db,
        )
        self.assertEqual([item.run_id for item in response.tasks], [react.run_id])
        self.assertEqual(response.total, 1)

        future = datetime.now(timezone.utc) + timedelta(days=1)
        empty = list_tasks(
            session_id=None,
            status=None,
            execution_mode=None,
            created_after=future,
            created_before=None,
            limit=50,
            offset=0,
            db=self.db,
        )
        self.assertEqual(empty.tasks, [])
        self.assertEqual(empty.total, 0)

    def test_cancel_is_terminal_and_records_the_previous_status(self) -> None:
        run = self._create_run()
        response = cancel_task(
            run.run_id,
            TaskCancelRequest(reason="cancel regression"),
            self.db,
        )
        self.assertEqual(response.status, "cancelled")
        trace = store.list_tool_traces(self.db, run.run_id)[-1]
        self.assertEqual(json.loads(trace.output_json)["previous_status"], "pending")

        from app.agent.executor import run_plan

        summary = run_plan(
            self.db,
            run.run_id,
            settings_obj=Settings(parallel_execution_enabled=False),
        )
        self.assertEqual(summary["status"], "cancelled")

    def test_cancel_during_a_tool_call_is_not_overwritten_by_completion(self) -> None:
        run = self._create_run()

        def cancel_during_call(_name, _arguments):
            store.update_agent_run_status(
                self.db,
                run.run_id,
                "cancelled",
                "cancelled during tool",
            )
            return ToolResult(success=True, output={"text": "done"})

        from app.agent.executor import run_plan

        with (
            patch("app.agent.executor.is_executable_tool", return_value=True),
            patch("app.agent.executor.execute_tool", side_effect=cancel_during_call),
        ):
            summary = run_plan(
                self.db,
                run.run_id,
                settings_obj=Settings(parallel_execution_enabled=False),
            )
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(store.get_fresh_agent_run(self.db, run.run_id).status, "cancelled")

    def test_retry_rejects_live_runs_and_clears_execution_state(self) -> None:
        pending = self._create_run()
        with self.assertRaises(HTTPException) as caught:
            retry_task(pending.run_id, TaskRetryRequest(), self.db)
        self.assertEqual(caught.exception.status_code, 409)

        failed = self._create_run(status="failed")
        failed_plan = _plan()
        failed_plan["confirmation"] = {"approved": True}
        failed_plan["react_state"] = {"observation_history": [{"secret": "stale"}]}
        store.replace_agent_run_plan(self.db, failed.run_id, failed_plan)

        retried = retry_task(failed.run_id, TaskRetryRequest(), self.db)
        new_run = store.get_agent_run(self.db, retried.run_id)
        new_plan = json.loads(new_run.plan_json)
        self.assertEqual(new_plan["parent_run_id"], failed.run_id)
        self.assertNotIn("confirmation", new_plan)
        self.assertNotIn("react_state", new_plan)

        with self.assertRaises(HTTPException) as unsupported:
            retry_task(
                failed.run_id,
                TaskRetryRequest(from_failed_step=True),
                self.db,
            )
        self.assertEqual(unsupported.exception.status_code, 400)

    def test_background_failure_is_persisted(self) -> None:
        run = self._create_run()
        with (
            patch("app.api.tasks.SessionLocal", self.Session),
            patch("app.api.tasks.run_task_by_mode", side_effect=RuntimeError("forced failure")),
        ):
            _run_task_in_background(run.run_id)
        failed = store.get_fresh_agent_run(self.db, run.run_id)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_message, "forced failure")

    def test_trace_api_exposes_token_and_cost_metrics(self) -> None:
        run = self._create_run()
        trace = record_trace_event(
            db=self.db,
            run_id=run.run_id,
            step_no=1,
            tool_name="report_synthesis",
            status="success",
            input_data={},
            output_summary="done",
            output_data={},
            token_in=12,
            token_out=7,
            estimated_cost=0.25,
        )
        response = _tool_trace_response(trace)
        self.assertEqual(response.token_in, 12)
        self.assertEqual(response.token_out, 7)
        self.assertEqual(response.estimated_cost, 0.25)

    def test_sse_resume_cursor_skips_acknowledged_traces(self) -> None:
        run = self._create_run()
        traces = []
        for step_no in range(1, 4):
            traces.append(
                record_trace_event(
                    db=self.db,
                    run_id=run.run_id,
                    step_no=step_no,
                    tool_name=f"tool_{step_no}",
                    status="success",
                    input_data={},
                    output_summary="done",
                    output_data={},
                )
            )
        cursor = TraceEventCursor()
        self.assertTrue(
            _seed_cursor_after_event_id(
                self.db,
                run.run_id,
                cursor,
                traces[1].trace_id,
            )
        )
        events, _ = build_incremental_events(self.db, run.run_id, cursor)
        replayed_trace_ids = [event["trace_id"] for event in events if event.get("trace_id")]
        self.assertEqual(replayed_trace_ids, [traces[2].trace_id])

    def test_dynamic_remote_mcp_tool_is_executable_when_policy_allows_it(self) -> None:
        from app.agent.executor import is_executable_tool

        register_tool(
            ToolSpec(
                name="regression_remote.search",
                description="Read-only dynamically discovered remote tool.",
                input_schema={},
                risk_level=RiskLevel.LOW,
                read_only=True,
                side_effect_free=True,
                tags=["mcp_remote", "mcp-channel-readonly"],
                metadata={"tool_source": "mcp_remote", "mcp_channel": "readonly"},
            ),
            handler=lambda _arguments: ToolResult(success=True),
        )
        register_tool(
            ToolSpec(
                name="regression_remote.write",
                description="Write-channel remote tool.",
                input_schema={},
                risk_level=RiskLevel.HIGH,
                read_only=False,
                side_effect_free=False,
                tags=["mcp_remote", "mcp-channel-write"],
                metadata={"tool_source": "mcp_remote", "mcp_channel": "write"},
            ),
            handler=lambda _arguments: ToolResult(success=True),
        )

        self.assertTrue(is_executable_tool("regression_remote.search"))
        self.assertFalse(is_executable_tool("regression_remote.write"))


class _State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class _StreamingResponse:
    def __init__(self, lines: list[str]):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = True):
        del decode_unicode
        return iter(self.lines)


class StreamlitRegressionTests(unittest.TestCase):
    def test_untrusted_html_is_escaped_in_reports_and_trace_cards(self) -> None:
        import frontend.streamlit_app as frontend

        payload = '<svg onload=alert(1)><a href="java&#x73;cript:alert(2)">x</a></svg>'
        sanitized = frontend._sanitize_html(payload)
        self.assertNotIn("<svg", sanitized)
        self.assertIn("&lt;svg", sanitized)

        captured = []
        with patch.object(
            frontend.st,
            "markdown",
            side_effect=lambda body, **kwargs: captured.append((body, kwargs)),
        ):
            frontend.trace_step_card(
                {
                    "step_no": 1,
                    "tool_name": "remote_tool",
                    "status": "failed",
                    "input_summary": payload,
                    "output_summary": '<img src=x onerror=alert(3)>',
                    "error_message": "<script>alert(4)</script>",
                }
            )
        body, kwargs = captured[0]
        self.assertTrue(kwargs["unsafe_allow_html"])
        self.assertNotIn("<svg", body)
        self.assertNotIn("<img", body)
        self.assertNotIn("<script", body)
        self.assertIn("&lt;svg", body)

    def test_streamlit_sse_uses_a_bounded_resumable_slice(self) -> None:
        import frontend.streamlit_app as frontend

        state = _State(
            api_base_url="http://example.test",
            api_key="",
            last_event_id="trace-old",
            event_log=[],
            realtime_auto_refresh=True,
        )
        event = {
            "event_type": "trace_finished",
            "trace_id": "trace-new",
            "status": "success",
        }
        lines = [
            "id: trace-new",
            "event: trace_finished",
            f"data: {json.dumps(event)}",
            "",
        ]
        captured: dict = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _StreamingResponse(lines)

        old_state = frontend.st.session_state
        frontend.st.session_state = state
        try:
            with patch("frontend.streamlit_app.requests.get", side_effect=fake_get):
                frontend.stream_task_events("run-1")
        finally:
            frontend.st.session_state = old_state

        self.assertEqual(captured["params"]["max_duration_seconds"], 2)
        self.assertEqual(captured["params"]["after_trace_id"], "trace-old")
        self.assertEqual(state.last_event_id, "trace-new")
        self.assertEqual(len(state.event_log), 1)


if __name__ == "__main__":
    unittest.main()
