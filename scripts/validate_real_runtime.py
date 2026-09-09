"""Explicit R10 acceptance check for real LLM, search, fetch, and reporting.

This command can consume provider quota and access the public internet. It
never prints credentials, provider response bodies, fetched content, or prompt
text. No network call is made unless --confirm-real-calls is present.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.dispatcher import run_task_by_mode
from app.agent.planner import plan_task
from app.config import settings
from app.database import SessionLocal, init_db
from app.runtime.preflight import run_runtime_preflight
from app.skills.registry import init_skill_registry
from app.tools.defaults import register_default_tools
from app.trace import store


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the real R10 runtime without exposing secrets.")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="Acknowledge that real provider calls may consume quota.",
    )
    parser.add_argument(
        "--run-task",
        action="store_true",
        help="After preflight, execute one persisted search→fetch→LLM report task.",
    )
    parser.add_argument(
        "--question",
        default="Summarize the FastAPI official documentation for dependency injection and cite the fetched source.",
        help="Acceptance-task question used only with --run-task.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_real_calls:
        _print({
            "ready": False,
            "error_type": "confirmation_required",
            "message": "No real call was made. Re-run with --confirm-real-calls.",
        })
        return 2
    if settings.research_profile == "offline" or settings.offline_mode:
        _print({
            "ready": False,
            "error_type": "offline_profile",
            "message": "Real runtime validation requires the deep or standard profile.",
        })
        return 2

    preflight = run_runtime_preflight(settings)
    _print({"stage": "preflight", **preflight})
    if not preflight["ready"]:
        return 1
    if not args.run_task:
        return 0
    if settings.report_generation_mode != "llm":
        _print({
            "stage": "task",
            "ready": False,
            "error_type": "invalid_configuration",
            "message": "Full acceptance requires REPORT_GENERATION_MODE=llm.",
        })
        return 2

    try:
        init_db()
        register_default_tools()
        init_skill_registry(ROOT / "workspace" / "skills")
        allowed_tools = ["tavily_search", "web_fetcher", "report_writer"]
        with SessionLocal() as db:
            run = store.create_agent_run(
                db,
                task=args.question,
                report_type="summary",
                source_mode="real",
                allowed_tools=allowed_tools,
                run_config_snapshot=json.dumps(settings.get_safe_runtime_config_summary(), ensure_ascii=False),
            )
            plan = plan_task(
                run.task,
                allowed_tools,
                "real",
                planner_mode="deterministic",
                scenario_template="deep_web_research",
                execution_mode_override="planned",
                skill_name="deep_web_research",
            )
            store.update_agent_run_plan(db, run.run_id, plan)
            acceptance_settings = settings.model_copy(update={"deep_research_enabled": False})
            result = run_task_by_mode(db, run.run_id, acceptance_settings)
            final_run = store.get_fresh_agent_run(db, run.run_id)
            traces = store.list_tool_traces(db, run.run_id)
            summary = {
                "stage": "task",
                "run_id": run.run_id,
                "status": final_run.status if final_run else result.get("status", "unknown"),
                "tool_traces": len(traces),
                "successful_traces": sum(item.status == "success" for item in traces),
                "report_generated": bool(final_run and final_run.report_path),
                "source_mode": "real",
            }
            _print(summary)
            return 0 if summary["status"] == "completed" and summary["report_generated"] else 1
    except Exception:
        _print({
            "stage": "task",
            "status": "failed",
            "error_type": "internal_error",
            "message": "Acceptance task failed; inspect the persisted Run and server-side logs.",
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
