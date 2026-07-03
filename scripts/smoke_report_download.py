"""Smoke checks for Markdown, Word, and PDF report downloads."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.executor import run_plan
from app.agent.planner import plan_task
from app.agent.report_exporter import _clean_inline_markdown, _pdf_inline_markup
from app.database import SessionLocal, init_db
from app.main import app
from app.tools.defaults import register_default_tools
from app.trace import store


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _make_completed_run() -> str:
    init_db()
    register_default_tools()
    with SessionLocal() as db:
        task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
        run = store.create_agent_run(
            db=db,
            task=task,
            report_type="summary",
            source_mode="mock",
            allowed_tools=["file_reader", "sql_query", "rag_search", "report_writer"],
        )
        plan = plan_task(
            task,
            ["file_reader", "sql_query", "rag_search", "report_writer"],
            "mock",
            planner_mode="deterministic",
        )
        run = store.update_agent_run_plan(db, run.run_id, plan)
        summary = run_plan(db, run.run_id)
        _assert(summary["status"] == "completed", f"run did not complete: {summary}")
        return run.run_id


def main() -> None:
    long_encoded_url = (
        "https://www.facebook.com/example/posts/"
        "%E7%82%BA%E4%BB%80%E9%BA%BC%E8%A6%81%E5%AD%B8%E7%BF%92comfyui"
    )
    markdown_link = f"[示例来源]({long_encoded_url})"
    _assert(
        _clean_inline_markdown(markdown_link) == "示例来源",
        "visible DOCX/PDF link text should not include the full encoded URL",
    )
    _assert(
        "facebook.com/...</a>" in _pdf_inline_markup(long_encoded_url),
        "PDF bare long URLs should render as short visible domains",
    )
    _assert(
        f'href="{long_encoded_url}"' in _pdf_inline_markup(markdown_link),
        "PDF markdown links should keep the original URL as hyperlink target",
    )

    run_id = _make_completed_run()
    with TestClient(app) as client:
        expected = {
            "markdown": ("text/markdown", b"#", ROOT / "workspace" / "reports" / f"{run_id}.md"),
            "docx": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                b"PK",
                ROOT / "workspace" / "reports" / f"{run_id}.docx",
            ),
            "pdf": ("application/pdf", b"%PDF", ROOT / "workspace" / "reports" / f"{run_id}.pdf"),
        }
        sizes: dict[str, int] = {}
        for report_format, (content_type, magic, path) in expected.items():
            response = client.get(f"/api/reports/{run_id}/download?format={report_format}")
            _assert(response.status_code == 200, response.text)
            _assert(
                response.headers.get("content-type", "").startswith(content_type),
                f"bad content-type for {report_format}: {response.headers.get('content-type')}",
            )
            _assert(response.content.startswith(magic), f"bad file header for {report_format}")
            _assert(path.exists() and path.is_file(), f"download file missing: {path}")
            sizes[report_format] = len(response.content)

        missing = client.get("/api/reports/missing-run-id/download?format=pdf")
        _assert(missing.status_code == 404, f"missing run should be 404: {missing.text}")

    print(
        json.dumps(
            {"report_download": "ok", "run_id": run_id, "sizes": sizes},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
