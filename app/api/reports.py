"""Report endpoint backed by generated Markdown files."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agent.report_exporter import (
    export_report,
    read_report_markdown,
    report_filename,
    report_media_type,
    resolve_report_path,
)
from app.database import get_db
from app.schemas import ReportResponse
from app.security import require_api_key, require_request_context
from app.trace import store

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)

ROOT = Path(__file__).resolve().parents[2]


def _resolve_existing_report(run_id: str, report_path_value: str | None) -> tuple[Path, str]:
    if not report_path_value:
        raise HTTPException(
            status_code=404,
            detail="Report has not been generated yet. Run POST /api/tasks/{run_id}/run first.",
        )
    try:
        report_path = resolve_report_path(report_path_value)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not report_path.exists() or not report_path.is_file():
        raise HTTPException(status_code=404, detail="Report path is recorded but the file is missing.")
    return report_path, read_report_markdown(report_path_value)


@router.get("/{run_id}", response_model=ReportResponse)
async def get_report(
    run_id: str,
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Return a generated Markdown report when it exists."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")

    if run.report_path:
        report_path = Path(run.report_path)
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        if report_path.exists() and report_path.is_file():
            return ReportResponse(
                run_id=run_id,
                markdown=report_path.read_text(encoding="utf-8"),
                report_path=run.report_path,
                exists=True,
                message=None,
            )

    message = "Report has not been generated yet. Run POST /api/tasks/{run_id}/run first."
    return ReportResponse(
        run_id=run_id,
        markdown=message,
        report_path=None,
        exists=False,
        message=message,
    )


@router.get("/{run_id}/download")
async def download_report(
    run_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|md|docx|pdf)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Download the generated report as Markdown, Word, or PDF."""

    run = store.get_agent_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    _report_path, markdown = _resolve_existing_report(run_id, run.report_path)
    try:
        result = export_report(run_id, markdown, format)
        export_path = resolve_report_path(result.report_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path=export_path,
        media_type=report_media_type(result.format),
        filename=report_filename(run_id, result.format),
    )
