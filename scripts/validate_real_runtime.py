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
    parser.add_argument(
        "--r11-fetch-smoke",
        action="store_true",
        help="Run the explicit R11 static, Browser, PDF, and configured remote-extractor smoke.",
    )
    parser.add_argument("--static-url", default="https://example.com/", help="Public static HTML fixture URL.")
    parser.add_argument("--browser-url", default="https://playwright.dev/python/", help="Public Browser fixture URL.")
    parser.add_argument(
        "--pdf-url",
        default="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        help="Public PDF fixture URL.",
    )
    return parser


def _r11_fetch_smoke(args: argparse.Namespace) -> int:
    from app.retrieval.remote_extract import configured_remote_providers
    from app.tools.web_fetcher import web_fetch

    cases = [
        ("static_http", args.static_url, ["http"]),
        ("browser", args.browser_url, ["browser"]),
        ("pdf", args.pdf_url, []),
    ]
    remote = configured_remote_providers(
        provider_order=settings.fetch_remote_extract_provider_order,
    )
    if settings.fetch_remote_extract_enabled and any(provider.available() for provider in remote):
        cases.append(("remote_extract", args.static_url, ["remote_extract"]))

    results = []
    for name, url, preferred in cases:
        result = web_fetch(
            {
                "urls": [url],
                "preferred_backends": preferred,
                "max_chars": 8000,
                "timeout_seconds": settings.fetch_browser_timeout_seconds,
                "batch_timeout_seconds": min(120, settings.fetch_browser_timeout_seconds + 10),
            },
            settings_obj=settings,
        )
        page = result.output["pages"][0] if result.output and result.output.get("pages") else {}
        results.append(
            {
                "case": name,
                "success": result.success,
                "fetch_status": page.get("fetch_status"),
                "fetch_backend": page.get("fetch_backend"),
                "provider": page.get("provider"),
                "content_basis": page.get("content_basis"),
                "failure_code": page.get("error_code") or page.get("error"),
                "retrieval_attempts": page.get("retrieval_attempts") or [],
            }
        )
    ready = bool(results) and all(item["success"] for item in results)
    _print(
        {
            "stage": "r11_fetch_smoke",
            "ready": ready,
            "cases": results,
            "remote_case": "executed" if any(item["case"] == "remote_extract" for item in results) else "not_configured",
        }
    )
    return 0 if ready else 1


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

    if args.r11_fetch_smoke:
        return _r11_fetch_smoke(args)

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
