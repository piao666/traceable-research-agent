"""Mock report endpoint for the Day 1-3 skeleton."""

from fastapi import APIRouter

from app.schemas import ReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{run_id}", response_model=ReportResponse)
async def get_report(run_id: str) -> ReportResponse:
    """Return mock Markdown until report_writer is implemented."""

    markdown = (
        "# Mock Traceable Research Report\n\n"
        f"- Run ID: `{run_id}`\n"
        "- Status: placeholder\n\n"
        "The real report_writer tool will be implemented in a later phase."
    )
    return ReportResponse(
        run_id=run_id,
        markdown=markdown,
        report_path=None,
        message="Mock report endpoint. No report file is generated in Day 1-3.",
    )
