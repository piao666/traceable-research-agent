"""Offline smoke coverage for planned and optional ReAct execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.dispatcher import run_task_by_mode
from app.agent.planner import plan_task
from app.agent.react_executor import run_react_task
from app.config import Settings
from app.database import SessionLocal, init_db
from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.rag.build_index import build_local_index
from app.tools.defaults import register_default_tools
from app.trace import store


class ScriptedLLMClient(LLMClient):
    def __init__(self, decisions: list[dict | str]):
        self.decisions = list(decisions)

    def is_available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": "scripted", "model": "react-smoke", "available": True}

    def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        decision = self.decisions.pop(0) if self.decisions else {
            "thought": "No scripted actions remain.",
            "action": "finish",
            "args": {"summary": "Scripted run finished."},
            "finish_reason": "script_complete",
        }
        content = decision if isinstance(decision, str) else json.dumps(decision)
        return LLMResponse(
            success=True,
            content=content,
            provider="scripted",
            model="react-smoke",
        )


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def create_run(db, task: str, allowed_tools: list[str]):
    run = store.create_agent_run(db, task, "summary", "mock", allowed_tools)
    plan = plan_task(task, allowed_tools, "mock", planner_mode="deterministic")
    plan["requested_execution_mode"] = "react"
    plan["execution_mode"] = "react"
    return store.update_agent_run_plan(db, run.run_id, plan)


def trace_metadata(trace) -> dict:
    try:
        output = json.loads(trace.output_json or "{}")
    except json.JSONDecodeError:
        return {}
    return output.get("metadata", {}) if isinstance(output, dict) else {}


def report_exists(run) -> bool:
    return bool(run.report_path and (ROOT / run.report_path).exists())


def main() -> None:
    init_db()
    register_default_tools()
    from scripts.init_demo_db import init_demo_db

    init_demo_db()
    build_local_index()

    react_settings = Settings(
        execution_mode="react",
        react_enabled=True,
        react_max_steps=5,
        react_same_tool_max_calls=2,
        react_fallback_to_planned=True,
        react_finish_on_invalid_decision=True,
    )
    with SessionLocal() as db:
        planned = create_run(
            db,
            "Read local docs and generate a traceable summary report.",
            ["file_reader", "report_writer"],
        )
        planned_plan = json.loads(planned.plan_json)
        planned_plan["requested_execution_mode"] = "planned"
        planned_plan["execution_mode"] = "planned"
        store.replace_agent_run_plan(db, planned.run_id, planned_plan)
        planned_summary = run_task_by_mode(db, planned.run_id, Settings())
        assert_true(planned_summary["status"] == "completed", "planned default failed")

        basic = create_run(
            db,
            "Read local docs and generate a traceable summary report.",
            ["file_reader", "rag_search", "report_writer"],
        )
        basic_client = ScriptedLLMClient(
            [
                {"thought": "Read approved local evidence.", "action": "file_reader", "args": {"path": "demo_research_note.md", "max_chars": 2000}, "finish_reason": None},
                {"thought": "Retrieve supporting chunks.", "action": "rag_search", "args": {"query": "trace tool registry", "top_k": 2}, "finish_reason": None},
                {"thought": "Evidence is sufficient for the report.", "action": "report_writer", "args": {}, "finish_reason": "completed"},
            ]
        )
        basic_summary = run_task_by_mode(
            db, basic.run_id, react_settings, llm_client=basic_client
        )
        basic_run = store.get_agent_run(db, basic.run_id)
        basic_traces = store.list_tool_traces(db, basic.run_id)
        assert_true(basic_summary["status"] == "completed", "basic ReAct run failed")
        assert_true(report_exists(basic_run), "basic ReAct report missing")
        assert_true(
            all(trace_metadata(trace).get("execution_mode") == "react" for trace in basic_traces),
            "basic ReAct trace metadata missing",
        )
        assert_true(
            all(
                trace_metadata(trace).get("thought")
                and trace_metadata(trace).get("action")
                and trace_metadata(trace).get("observation_summary")
                for trace in basic_traces
            ),
            "Thought/Action/Observation missing",
        )

        recovery = create_run(
            db,
            "Recover from missing local evidence and finish with available retrieval evidence.",
            ["file_reader", "rag_search", "report_writer"],
        )
        recovery_client = ScriptedLLMClient(
            [
                {"thought": "Try the requested local file.", "action": "file_reader", "args": {"path": "missing.md"}, "finish_reason": None},
                {"thought": "The file failed, so use local retrieval.", "action": "rag_search", "args": {"query": "trace persistence", "top_k": 2}, "finish_reason": None},
                {"thought": "Finish with recovered evidence.", "action": "finish", "args": {"summary": "Recovered through RAG."}, "finish_reason": "completed"},
            ]
        )
        recovery_summary = run_react_task(db, recovery.run_id, react_settings, recovery_client)
        recovery_traces = store.list_tool_traces(db, recovery.run_id)
        assert_true(recovery_summary["status"] == "completed", "failure recovery failed")
        assert_true(any(trace.status == "failed" for trace in recovery_traces), "failed observation not traced")
        assert_true(
            any(
                trace.status == "failed"
                and trace_metadata(trace).get("execution_mode") == "react"
                and trace_metadata(trace).get("observation_summary")
                for trace in recovery_traces
            ),
            "failed observation metadata missing",
        )
        assert_true(any(trace.tool_name == "rag_search" and trace.status == "success" for trace in recovery_traces), "recovery tool not used")

        invalid = create_run(
            db,
            "Read local docs and generate a report.",
            ["file_reader", "report_writer"],
        )
        invalid_summary = run_react_task(
            db,
            invalid.run_id,
            react_settings,
            ScriptedLLMClient(["not valid json"]),
        )
        invalid_plan = json.loads(store.get_agent_run(db, invalid.run_id).plan_json)
        invalid_meta = [trace_metadata(trace) for trace in store.list_tool_traces(db, invalid.run_id)]
        assert_true(invalid_summary["status"] == "completed", "invalid decision caused run failure")
        invalid_state = invalid_plan["react_state"]
        assert_true(
            int(invalid_state.get("invalid_decisions") or 0) >= 1,
            "invalid decision count missing",
        )
        assert_true(any(meta.get("error_type") == "invalid_decision" for meta in invalid_meta), "invalid decision metadata missing")

        limited = create_run(
            db,
            "Read local docs repeatedly but stop safely.",
            ["file_reader", "report_writer"],
        )
        repeated_decision = {"thought": "Read the local note.", "action": "file_reader", "args": {"path": "demo_research_note.md"}, "finish_reason": None}
        limited_summary = run_react_task(
            db,
            limited.run_id,
            react_settings,
            ScriptedLLMClient([repeated_decision, repeated_decision, repeated_decision]),
        )
        limited_plan = json.loads(store.get_agent_run(db, limited.run_id).plan_json)
        limited_meta = [trace_metadata(trace) for trace in store.list_tool_traces(db, limited.run_id)]
        assert_true(limited_summary["status"] == "completed", "tool call limit did not terminate")
        assert_true(limited_plan["react_state"]["completed_with_limitation"] is True, "limit was not recorded")
        assert_true(any(meta.get("error_type") == "tool_call_limit" for meta in limited_meta), "tool call limit trace missing")

        hitl = create_run(
            db,
            "Read local docs, retrieve trace evidence, and generate a markdown report with human approval",
            ["file_reader", "rag_search", "report_writer"],
        )
        hitl_client = ScriptedLLMClient(
            [
                {"thought": "Read local evidence.", "action": "file_reader", "args": {"path": "demo_research_note.md"}, "finish_reason": None},
                {"thought": "Retrieve supporting evidence.", "action": "rag_search", "args": {"query": "trace evidence", "top_k": 2}, "finish_reason": None},
                {"thought": "Request the approved report action.", "action": "report_writer", "args": {}, "finish_reason": "completed"},
            ]
        )
        waiting = run_react_task(db, hitl.run_id, react_settings, hitl_client)
        waiting_run = store.get_agent_run(db, hitl.run_id)
        assert_true(waiting["status"] == "waiting_human", "ReAct bypassed HITL")
        assert_true(not report_exists(waiting_run), "report existed before confirmation")
        hitl_plan = json.loads(waiting_run.plan_json)
        pending = hitl_plan["react_state"]["pending_confirmation"]
        hitl_plan["confirmation"] = {
            "required_step_no": pending["step_no"],
            "required_tool_name": pending["decision"]["action"],
            "approved": True,
            "comment": "Approved by ReAct smoke.",
        }
        store.replace_agent_run_plan(db, hitl.run_id, hitl_plan)
        store.update_agent_run_status(db, hitl.run_id, "pending", None)
        resumed = run_react_task(db, hitl.run_id, react_settings, hitl_client)
        assert_true(resumed["status"] == "completed", "approved ReAct run did not resume")
        assert_true(report_exists(store.get_agent_run(db, hitl.run_id)), "approved report missing")

    print(
        json.dumps(
            {
                "react_executor": "ok",
                "planned_default": "ok",
                "react_basic": "ok",
                "failure_recovery": "ok",
                "invalid_decision": "ok",
                "tool_call_limit": "ok",
                "hitl_guard": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
