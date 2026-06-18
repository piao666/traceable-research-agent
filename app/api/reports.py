"""Report endpoint backed by generated Markdown files."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
