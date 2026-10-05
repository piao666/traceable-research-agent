"""Report endpoint backed by generated Markdown files."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agent.report_exporter import (
    export_report,
    normalize_report_format,
    read_report_markdown,
    report_filename,
    report_media_type,
    resolve_report_path,
)
from app.database import get_db
from app.agent.outcome import report_block_reason, result_integrity
from app.schemas import ReportResponse
from app.security import require_api_key
from app.trace import store

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(require_api_key)],
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

    blocker = report_block_reason(run)
    if blocker:
        return ReportResponse(**result_integrity(run), run_id=run_id, markdown="",
                              report_path=None, exists=False, availability="blocked", message=blocker)

    if run.report_path:
        try:
            report_path = resolve_report_path(run.report_path)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Recorded report path is outside the allowed workspace.") from exc
        if report_path.exists() and report_path.is_file():
            return ReportResponse(
                **result_integrity(run),
                run_id=run_id,
                markdown=report_path.read_text(encoding="utf-8"),
                report_path=run.report_path,
                exists=True,
                availability="partial" if run.status == "incomplete" else "available",
                message=("Report contains partial results; research requirements were not fully established."
                         if run.status == "incomplete" else None),
            )

    missing = bool(run.report_path)
    message = "Report path is recorded but the file is missing." if missing else "Report has not been generated yet."
    return ReportResponse(
        **result_integrity(run),
        run_id=run_id,
        markdown="",
        report_path=None,
        exists=False,
        availability="missing" if missing else "not_generated",
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
    blocker = report_block_reason(run)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    report_path, markdown = _resolve_existing_report(run_id, run.report_path)
    normalized_format = normalize_report_format(format)
    # The adopted Markdown file is bound to the final report revision and
    # terminal SHA256.  Re-exporting it to the same canonical `.md` path on
    # Windows rewrites LF bytes as CRLF, invalidating that identity merely by
    # downloading it.  Serve those exact persisted bytes directly instead.
    if normalized_format == "markdown":
        return FileResponse(
            path=report_path,
            media_type=report_media_type(normalized_format),
            filename=report_filename(run_id, normalized_format),
        )
    try:
        result = export_report(run_id, markdown, normalized_format)
        export_path = resolve_report_path(result.report_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path=export_path,
        media_type=report_media_type(result.format),
        filename=report_filename(run_id, result.format),
    )
