"""Internal smoke for file path whitelist HITL and resume."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.executor import run_plan
from app.agent.plan_guardrails import normalize_plan_arguments
from app.agent.planner import plan_task
from app.database import SessionLocal, init_db
from app.main import app
from app.tools.defaults import register_default_tools
from app.trace import store


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _outside_file() -> Path:
    target = ROOT / "workspace" / "tmp" / "hitl_outside_allowed_root.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("outside allowed roots but approved by HITL\n", encoding="utf-8")
    return target


def _outside_plan(path: Path) -> dict:
    task = f"Read the explicit local file {path} and generate a markdown report"
    plan = {
        "version": "hitl-path-smoke",
        "task": task,
        "source_mode": "mock",
        "allowed_tools": ["file_reader", "report_writer"],
        "steps": [
            {
                "step_no": 1,
                "goal": "Read explicit local file outside the default docs root.",
                "tool_name": "file_reader",
                "arguments": {"path": str(path), "max_chars": 1000},
                "expected_output": "File content.",
                "completion_criteria": "The file is read only after per-file approval.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
            {
                "step_no": 2,
                "goal": "Generate report.",
                "tool_name": "report_writer",
                "arguments": {},
                "expected_output": "Markdown report.",
                "completion_criteria": "Report saved.",
                "risk_level": "low",
                "requires_confirmation": False,
            },
        ],
        "notes": [],
        "confirmation": None,
    }
    return normalize_plan_arguments(plan, task, "mock")


def _create_run_with_plan(db, task: str, plan: dict):
    run = store.create_agent_run(
        db=db,
        task=task,
        report_type="summary",
        source_mode="mock",
        allowed_tools=plan["allowed_tools"],
    )
    return store.update_agent_run_plan(db, run.run_id, plan)


def main() -> None:
    init_db()
    register_default_tools()
    outside = _outside_file()

    with SessionLocal() as db:
        allowed_task = "Read local docs and generate a markdown report"
        allowed_plan = plan_task(
            allowed_task,
            ["file_reader", "report_writer"],
            "mock",
            planner_mode="deterministic",
        )
        allowed_run = _create_run_with_plan(db, allowed_task, allowed_plan)
        allowed_run_id = allowed_run.run_id
        allowed_summary = run_plan(db, allowed_run.run_id)
        _assert(
            allowed_summary["status"] == "completed",
            f"Expected allowed docs path to complete, got {allowed_summary}",
        )

        rejected_plan = _outside_plan(outside)
        rejected_step = rejected_plan["steps"][0]
        _assert(
            rejected_step["requires_confirmation"] is True,
            f"Outside path was not marked for HITL: {rejected_step}",
        )
        rejected_run = _create_run_with_plan(db, rejected_plan["task"], rejected_plan)
        rejected_run_id = rejected_run.run_id
        waiting = run_plan(db, rejected_run.run_id)
        _assert(waiting["status"] == "waiting_human", f"Expected waiting_human, got {waiting}")

        approved_plan = _outside_plan(outside)
        approved_run = _create_run_with_plan(db, approved_plan["task"], approved_plan)
        approved_run_id = approved_run.run_id
        waiting_approved = run_plan(db, approved_run.run_id)
        _assert(
            waiting_approved["status"] == "waiting_human",
            f"Expected waiting_human before approval, got {waiting_approved}",
        )

    with TestClient(app) as client:
        rejected = client.post(
            f"/api/tasks/{rejected_run_id}/confirm",
            json={"approved": False, "resume": True, "comment": "Rejected by smoke_hitl."},
        )
        _assert(rejected.status_code == 200, rejected.text)
        _assert(rejected.json()["status"] == "failed", rejected.text)

        approved = client.post(
            f"/api/tasks/{approved_run_id}/confirm",
            json={"approved": True, "resume": True, "comment": "Approved by smoke_hitl."},
        )
        _assert(approved.status_code == 200, approved.text)
        payload = approved.json()
        _assert(payload["status"] == "completed", approved.text)

        trace = client.get(f"/api/tasks/{approved_run_id}/trace")
        _assert(trace.status_code == 200, trace.text)
        file_traces = [item for item in trace.json() if item["tool_name"] == "file_reader"]
        _assert(file_traces and file_traces[0]["status"] == "success", trace.text)
        metadata = file_traces[0].get("metadata") or {}
        _assert(
            metadata.get("approved_outside_allowed_roots") is True,
            f"Approved outside path metadata missing: {metadata}",
        )

    print(
        json.dumps(
            {
                "hitl": "ok",
                "allowed_run_id": allowed_run_id,
                "rejected_run_id": rejected_run_id,
                "approved_run_id": approved_run_id,
                "outside_path": str(outside),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
